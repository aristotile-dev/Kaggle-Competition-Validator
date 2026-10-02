"""
Sends personalized invalidation-reason emails to students whose
submissions were ACTUALLY invalidated the previous night - not just
"currently Invalid" in general, specifically the ones you tapped
"Invalidate" for in Telegram.

SAFETY DESIGN - this is the important part:
  - Reads invalidation_actions_<date>.csv, which ONLY exists if you
    actually tapped the confirm button and invalidate_submissions.py
    ran with --apply. If you never tap it on a given night, this file
    is never created for that date, so this script simply finds
    nothing to send and exits quietly - no risk of emailing anyone
    about something that didn't actually happen.
  - Only rows with status == "invalidated" are used (skips any that
    errored or were dry-run, so an email is never sent claiming
    something happened when it didn't).
  - One email per TEAM (grouped), even if they had multiple invalid
    submissions that night - never spams per submission ID.
  - Reuses the EXACT SAME reason-to-message logic as Email Master
    (imported directly, not duplicated) - what the sheet says and what
    the email says are always guaranteed to match.

Meant to run the MORNING after a nightly run (e.g. 9 AM), as its own
SEPARATE cron entry - NOT part of run_daily.py's chain, since the whole
point is a delay between "invalidated last night" and "emailed this
morning."

Requires EMAIL_ADDRESS, EMAIL_APP_PASSWORD, and EMAIL_SMTP_SERVER in .env.
For Gmail: smtp.gmail.com, port 587, needs a Gmail App Password
(myaccount.google.com/apppasswords, needs 2-Step Verification first).
For Microsoft 365/Outlook work accounts: smtp.office365.com, port 587 -
NOTE: many organizations disable "SMTP AUTH" by default for security,
even when the email account itself works fine in the Outlook app. If
this fails with an auth error mentioning "SmtpClientAuthentication" or
similar, that's your org's IT policy blocking it, not a bug here - would
need an IT admin to enable SMTP AUTH for this specific mailbox, or a
different sending account entirely.

Run from the project root:
    python notify/email_students.py                  (dry-run, default - prints what WOULD be sent)
    python notify/email_students.py --apply           (actually sends)
    python notify/email_students.py --date 2026-09-15 --apply   (specific date)
"""

import os
import sys
import smtplib
import argparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

import pandas as pd
import gspread
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from output.sheets_logger import (
    reason_to_student_message_specific,
    load_scores_email_lookup,
    connect,
    get_or_create_worksheet_custom,
    SHEET_ID,
)

