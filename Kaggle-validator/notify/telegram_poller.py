"""
Continuously listens for a tapped confirmation button and triggers
invalidation the moment it's found - no cron, no delay.

Uses Telegram's LONG-polling: each request to Telegram holds the
connection open for up to 30 seconds, waiting for something new to
happen, and returns immediately the moment it does. This means a tap is
noticed within seconds, not on some fixed schedule.

This process is meant to run FOREVER in the background as a systemd
service (see README) - not launched repeatedly by cron. It re-reads the
pending-confirmation file fresh on every tap it sees, so a brand new
night's run_daily.py can write a new pending state while this is already
running, and it'll pick it up correctly without needing a restart.

Run from the project root (for testing - Ctrl+C to stop):
    python notify/telegram_poller.py

For real unattended use, run it as a systemd service instead (see README).
"""

import os
import sys
import json
import time
import subprocess

import requests
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
import config

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PENDING_STATE_FILE = os.path.join(config.DATA_DIR, "pending_confirmation.json")
OFFSET_FILE = os.path.join(config.DATA_DIR, "telegram_offset.json")

LONG_POLL_SECONDS = 30  # how long Telegram holds the connection open per request


def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": LONG_POLL_SECONDS}
    if offset is not None:
        params["offset"] = offset
    # client-side timeout must exceed Telegram's own long-poll timeout,
    # otherwise WE time out before Telegram's server responds
    resp = requests.get(url, params=params, timeout=LONG_POLL_SECONDS + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def answer_callback(callback_query_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    requests.post(url, data={"callback_query_id": callback_query_id, "text": text}, timeout=15)


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=15)


def load_offset():
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset")
    return None


def save_offset(offset):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


def load_pending():
    if not os.path.exists(PENDING_STATE_FILE):
        return None
    with open(PENDING_STATE_FILE) as f:
        return json.load(f)


def handle_confirmed_tap(pending):
    send_message(f"\u2705 Confirmed. Running invalidation for {pending['date']}...")

    result = subprocess.run(
        [
            sys.executable,
            os.path.join(PROJECT_ROOT, "actions", "invalidate_submissions.py"),
            "--date", pending["date"],
            "--apply",
            "--no-prompt",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    pending["status"] = "confirmed"
    with open(PENDING_STATE_FILE, "w") as f:
        json.dump(pending, f)

    tail = result.stdout.strip().split("\n")[-1] if result.stdout else "Done (no output captured)."
    if result.returncode != 0:
        send_message(f"\u26a0\ufe0f Invalidation for {pending['date']} exited with an error.\n{tail}")
    else:
        send_message(f"Invalidation finished for {pending['date']}.\n{tail}")

    print(f"[{pending['date']}] Invalidation triggered and completed.")
    return "Confirmed! Invalidating now..."


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")
        return

    offset = load_offset()
    print("Poller started - listening for taps in real time (long-polling). Ctrl+C to stop.")

    while True:
        try:
            updates = get_updates(offset=offset)
        except requests.exceptions.RequestException as e:
            print(f"Network error, retrying in 5s: {e}")
            time.sleep(5)
            continue
        except KeyboardInterrupt:
            print("\nStopped.")
            return

        for update in updates:
            offset = update["update_id"] + 1
            save_offset(offset)

            callback = update.get("callback_query")
            if not callback:
                continue
            if str(callback.get("from", {}).get("id")) != str(CHAT_ID):
                continue

            # re-read fresh every time - a new night's run may have written
            # a new pending state while this loop was already running
            pending = load_pending()
            if not pending or pending.get("status") != "pending":
                continue

            if callback.get("data") == pending["callback_data"]:
                answer_text = handle_confirmed_tap(pending)
                answer_callback(callback["id"], answer_text)


if __name__ == "__main__":
    main()
