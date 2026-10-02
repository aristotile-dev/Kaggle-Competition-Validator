"""
ONE-TIME interactive login (Windows/local only).

Opens a real, persistent Chrome profile (stored at config.CHROME_USER_DATA_DIR)
and lets you log in manually as the host Kaggle account (dsmlcontent).
Handle any 2FA yourself, then press Enter in the terminal once you're in.

Because this uses launch_persistent_context pointed at CHROME_USER_DATA_DIR,
the login cookies are saved directly into that profile folder on disk - no
separate session-file export needed for local/Windows use. Every other
script (scrapers, actions) reuses this same profile automatically via
auth/browser_context.py.

Run from the project root:
    python auth/login_and_save_session.py

After this, verify with:
    python auth/verify_session.py

(For deploying to the headless Linux/EC2 server later, use
auth/export_session_for_cloud.py separately to export a portable
kaggle_session.json from this same logged-in profile.)
"""

import sys
import os
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def main():
    os.makedirs(config.CHROME_USER_DATA_DIR, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=config.CHROME_USER_DATA_DIR,
            channel="chrome",
            headless=False,  # MUST be headed for manual login
            viewport=None,  # None = use the real OS window size, not a fixed small viewport
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        print("Opening Kaggle login page...")
        page.goto("https://www.kaggle.com/account/login")

        print("\n" + "=" * 60)
        print("ACTION NEEDED:")
        print("1. Log in with the HOST account (dsmlcontent) in the browser window.")
        print("   (If the window looks too small, just maximize it or press Ctrl + - to zoom out.)")
        print("2. Complete any 2FA / verification if prompted.")
        print("3. Once you see you're logged in (e.g. your profile icon top-right),")
        print("   come back here and press Enter.")
        print("=" * 60 + "\n")
        input("Press Enter once logged in...")

        print(f"\nDone - the login is saved directly in the Chrome profile at:")
        print(f"  {config.CHROME_USER_DATA_DIR}")
        print("Every script in this project will reuse it automatically.")
        print("Run 'python auth/verify_session.py' to confirm.")

        context.close()


if __name__ == "__main__":
    main()
