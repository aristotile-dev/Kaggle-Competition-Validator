"""
Export the already-authenticated Kaggle session (from your local
chrome-profile) into a portable storage_state.json file.

This file contains just the cookies/login state as plain JSON - unlike
the raw Chrome profile folder, it's NOT tied to Windows-specific
encryption, so it can be uploaded to and used on a Linux server (AWS EC2)
without needing to redo the Google-login-bypass trick there.

Run this ONCE now, and again whenever the exported session eventually
expires (Kaggle sessions don't last forever - if EC2 scrapers start
failing to find data weeks from now, re-run this and re-upload).

Run from the project root:
    python auth/export_session_for_cloud.py
"""

import sys
import os
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

OUTPUT_FILE = "kaggle_session.json"


def main():
    if not os.path.exists(config.CHROME_USER_DATA_DIR):
        print(f"ERROR: {config.CHROME_USER_DATA_DIR} doesn't exist.")
        print("You need the authenticated Chrome profile set up first (see README Step 1).")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=config.CHROME_USER_DATA_DIR,
            channel="chrome",
            headless=False,
        )
        page = context.new_page()

        # quick sanity check that we're actually still logged in before exporting
        page.goto("https://www.kaggle.com")
        page.wait_for_timeout(2000)
        if "login" in page.url or page.query_selector("text=Sign In"):
            print("WARNING: doesn't look logged in right now. Log in first, then re-run this.")
            context.close()
            return

        context.storage_state(path=OUTPUT_FILE)
        print(f"Session exported to {OUTPUT_FILE}")
        print("Upload this file to your AWS EC2 instance's project folder.")
        print("NOTE: this file grants access to the NIAT Kaggle account - keep it as private")
        print("as a password, don't commit it to any public repo or share it.")

        context.close()


if __name__ == "__main__":
    main()
