"""
PHASE 5: Log reconciliation results to Google Sheets.

TWO SEPARATE spreadsheet files, opened by ID (not name - more robust,
no risk of a typo or case mismatch in the title silently breaking things):
  1. Internal file (SHEET_ID) - "Daily Log", "Latest Snapshot", and
     "Email Master" tabs. Full technical detail: private scores, raw
     reasons, submission IDs, AND student emails (for your own outreach
     automation later). NEVER share this file's link with anyone but
     yourself.
  2. Student-facing file (STUDENT_SHEET_ID) - just "Student Notice".
     Appended every run with day-separator banners (kept as history on
     purpose - needed for resolving disputes about what was communicated
     on a given day). No scores, no submission IDs, no emails, no raw
     internal reasons - just a plain Valid / Action needed status and
     guidance.

"Email Master" (internal tab) mirrors Student Notice's Status/Reason
columns exactly, plus a real Email column pulled from the automated
Download All Scores export (scrapers/download_scores_export.py) - this is
the internal staging table meant to power future automated per-student
email sending, without ever exposing emails to the whole class.

SHEET_ID and STUDENT_SHEET_ID below are placeholders - set them to your
two Sheets' actual IDs (grab each from its URL:
docs.google.com/spreadsheets/d/THIS_PART/edit).

Setup needed (one-time):
  1. Google Cloud service account JSON key saved as service_account.json
     in the project root.
  2. Create TWO separate Google Sheets files (internal + student-facing).
  3. Share BOTH with your service account's email with Editor access.
  4. Share only the student-facing file's link with students.

Run from the project root:
    python output/sheets_logger.py
"""

import sys
import os
import argparse
from datetime import datetime

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SERVICE_ACCOUNT_FILE = "service_account.json"
SHEET_ID = "PASTE_YOUR_INTERNAL_SHEET_ID_HERE"  # internal only - private scores + emails live here
STUDENT_SHEET_ID = "PASTE_YOUR_STUDENT_SHEET_ID_HERE"  # safe to share with students

COLUMNS = [
    "Date",
    "Team",
    "Username",
    "Public Score",
    "Private Score",
    "Decision",
    "Reason",
    "Effective Valid Score",
    "Effective Valid Private Score",
    "Invalid Submission IDs",
]

STUDENT_COLUMNS = ["Date", "Team", "Status", "What to do"]
EMAIL_MASTER_COLUMNS = ["Date", "Team", "Status", "Reason", "Email"]


def connect():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client


def get_or_create_worksheet(spreadsheet, title):
    try:
        return spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=26)
        ws.append_row(COLUMNS)
        return ws


def get_or_create_worksheet_custom(spreadsheet, title, columns):
    try:
        return spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=26)
        ws.append_row(columns)
        return ws


def safe(val):
    """Convert any pandas NaN (which sneaks in after a CSV round-trip, even
    for originally-empty strings) into a plain empty string. Google's API
    rejects raw NaN as invalid JSON, so every field needs this."""
    if pd.isna(val):
        return ""
    return val


def team_results_to_rows(team_results_df, date_str):
    rows = []
    for _, r in team_results_df.iterrows():
        rows.append(
            [
                date_str,
                safe(r["team"]),
                safe(r["username"]),
                safe(r["current_public_score"]),
                safe(r["current_private_score"]),
                safe(r["decision"]),
                safe(r["reason"]),
                safe(r["effective_valid_score"]),
                safe(r["effective_valid_private_score"]),
                safe(r["invalid_submission_ids"]),
            ]
        )
    return rows


