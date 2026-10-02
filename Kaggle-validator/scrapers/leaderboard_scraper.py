"""
PHASE 1 - STEP 2 (v2): Scrape the leaderboard.

Kaggle's leaderboard is NOT a <table> - it's a MUI list:
    ul[role="list"] > li.MuiListItem-root  (first li = header, skip it)
      -> div > 6 direct child <span>s, in order:
         [0] rank   [1] team name   [2] members (contains <a href="/username">)
         [3] score  [4] entries     [5] last-updated

We select on structural position + the MuiListItem-root library class
(stable) rather than the sc-xxxxx styled-components hashes (which change
on Kaggle's next deploy) - so this should survive minor Kaggle UI updates.

Run from the project root:
    python scrapers/leaderboard_scraper.py
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


def scrape_leaderboard(page):
    print(f"Navigating to {config.LEADERBOARD_URL}")
    page.goto(config.LEADERBOARD_URL)

    # dismiss cookie banner if present, it can intercept clicks/scrolls
    try:
        page.click("text=OK, Got it", timeout=3000)
    except Exception:
        pass

    try:
        page.wait_for_selector('[role="main"] ul[role="list"]', timeout=15000)
    except Exception:
        print('WARNING: No leaderboard list found within 15s. Saving debug artifacts.')
        dump_debug(page, "leaderboard")
        return []

    list_el = page.query_selector('[role="main"] ul[role="list"]')

    # Kaggle virtualizes this list - only a handful of <li> exist in the DOM
    # at once and get swapped as you scroll. So we collect incrementally on
    # every scroll step (keyed by username to dedupe) instead of expecting
    # all rows to exist simultaneously.
    collected = {}
    prev_max_rank = -1
    stable_iters = 0
    max_iters = 60

    for i in range(max_iters):
        list_el = page.query_selector('[role="main"] ul[role="list"]')  # re-query fresh, avoid stale handle
        if not list_el:
            break
        rows = list_el.query_selector_all(":scope > li")

        for li in rows:
            spans = li.query_selector_all(":scope > div > span")
            if len(spans) < 6:
                continue

            rank_text = spans[0].inner_text().strip()
            if not rank_text.isdigit():
                continue  # header row or malformed

            profile_link = spans[2].query_selector("a[href]")
            username = (
                profile_link.get_attribute("href").lstrip("/")
                if profile_link
                else spans[1].inner_text().strip()
            )

            collected[username] = {
                "rank": rank_text,
                "team": spans[1].inner_text().strip(),
                "username": username,
                "score": spans[3].inner_text().strip(),
                "entries": spans[4].inner_text().strip(),
                "last": spans[5].inner_text().strip(),
            }

        current_max_rank = max((int(v["rank"]) for v in collected.values()), default=0)
        if current_max_rank == prev_max_rank:
            stable_iters += 1
            if stable_iters >= 6:  # was 3 - cloud/headless can be slower to render each batch
                # Scroll has stopped revealing new rows. Some Kaggle leaderboards
                # gate further rows behind a "See N More" click button instead of
                # (or in addition to) scroll-virtualization. Check for it before
                # concluding we're really done.
                see_more = page.query_selector('button:has-text("See"):has-text("More")')
                if see_more:
                    print("Found 'See More' button - clicking to load further rows.")
                    see_more.click()
                    page.wait_for_timeout(800)
                    stable_iters = 0
                    continue
                break  # truly done - no more rows via scroll or click
        else:
            stable_iters = 0
        prev_max_rank = current_max_rank

        if rows:
            rows[-1].scroll_into_view_if_needed()
        else:
            page.mouse.wheel(0, 800)
        page.wait_for_timeout(1000)  # was 500 - more buffer for cloud network/rendering

    results = sorted(collected.values(), key=lambda x: int(x["rank"]))
    print(f"Collected {len(results)} unique leaderboard rows (max rank seen: {prev_max_rank}).")

    if not results:
        print("WARNING: List container found, but 0 usable data rows extracted.")
        dump_debug(page, "leaderboard")

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
        print("On Windows: follow README Step 1. On cloud: upload kaggle_session.json.")
        return

    if config.COMPETITION_URL == "PASTE_YOUR_COMPETITION_URL_HERE":
        print("ERROR: Set COMPETITION_URL in config.py first.")
        return

    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()

        data = scrape_leaderboard(page)

        if data:
            df = pd.DataFrame(data)
            os.makedirs(config.DATA_DIR, exist_ok=True)
            out_path = os.path.join(config.DATA_DIR, f"leaderboard_{date_str}.csv")
            df.to_csv(out_path, index=False)
            print(f"\nSaved {len(df)} rows to {out_path}")
            print(df[["rank", "team", "username", "score"]].to_string(index=False))
        else:
            print("No data scraped. Check debug artifacts in data/ folder.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
