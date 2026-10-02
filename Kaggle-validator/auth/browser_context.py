"""
Shared browser-context helper - lets every scraper/action script work
UNCHANGED on both your Windows laptop and a cloud/Linux server, by
auto-detecting which auth method is actually available:

  - kaggle_session.json exists  -> cloud/Linux mode: launch default
    Playwright Chromium, load the exported session.
  - chrome-profile/ folder exists -> local/Windows mode: reuse the real,
    manually-authenticated Chrome profile via launch_persistent_context.

Usage in a script:
    from auth.browser_context import get_browser_context
    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()
        ...
        context.close()
        if browser:
            browser.close()
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def get_browser_context(playwright):
    """Returns (context, browser). `browser` is None when using the
    persistent-profile path (it has no separate browser object to close) -
    always check `if browser:` before calling browser.close()."""

    viewport = {"width": 1440, "height": 1600}

    if os.path.exists(config.SESSION_FILE):
        browser = playwright.chromium.launch(headless=config.HEADLESS)
        context = browser.new_context(storage_state=config.SESSION_FILE, viewport=viewport)
        return context, browser

    if os.path.exists(config.CHROME_USER_DATA_DIR):
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config.CHROME_USER_DATA_DIR,
            channel="chrome",
            headless=config.HEADLESS,
            viewport=viewport,
        )
        return context, None

    raise RuntimeError(
        "No auth method found. Need either kaggle_session.json (cloud) "
        "or the chrome-profile folder (local Windows) to exist."
    )