load_dotenv()

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
EMAIL_SMTP_SERVER = os.environ.get("EMAIL_SMTP_SERVER", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

SUBJECT = "Update on your <Competition Name> submission"
EMAIL_TRACKER_COLUMNS = ["Date", "Team", "Email", "Status", "Detail"]


def log_email_tracker(date_str, rows):
    """Internal-only tab, same file as Email Master. One row per team per
    run - dry-runs are logged too (status 'Would Send (Dry Run)'), so you
    can verify this tab works and review drafts without sending anything
    for real; a genuine send shows status 'Sent' or 'Failed'.

    Uses EXPLICIT cell ranges (ws.update with a computed A{row}:E{row}
    range) rather than append_row/append_rows. This matters: Sheets'
    own "append" API guesses where the table is based on existing data,
    and that guess goes wrong when earlier rows have blank cells (e.g.
    a 'Skipped - No Email' row has an empty Email column) - it was
    silently shifting later banners/rows several columns to the right.
    Computing the exact target row ourselves removes that ambiguity
    entirely - alignment can never drift, regardless of what blank
    cells exist earlier in the sheet."""
    client = connect()
    spreadsheet = client.open_by_key(SHEET_ID)
    ws = get_or_create_worksheet_custom(spreadsheet, "Email Tracker", EMAIL_TRACKER_COLUMNS)

    n_cols = len(EMAIL_TRACKER_COLUMNS)
    last_col_letter = gspread.utils.rowcol_to_a1(1, n_cols).rstrip("1")

    existing = ws.get_all_values()
    start_row = len(existing) + 1

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = f"\u25b6 {timestamp} \u2014 {len(rows)} team(s)"
    banner_row = [label] + [""] * (n_cols - 1)

    all_new_rows = [banner_row] + rows
    end_row = start_row + len(all_new_rows) - 1

    ws.update(values=all_new_rows, range_name=f"A{start_row}:{last_col_letter}{end_row}", value_input_option="USER_ENTERED")

    ws.format(
        f"A{start_row}:{last_col_letter}{start_row}",
        {
            "backgroundColor": {"red": 0.85, "green": 0.90, "blue": 1.0},
            "textFormat": {"bold": True, "fontSize": 11},
            "horizontalAlignment": "LEFT",
        },
    )
    print(f"Appended {len(rows)} rows to 'Email Tracker' at row {start_row} (explicit range - alignment guaranteed).")


def build_email_body(first_name, reasons):
    reason_lines = "\n\n".join(f"- {r}" for r in reasons)
    return f"""Hi {first_name},

Your submission to the <Competition Name> was invalidated overnight. Here's specifically why:

{reason_lines}

Please address this and resubmit when ready - you're welcome to reach out to the masterclass team if anything here is unclear.

Best regards,
NIAT Masterclass Team
"""


def load_invalidated_teams(date_str):
    """Returns {team: [raw_reason, ...]} for teams with at least one row
    that was genuinely invalidated (status == 'invalidated') on this
    date AND whose OVERALL reconciliation decision is currently Invalid.

    That second condition matters: a team can have an old, invalidated
    submission cleaned up while their CURRENT best submission is
    completely fine (decision == 'Valid' overall) - e.g. an early raw-CSV
    attempt before they switched to proper notebook submission. Emailing
    those teams an "action needed" message would be misleading and
    needlessly alarming, since nothing is actually wrong with their
    standing. Only teams who are genuinely Invalid right now get emailed.

    Empty dict if the file doesn't exist - meaning you never tapped the
    confirm button that night, nothing to send."""
    path = os.path.join(config.DATA_DIR, f"invalidation_actions_{date_str}.csv")
    if not os.path.exists(path):
        print(f"No {path} found - either nothing was invalidated, or you never tapped confirm. Nothing to send.")
        return {}

    df = pd.read_csv(path, dtype=str).fillna("")
    invalidated = df[df["status"] == "invalidated"]
    if invalidated.empty:
        print("File exists but no rows have status 'invalidated' - nothing to send.")
        return {}

    team_results_path = os.path.join(config.DATA_DIR, f"reconciliation_teams_{date_str}.csv")
    if os.path.exists(team_results_path):
        team_results = pd.read_csv(team_results_path)
        currently_invalid_teams = set(team_results[team_results["decision"] == "Invalid"]["team"])
        skipped = set(invalidated["team"]) - currently_invalid_teams
        if skipped:
            print(f"Skipping {len(skipped)} team(s) whose current best is actually fine (only an old submission was cleaned up): {', '.join(skipped)}")
        invalidated = invalidated[invalidated["team"].isin(currently_invalid_teams)]
    else:
        print(f"WARNING: {team_results_path} not found - can't cross-check current status, proceeding with all invalidated rows.")

    if invalidated.empty:
        print("No teams are BOTH invalidated tonight AND currently Invalid overall - nothing to send.")
        return {}

    grouped = {}
    for team, group in invalidated.groupby("team"):
        grouped[team] = list(dict.fromkeys(group["reason"].tolist()))  # distinct, order preserved
    return grouped


def send_email(to_address, subject, body):
    msg = MIMEMultipart()
    msg["From"] = f"NIAT Masterclass Team <{EMAIL_ADDRESS}>"
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        default=None,
        help="Which night's invalidations to email about (YYYY-MM-DD). "
        "Defaults to YESTERDAY, since this is meant to run the morning "
        "after a night's run_daily.py + your Telegram tap.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually send emails (default is dry-run)")
    parser.add_argument(
        "--test-email",
        default=None,
        help="Send ONE sample email straight to this address, bypassing all "
        "invalidation lookup - useful for checking spam-folder placement "
        "before trusting the real thing. Always actually sends (no dry-run "
        "for this mode, since the whole point is to see real delivery).",
    )
    args = parser.parse_args()

    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        print("ERROR: EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set in .env")
        return

    if args.test_email:
        body = build_email_body(
            "there",
            ["This is a sample reason - just testing delivery and spam placement, nothing real."],
        )
        print(f"Sending test email to {args.test_email} ...")
        try:
            send_email(args.test_email, "[TEST] " + SUBJECT, body)
            print(f"Sent. Check {args.test_email}'s inbox AND spam folder.")
        except Exception as e:
            print(f"ERROR sending test email: {e}")
        return

    date_str = args.date if args.date else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    invalidated_teams = load_invalidated_teams(date_str)
    if not invalidated_teams:
        return

    email_lookup = load_scores_email_lookup(date_str)

    print(f"Found {len(invalidated_teams)} team(s) actually invalidated on {date_str}.")
    print(f"Mode: {'SEND' if args.apply else 'DRY RUN'}\n")

    tracker_rows = []

    for team, raw_reasons in invalidated_teams.items():
        email = email_lookup.get(team, "")
        first_name = team.split()[0] if team.split() else team
        messages = [reason_to_student_message_specific(r) for r in raw_reasons]
        distinct_messages = list(dict.fromkeys(messages))
        body = build_email_body(first_name, distinct_messages)

        if not email:
            print(f"SKIP {team}: no email on file (private profile or never scored).")
            tracker_rows.append([date_str, team, "", "Skipped - No Email", "Private profile or no scored submission on file"])
            continue

        print(f"{'SENDING' if args.apply else 'WOULD SEND'} to {team} <{email}>:")
        print(body)
        print("-" * 60)

        if args.apply:
            try:
                send_email(email, SUBJECT, body)
                print(f"  -> Sent to {email}")
                tracker_rows.append([date_str, team, email, "Sent", ""])
            except Exception as e:
                print(f"  -> ERROR sending to {email}: {e}")
                tracker_rows.append([date_str, team, email, "Failed", str(e)])
        else:
            tracker_rows.append([date_str, team, email, "Would Send (Dry Run)", "Not actually sent - re-run with --apply to send for real"])

    if tracker_rows:
        try:
            log_email_tracker(date_str, tracker_rows)
        except Exception as e:
            print(f"WARNING: could not write to Email Tracker sheet: {e}")


if __name__ == "__main__":
    main()
