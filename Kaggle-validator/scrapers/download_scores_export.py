"""
Automates Kaggle's "Download All Scores" export (Settings > Teams &
Submissions > Download All Scores) - no more manual click-download-paste
needed. Captures the file via Playwright's download API. Handles both
possible outcomes: a direct CSV, or a zip containing the CSV (confirmed
both happen depending on the competition/moment).

This export is what feeds the Email column in the internal "Email Master"
sheet tab (see output/sheets_logger.py) - it's the only place Kaggle
exposes student emails, and only for teams with at least one SCORED
submission (teams that never scored anything won't appear in it, no
matter how many times this runs - a real limitation, not a bug here).

Run from the project root:
    python scrapers/download_scores_export.py
"""

import sys
import os
import argparse
from datetime import datetime
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from auth.browser_context import get_browser_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Pin the output date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    date_str = args.date if args.date else datetime.now().strftime("%Y-%m-%d")

    if not os.path.exists(config.SESSION_FILE) and not os.path.exists(config.CHROME_USER_DATA_DIR):
        print("ERROR: No auth method found (kaggle_session.json or chrome-profile).")
        return

    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()

        print(f"Navigating to {config.TEAMS_SUBMISSIONS_URL}")
        page.goto(config.TEAMS_SUBMISSIONS_URL)
        page.wait_for_timeout(2000)

        os.makedirs(config.DATA_DIR, exist_ok=True)
        out_path = os.path.join(config.DATA_DIR, f"all_scores_{date_str}.csv")

        try:
            with page.expect_download(timeout=30000) as download_info:
                page.click('button:has-text("Download All Scores")')
            download = download_info.value

            suggested_name = download.suggested_filename or ""
            if suggested_name.lower().endswith(".zip"):
                zip_path = os.path.join(config.DATA_DIR, f"all_scores_{date_str}.zip")
                download.save_as(zip_path)
                import zipfile
                with zipfile.ZipFile(zip_path, "r") as zf:
                    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    if not csv_names:
                        print("ERROR: no CSV found inside the downloaded zip.")
                    else:
                        with zf.open(csv_names[0]) as src, open(out_path, "wb") as dst:
                            dst.write(src.read())
                        print(f"Extracted {csv_names[0]} from zip -> {out_path}")
                os.remove(zip_path)
            else:
                download.save_as(out_path)
                print(f"Saved scores export to {out_path}")
        except Exception as e:
            # Non-fatal on purpose - sheets_logger.py's Email Master tab
            # gracefully falls back to blank emails if this file is missing,
            # so a failure here shouldn't block the rest of the pipeline.
            print(f"WARNING: Could not download scores export: {e}")
            print("Email Master will have blank emails for this run - not a critical failure.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
