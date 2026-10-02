"""
PHASE 1 - STEP 1 (revised): Verify the persistent Chrome profile is logged in.

This does NOT log in for you - you already did that manually in a real
Chrome window (see README). This just confirms Playwright can reuse that
session successfully before we run the actual scrapers.

Run from the project root:
    python auth/verify_session.py

IMPORTANT: Close ALL Chrome windows using the chrome-profile folder before
running this - Playwright can't open a profile that's already in use.
"""

import sys
import os
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def main():
    if not os.path.exists(config.CHROME_USER_DATA_DIR):
        print(f"ERROR: {config.CHROME_USER_DATA_DIR} doesn't exist yet.")
        print("Follow the README Step 1 first (create profile + log in manually).")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=config.CHROME_USER_DATA_DIR,
            channel="chrome",       # use REAL installed Chrome, not bundled Chromium
            headless=False,
        )
        page = context.new_page()

        print("Navigating to kaggle.com ...")
        page.goto("https://www.kaggle.com")
        page.wait_for_timeout(3000)  # let the page settle

        # A logged-in Kaggle page shows the user's avatar/profile menu.
        # If we land on a login prompt instead, the session isn't valid.
        if "login" in page.url or page.query_selector("text=Sign In"):
            print("NOT logged in. Go re-check Step 1 in the README.")
        else:
            print("Looks logged in! You're good to run the scrapers.")

        input("Press Enter to close this browser window...")
        context.close()


if __name__ == "__main__":
    main()
