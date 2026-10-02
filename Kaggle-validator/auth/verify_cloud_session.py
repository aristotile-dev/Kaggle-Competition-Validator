"""
Verify the CLOUD session (kaggle_session.json) is still valid - works
correctly on a headless Linux server, unlike auth/verify_session.py which
only checks the local Windows Chrome profile.

Run from the project root:
    python auth/verify_cloud_session.py
"""

import sys
import os
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from auth.browser_context import get_browser_context


def main():
    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()

        print("Navigating to kaggle.com ...")
        page.goto("https://www.kaggle.com")
        page.wait_for_timeout(3000)

        if "login" in page.url or page.query_selector("text=Sign In"):
            print("NOT LOGGED IN - kaggle_session.json has expired.")
            print("Fix: re-run auth/export_session_for_cloud.py on your Windows")
            print("machine, then upload the fresh kaggle_session.json to this server.")
        else:
            print("Looks logged in! Session is still valid.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