def add_day_separator(worksheet, date_str, n_teams, n_cols):
    """Insert a bold, colored banner row before a day's batch of rows, so
    scrolling through weeks of history stays easy to navigate. Returns
    True if today was already logged (caller should skip appending the
    data rows too, not just the banner)."""
    existing = worksheet.get_all_values()

    for row in reversed(existing):
        if row and row[0].startswith("\u25b6"):
            if date_str in row[0]:
                return True  # today's already logged
            break  # most recent banner is from an earlier day - proceed

    next_row = len(existing) + 1
    label = f"\u25b6 {date_str} \u2014 {n_teams} teams checked"

    worksheet.append_row([label] + [""] * (n_cols - 1))

    last_col_letter = gspread.utils.rowcol_to_a1(1, n_cols).rstrip("1")
    banner_range = f"A{next_row}:{last_col_letter}{next_row}"

    # Intentionally NOT merging cells - a merge makes Ctrl+Space column-select
    # grab the whole banner block too. Coloring without merging gives the
    # same solid-bar look while keeping every cell independent.
    worksheet.format(
        banner_range,
        {
            "backgroundColor": {"red": 0.85, "green": 0.90, "blue": 1.0},
            "textFormat": {"bold": True, "fontSize": 11},
            "horizontalAlignment": "LEFT",
        },
    )
    return False


def reason_to_student_message_specific(reason):
    """Maps an ALREADY-SPECIFIC individual submission reason (from the
    per-submission audit, not the blended team-level summary) to accurate
    student-facing guidance."""
    reason_lower = reason.lower()
    if "raw csv" in reason_lower:
        return "Please resubmit via Kaggle Notebook (Submit from Notebook), not a direct CSV upload."
    if "not shared" in reason_lower:
        return "You submitted via notebook, but it hasn't been shared with us yet - that's why it was invalidated. Please share your notebook so it can be verified."
    if "no username mapping" in reason_lower:
        return "We couldn't verify your submission - please contact the course team."
    return "Please check your submission and contact the course team if you're unsure why."


def reason_to_student_message(decision, reason):
    """Fallback for teams with NO submission-level audit rows at all
    (e.g. never had any scored submission)."""
    if decision == "Valid":
        return "Valid", "You're all set. Your submission meets requirements."

    reason_lower = reason.lower()
    if "no scored submission" in reason_lower:
        return "Action needed", "No valid scored submission found yet. Please submit and make sure it scores."

    return "Action needed", "Please check your submission and contact the course team if you're unsure why."


def compute_student_facing_rows(team_results_df, audit_df):
    """Returns [(team, status, message), ...] using SPECIFIC per-submission
    reasons where available (from the audit), falling back to the team-level
    reason only for teams with no scored submission at all."""
    if "already_invalidated" in audit_df.columns:
        actionable = audit_df[(audit_df["valid"] == False) & (~audit_df["already_invalidated"])]
    else:
        actionable = audit_df[audit_df["valid"] == False] if "valid" in audit_df.columns else audit_df.iloc[0:0]

    team_specific_reasons = actionable.groupby("team")["reason"].apply(
        lambda s: list(dict.fromkeys(s))
    ) if len(actionable) else pd.Series(dtype=object)

    results = []
    for _, r in team_results_df.iterrows():
        team = r["team"]
        if r["decision"] == "Valid":
            status, message = "Valid", "You're all set. Your submission meets requirements."
        elif team in team_specific_reasons.index and team_specific_reasons[team]:
            specific_reasons = team_specific_reasons[team]
            messages = [reason_to_student_message_specific(reason) for reason in specific_reasons]
            status = "Action needed"
            message = " ".join(dict.fromkeys(messages))
        else:
            status, message = reason_to_student_message(r["decision"], r["reason"])
        results.append((team, status, message))
    return results


def log_student_notice(team_results_df, audit_df, date_str):
    client = connect()
    spreadsheet = client.open_by_key(STUDENT_SHEET_ID)  # SEPARATE file - never the internal one

    rows = [
        [date_str, team, status, message]
        for team, status, message in compute_student_facing_rows(team_results_df, audit_df)
    ]

    ws = get_or_create_worksheet_custom(spreadsheet, "Student Notice", STUDENT_COLUMNS)
    already_logged = add_day_separator(ws, date_str, len(rows), len(STUDENT_COLUMNS))
    if already_logged:
        print(f"'{date_str}' already logged in 'Student Notice' - skipping to avoid duplicates.")
        return
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Appended {len(rows)} rows to 'Student Notice' (with day separator).")


