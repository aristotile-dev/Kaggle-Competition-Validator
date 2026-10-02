"""
Daily orchestrator - runs the full Kaggle Submission Validator pipeline:
4 scrapers + scores export -> reconciliation engine -> Sheets logger ->
Telegram notify.

Meant to be triggered once a day by cron (23:59).

CRITICAL: the date is computed ONCE right here, at the very start, and
passed explicitly to every step via --date. Without this, each script
would independently ask "what's today's date?" at whatever moment IT
happens to run - and since the full chain takes several minutes, a run
starting at 23:59 can easily cross midnight partway through, causing
different steps to save files under different dates. Pinning one shared
date_str for the entire run eliminates this class of bug entirely.

Logs full output (stdout + stderr) of every step to logs/run_<timestamp>.log,
so you can check each morning whether last night's run actually succeeded.

Each step still runs even if an earlier one fails - a single scraper
hiccup won't silently skip everything after it.

Run directly (for testing):
    python run_daily.py
"""

import subprocess
import sys
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

STEPS = [
    ("Leaderboard scraper", os.path.join("scrapers", "leaderboard_scraper.py")),
    ("Teams roster scraper", os.path.join("scrapers", "teams_roster_scraper.py")),
    ("Submissions scraper", os.path.join("scrapers", "submissions_scraper.py")),
    ("Shared notebooks scraper", os.path.join("scrapers", "shared_notebooks_scraper.py")),
    ("Scores export (for emails)", os.path.join("scrapers", "download_scores_export.py")),
    ("Reconciliation engine", os.path.join("engine", "reconciliation.py")),
    ("Sheets logger", os.path.join("output", "sheets_logger.py")),
    ("Telegram notify", os.path.join("notify", "telegram_notify.py")),
]


def main():
    run_date = datetime.now().strftime("%Y-%m-%d")  # pinned ONCE for this entire run
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(LOG_DIR, f"run_{timestamp}.log")

    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"===== Kaggle Validator daily run started {datetime.now()} (pinned date: {run_date}) =====\n")

        for name, script_path in STEPS:
            log.write(f"\n--- {name} ---\n")
            log.flush()
            full_path = os.path.join(PROJECT_ROOT, script_path)
            result = subprocess.run(
                [sys.executable, full_path, "--date", run_date],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            log.write(result.stdout)
            if result.returncode != 0:
                log.write(f"\n!!! {name} exited with error code {result.returncode} !!!\n")
                log.write(result.stderr)
            log.flush()

        log.write(f"\n===== Run finished {datetime.now()} =====\n")

    print(f"Done. Log saved to {log_path}")


if __name__ == "__main__":
    main()
