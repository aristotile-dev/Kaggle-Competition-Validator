"""
PHASE 1c: Scrape the Settings > Teams & Submissions roster.

This is a MUI X DataGrid (role="grid"), not a list or table - cells carry
data-field attributes (rank, name, entries, publicScore, privateScore,
isHidden), which is far more robust than positional guessing.

This is the AUTHORITATIVE full team roster (all 35, whether scored or not) -
the leaderboard alone would silently miss unscored teams. Any team here with
no rank/score is an automatic "Invalid: no scored submission" case later.

For competitions with >~50 teams, Kaggle paginates via a real
"Go to next page" button (not virtualization) - handled below.

Run from the project root:
    python scrapers/teams_roster_scraper.py
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


def scrape_teams_roster(page):
    print(f"Navigating to {config.TEAMS_SUBMISSIONS_URL}")
    page.goto(config.TEAMS_SUBMISSIONS_URL)

    try:
        page.wait_for_selector('[role="grid"]', timeout=15000)
    except Exception:
        print('WARNING: No DataGrid found within 15s. Saving debug artifacts.')
        dump_debug(page, "teams_roster")
        return []

    all_rows = {}
    page_num = 1

    while True:
        page.wait_for_timeout(700)  # let the grid settle after nav/click

        # data-id presence reliably distinguishes data rows from the header row
        row_elements = page.query_selector_all('[role="grid"] [role="row"][data-id]')
        print(f"Page {page_num}: found {len(row_elements)} rows.")

        for row in row_elements:
            data_id = row.get_attribute("data-id")

            def cell_text(field):
                cell = row.query_selector(f'[data-field="{field}"]')
                return cell.inner_text().strip() if cell else ""

            hidden_cell = row.query_selector('[data-field="isHidden"]')
            # Heuristic: a "hidden" row shows a visibility-off style icon instead
            # of the "more_vert" kebab menu. Not yet confirmed against a real
            # hidden example - flag for review if this ever looks wrong.
            is_hidden = False
            if hidden_cell:
                kebab = hidden_cell.query_selector('button[aria-label="More actions"]')
                is_hidden = kebab is None  # no kebab button visible -> likely hidden state

            all_rows[data_id] = {
                "team": cell_text("name"),
                "rank": cell_text("rank"),
                "entries": cell_text("entries"),
                "public_score": cell_text("publicScore"),
                "private_score": cell_text("privateScore"),
                "is_hidden": is_hidden,
            }

        next_btn = page.query_selector('button[aria-label="Go to next page"]')
        if not next_btn or next_btn.is_disabled():
            break
        next_btn.click()
        page_num += 1

    results = list(all_rows.values())
    if not results:
        print("WARNING: Grid found, but 0 rows extracted.")
        dump_debug(page, "teams_roster")

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

        data = scrape_teams_roster(page)

        if data:
            df = pd.DataFrame(data)
            os.makedirs(config.DATA_DIR, exist_ok=True)
            out_path = os.path.join(config.DATA_DIR, f"teams_roster_{date_str}.csv")
            df.to_csv(out_path, index=False)
            print(f"\nSaved {len(df)} rows to {out_path}")
            print(df[["team", "rank", "entries", "public_score", "private_score"]].to_string(index=False))

            unscored = df[df["public_score"] == ""]
            if len(unscored) > 0:
                print(f"\n{len(unscored)} team(s) with NO score at all (auto-invalid candidates):")
                print(unscored[["team", "entries"]].to_string(index=False))
        else:
            print("No data scraped. Check debug artifacts in data/ folder.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
