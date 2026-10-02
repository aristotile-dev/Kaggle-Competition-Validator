"""
Sends the nightly Telegram summary with a tap-to-confirm inline button.

Runs as a step of run_daily.py, after the Sheets logger.
  - If there are 0 ACTIONABLE invalid submissions: sends a plain "all
    clear" message, no button, nothing to confirm.
  - If there ARE actionable ones: sends a summary + an inline button.
    Tapping it is picked up by telegram_poller.py (long-polling, reacts
    within seconds - not on a fixed schedule), which triggers the actual
    invalidation.
  - Runs circuit-breaker checks: team count compared against YESTERDAY's
    real count (not a hardcoded number, since the roster naturally grows
    as students join) - a DROP is flagged as suspicious, growth just gets
    a quiet note.

IMPORTANT: "actionable" count is NOT the same as the team-level "Invalid"
decision count. A team can be "Invalid" simply because it has zero score
at all, with nothing left to actually click (already fully resolved).
Counting those toward "needs action today" would cry wolf constantly. The
real actionable number is however many submission IDs are still sitting
in invalid_submission_ids right now.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in a .env file in the
project root - never hardcode these directly.

Run from the project root:
    python notify/telegram_notify.py
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta

import requests
import pandas as pd
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PENDING_STATE_FILE = os.path.join(config.DATA_DIR, "pending_confirmation.json")

# Only used as a fallback on the very first-ever run, before there's a
# "yesterday" file to compare against. Adjust to roughly this
# competition's known scale to avoid a false alarm on day 1 - from day 2
# onward the real day-over-day comparison takes over automatically and
# this number stops mattering.
EXPECTED_TEAM_COUNT = 15


def send_message(text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    resp = requests.post(url, data=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def build_summary(date_str):
    team_results_path = os.path.join(config.DATA_DIR, f"reconciliation_teams_{date_str}.csv")
    if not os.path.exists(team_results_path):
        return None, 0, [], []

    df = pd.read_csv(team_results_path)
    valid_count = int((df["decision"] == "Valid").sum())
    invalid_count = int((df["decision"] == "Invalid").sum())
    today_team_count = len(df)

    warnings = []

    yesterday = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_path = os.path.join(config.DATA_DIR, f"reconciliation_teams_{yesterday}.csv")

    if os.path.exists(yesterday_path):
        yesterday_count = len(pd.read_csv(yesterday_path))
        if today_team_count < yesterday_count:
            warnings.append(
                f"\u26a0\ufe0f Team count DROPPED: {yesterday_count} \u2192 {today_team_count}. "
                f"Teams don't normally disappear - a scraper may have missed rows. Double check before confirming."
            )
        elif today_team_count > yesterday_count:
            warnings.append(f"\u2139\ufe0f Team count grew: {yesterday_count} \u2192 {today_team_count} (new joins, normal).")
    elif today_team_count < EXPECTED_TEAM_COUNT - 5:
        warnings.append(
            f"\u26a0\ufe0f Only {today_team_count} teams found (expected ~{EXPECTED_TEAM_COUNT}). "
            f"A scraper may have failed - double check before confirming."
        )

    if valid_count == 0 and invalid_count == 0:
        warnings.append("\u26a0\ufe0f No decisions at all today - pipeline likely failed.")

    all_ids = []
    actionable_teams = 0
    for ids_str in df["invalid_submission_ids"].fillna(""):
        ids = [x.strip() for x in str(ids_str).split(",") if x.strip()]
        if ids:
            actionable_teams += 1
            all_ids.extend(ids)
    actionable_count = len(all_ids)

    lines = [f"<b>Kaggle Validator \u2014 {date_str}</b>"]
    if warnings:
        lines.append("")
        lines.extend(warnings)
    lines.append("")
    lines.append(f"Valid: {valid_count} | Invalid (team decisions): {invalid_count}")
    lines.append(f"Actionable today: {actionable_count} submission(s) across {actionable_teams} team(s)")

    return "\n".join(lines), actionable_count, all_ids, warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Pin the date to use (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")
        return

    date_str = args.date if args.date else datetime.now().strftime("%Y-%m-%d")
    result = build_summary(date_str)

    if result[0] is None:
        send_message(
            f"\u26a0\ufe0f Kaggle Validator \u2014 {date_str}: reconciliation results not found. "
            f"Pipeline likely failed tonight."
        )
        print("No reconciliation data found - sent failure alert.")
        return

    summary_text, actionable_count, all_ids, warnings = result

    if actionable_count == 0:
        send_message(summary_text + "\n\nNothing to invalidate tonight. \u2705")
        print("Sent all-clear message, nothing pending.")
        return

    callback_data = f"confirm_invalidate_{date_str}"
    reply_markup = {
        "inline_keyboard": [[
            {"text": f"\u2705 Invalidate {actionable_count} submission(s)", "callback_data": callback_data}
        ]]
    }
    summary_text += "\n\nTap below to invalidate, or just ignore this to leave everything as-is."
    send_message(summary_text, reply_markup=reply_markup)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PENDING_STATE_FILE, "w") as f:
        json.dump({"date": date_str, "callback_data": callback_data, "status": "pending"}, f)

    print(f"Sent Telegram summary for {date_str} ({actionable_count} actionable) - awaiting your tap.")


if __name__ == "__main__":
    main()