def load_scores_email_lookup(date_str):
    """Loads Team -> Email from the automated Download All Scores export
    for this date, if it exists. Returns {} if the export wasn't run or
    hasn't produced a file yet - callers should handle blank emails
    gracefully, not treat this as fatal."""
    export_path = os.path.join(config.DATA_DIR, f"all_scores_{date_str}.csv")
    if not os.path.exists(export_path):
        print(f"NOTE: {export_path} not found - Email Master will have blank emails this run.")
        return {}
    try:
        df = pd.read_csv(export_path, sep=None, engine="python")
    except Exception as e:
        print(f"WARNING: could not read {export_path}: {e}")
        return {}
    if "TeamName" not in df.columns or "UserEmail" not in df.columns:
        print(f"WARNING: {export_path} missing expected columns.")
        return {}
    lookup = df.drop_duplicates(subset="TeamName", keep="last").set_index("TeamName")["UserEmail"].to_dict()
    return {k: ("" if v == "[private]" else v) for k, v in lookup.items()}


def log_email_master(team_results_df, audit_df, date_str):
    """Internal-only tab, same file as Daily Log. Same Status/Reason as
    Student Notice, PLUS a real Email column - never shared with students,
    exists purely to stage data for future automated email sending."""
    client = connect()
    spreadsheet = client.open_by_key(SHEET_ID)  # the INTERNAL file

    email_lookup = load_scores_email_lookup(date_str)

    rows = []
    for team, status, message in compute_student_facing_rows(team_results_df, audit_df):
        rows.append([date_str, team, status, message, email_lookup.get(team, "")])

    ws = get_or_create_worksheet_custom(spreadsheet, "Email Master", EMAIL_MASTER_COLUMNS)
    already_logged = add_day_separator(ws, date_str, len(rows), len(EMAIL_MASTER_COLUMNS))
    if already_logged:
        print(f"'{date_str}' already logged in 'Email Master' - skipping to avoid duplicates.")
        return
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Appended {len(rows)} rows to 'Email Master' (with day separator).")


def log_to_sheets(team_results_df, date_str):
    client = connect()
    spreadsheet = client.open_by_key(SHEET_ID)

    rows = team_results_to_rows(team_results_df, date_str)

    daily_log = get_or_create_worksheet(spreadsheet, "Daily Log")
    already_logged = add_day_separator(daily_log, date_str, len(rows), len(COLUMNS))
    if already_logged:
        print(f"'{date_str}' already logged in 'Daily Log' - skipping to avoid duplicates.")
    else:
        daily_log.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"Appended {len(rows)} rows to 'Daily Log' (with day separator).")

    latest = get_or_create_worksheet(spreadsheet, "Latest Snapshot")
    latest.clear()
    latest.append_row(COLUMNS)
    latest.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Rewrote 'Latest Snapshot' with {len(rows)} current rows.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Pin the date to use (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"ERROR: {SERVICE_ACCOUNT_FILE} not found in project root.")
        return

    date_str = args.date if args.date else datetime.now().strftime("%Y-%m-%d")
    team_results_path = os.path.join(config.DATA_DIR, f"reconciliation_teams_{date_str}.csv")
    audit_path = os.path.join(config.DATA_DIR, f"reconciliation_audit_{date_str}.csv")

    if not os.path.exists(team_results_path):
        print(f"ERROR: {team_results_path} not found.")
        print("Run engine/reconciliation.py first to generate today's results.")
        return

    team_results_df = pd.read_csv(team_results_path)
    audit_df = pd.read_csv(audit_path) if os.path.exists(audit_path) else pd.DataFrame(columns=["team", "valid", "reason"])

    try:
        log_to_sheets(team_results_df, date_str)
    except gspread.exceptions.APIError as e:
        print(f"ERROR: Could not open internal sheet (ID: {SHEET_ID}): {e}")
        print("Make sure it exists and is shared with your service account's email.")

    try:
        log_student_notice(team_results_df, audit_df, date_str)
    except gspread.exceptions.APIError as e:
        print(f"ERROR: Could not open student sheet (ID: {STUDENT_SHEET_ID}): {e}")
        print("Make sure it exists and is shared with your service account's email.")

    try:
        log_email_master(team_results_df, audit_df, date_str)
    except gspread.exceptions.APIError as e:
        print(f"ERROR: Could not open internal sheet (ID: {SHEET_ID}): {e}")
        return

    print("\nDone. Check your Google Sheets.")


if __name__ == "__main__":
    main()
