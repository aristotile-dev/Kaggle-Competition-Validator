"""
PHASE 3: Scrape the Code tab's "Shared With You" notebook list.

Purpose: for each username, does a notebook shared with the NIAT host
account actually exist? This is existence + ownership only.

IMPORTANT (confirmed against real data): the "Score:" badge shown on some
notebook cards is NOT reliable as the source of truth for scoring - it only
reflects the notebook's current version if that exact version was itself
submitted, and can go stale if a student re-commits the notebook afterward
without resubmitting (we saw this directly with Dewanshu's notebook - his
team WAS scored, but his notebook card shows no Score line because he
re-committed after submitting). The authoritative score-per-submission data
already lives in Phase 2's submissions_scraper.py output. Score here is
captured as a bonus/secondary field only.

Same virtualization pattern as the leaderboard scraper (only a few cards
exist in the DOM at once, more load as you scroll) - reusing that proven
incremental collect-while-scrolling approach.

Run from the project root:
    python scrapers/shared_notebooks_scraper.py
"""

import sys
import os
import argparse
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from auth.browser_context import get_browser_context


def scrape_shared_notebooks(page):
    print(f"Navigating to {config.CODE_URL}")
    page.goto(config.CODE_URL)
    page.wait_for_timeout(2000)

    try:
        page.click("text=Shared With You", timeout=10000)
    except Exception as e:
        print(f"WARNING: couldn't click 'Shared With You' tab: {e}")

    try:
        page.wait_for_selector('[role="main"] ul[role="list"]', timeout=15000)
    except Exception:
        print('WARNING: No notebook list found within 15s. Saving debug artifacts.')
        dump_debug(page, "shared_notebooks")
        return []

    collected = {}
    prev_count = -1
    stable_iters = 0
    max_iters = 40

    for _ in range(max_iters):
        list_el = page.query_selector('[role="main"] ul[role="list"]')
        if not list_el:
            break

        cards = list_el.query_selector_all(":scope > li")
        for card in cards:
            # Notebook name link - has aria-label = exact notebook name
            name_link = card.query_selector('a[role="link"][aria-label]')
            if not name_link:
                continue
            notebook_name = name_link.get_attribute("aria-label")
            if not notebook_name:
                continue

            # Owner's profile link - href="/username"
            owner_link = card.query_selector('a[href^="/"][aria-label*="profile"]')
            owner_username = owner_link.get_attribute("href").lstrip("/") if owner_link else ""

            card_text = card.inner_text()
            is_private = "Private" in card_text
            # bonus/secondary field only - see module docstring caveat
            score = ""
            if "Score:" in card_text:
                for line in card_text.split("\n"):
                    if line.strip().startswith("Score:"):
                        score = line.strip().replace("Score:", "").strip()
                        break

            collected[notebook_name] = {
                "notebook_name": notebook_name,
                "owner_username": owner_username,
                "is_private": is_private,
                "score_badge": score,  # secondary/unreliable, see caveat above
            }

        if len(collected) == prev_count:
            stable_iters += 1
            if stable_iters >= 6:  # was 3 - cloud/headless can be slower to render each batch
                break
        else:
            stable_iters = 0
        prev_count = len(collected)

        if cards:
            cards[-1].scroll_into_view_if_needed()
        page.wait_for_timeout(1000)  # was 600 - more buffer for cloud network/rendering

    results = list(collected.values())
    print(f"Collected {len(results)} shared notebooks.")

    if not results:
        print("WARNING: List found, but 0 notebooks extracted.")
        dump_debug(page, "shared_notebooks")

    return results


def dump_debug(page, label):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = os.path.join(config.DATA_DIR, f"debug_{label}_{ts}.png")
    html_path = os.path.join(config.DATA_DIR, f"debug_{label}_{ts}.html")
    page.screenshot(path=screenshot_path, full_page=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"Debug screenshot saved: {screenshot_path}")
    print(f"Debug HTML saved: {html_path}")
    print(">> Send these to Orange (Claude) so we can fix the selector.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Pin the output date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    date_str = args.date if args.date else datetime.now().strftime("%Y-%m-%d")

    if not os.path.exists(config.SESSION_FILE) and not os.path.exists(config.CHROME_USER_DATA_DIR):
        print("ERROR: No auth method found (kaggle_session.json or chrome-profile).")
        return

    if config.COMPETITION_URL == "PASTE_YOUR_COMPETITION_URL_HERE":
        print("ERROR: Set COMPETITION_URL in config.py first.")
        return

    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()

        data = scrape_shared_notebooks(page)

        if data:
            df = pd.DataFrame(data)
            os.makedirs(config.DATA_DIR, exist_ok=True)
            out_path = os.path.join(config.DATA_DIR, f"shared_notebooks_{date_str}.csv")
            df.to_csv(out_path, index=False)
            print(f"\nSaved {len(df)} rows to {out_path}")
            print(df.to_string(index=False))
        else:
            print("No data scraped. Check debug artifacts in data/ folder.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
