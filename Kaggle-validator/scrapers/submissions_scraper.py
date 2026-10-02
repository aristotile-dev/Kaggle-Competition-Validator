"""
PHASE 2: Scrape every team's submissions - the Details column is THE signal
that separates notebook-sourced submissions from raw CSV uploads.

For each team on the roster:
  1. Click their name cell to open the "Team submissions" side panel
  2. Scrape each submission row: state icon, ID, Details (full text via the
     cell's title attribute - avoids UI truncation), public/private score, date
  3. Close the panel, move to the next team

Details column meaning (confirmed with TOTZ):
  - Non-empty  -> notebook-sourced (text is the student's own commit message,
                  NOT a fixed "Notebook X | Version Y" format - don't regex it
                  as gospel, just treat non-empty as "came from a notebook")
  - Empty      -> raw CSV upload, no notebook at all

State icon meaning (confirmed with TOTZ):
  - "flag"    -> Kaggle auto-marks best/selected submission(s). NOT a red flag.
                 Purely informational, ignore for validity decisions.
  - "warning" -> TOTZ has ALREADY manually invalidated this submission before.
                 Treat as "previously handled", don't re-flag - just log it as
                 such so the daily sheet doesn't double up on old work.

Run from the project root:
    python scrapers/submissions_scraper.py

WARNING: This opens/closes a panel per team (35 teams here) - takes a couple
of minutes. That's normal.
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


def cell_text(row, field):
    """Prefer the cell's title attribute - it holds the FULL text even when
    the visible UI truncates it with an ellipsis (this matters a lot for
    the Details column, which can be a long commit message)."""
    cell = row.query_selector(f'[data-field="{field}"]')
    if not cell:
        return ""
    content = cell.query_selector(".MuiDataGrid-cellContent")
    if content:
        title = content.get_attribute("title")
        if title:
            return title
    return cell.inner_text().strip()


def scrape_open_panel(page):
    """Scrape the currently-open Team submissions drawer. Defensive against
    virtualization for teams with many submissions, same pattern as the
    leaderboard scraper."""
    collected = {}
    prev_count = -1
    stable_iters = 0

    for _ in range(20):
        panel_rows = page.query_selector_all('.MuiDrawer-paper [role="row"][data-id]')

        for row in panel_rows:
            sub_id = row.get_attribute("data-id")

            state_icon = row.query_selector('[data-field="state"] span.google-symbols')
            state = state_icon.inner_text().strip() if state_icon else ""

            collected[sub_id] = {
                "submission_id": sub_id,
                "state": state,
                "details": cell_text(row, "details"),
                "public_score": cell_text(row, "publicScore"),
                "private_score": cell_text(row, "privateScore"),
                "date": cell_text(row, "date"),
            }

        if len(collected) == prev_count:
            stable_iters += 1
            if stable_iters >= 2:
                break
        else:
            stable_iters = 0
        prev_count = len(collected)

        if panel_rows:
            panel_rows[-1].scroll_into_view_if_needed()
        page.wait_for_timeout(400)

    return list(collected.values())


def scrape_all_team_submissions(page):
    print(f"Navigating to {config.TEAMS_SUBMISSIONS_URL}")
    page.goto(config.TEAMS_SUBMISSIONS_URL)

    try:
        page.wait_for_selector('[role="grid"]', timeout=15000)
    except Exception:
        print("WARNING: Roster grid not found. Saving debug artifacts.")
        dump_debug(page, "submissions_roster")
        return {}

    all_data = {}
    page_num = 1

    while True:
        page.wait_for_timeout(700)
        n_rows = len(page.query_selector_all('[role="grid"] [role="row"][data-id]'))
        print(f"\nRoster page {page_num}: {n_rows} teams")

        for i in range(n_rows):
            # Re-query fresh every time - panel open/close can shift DOM state
            rows = page.query_selector_all('[role="grid"] [role="row"][data-id]')
            if i >= len(rows):
                break
            row = rows[i]
            name_cell = row.query_selector('[data-field="name"]')
            team_name = name_cell.inner_text().strip() if name_cell else f"row_{i}"

            print(f"  [{i+1}/{n_rows}] Opening: {team_name}")
            name_cell.click()

            try:
                page.wait_for_selector("text=Team submissions", timeout=8000)
            except Exception:
                print(f"    WARNING: panel didn't open for {team_name}, skipping.")
                continue

            page.wait_for_timeout(800)
            submissions = scrape_open_panel(page)
            all_data[team_name] = submissions
            print(f"    {len(submissions)} submission(s) found.")

            close_btn = page.query_selector('button[aria-label="Close"]')
            if close_btn:
                close_btn.click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        next_btn = page.query_selector('button[aria-label="Go to next page"]')
        if not next_btn or next_btn.is_disabled():
            break
        next_btn.click()
        page_num += 1

    return all_data


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

        all_data = scrape_all_team_submissions(page)

        if all_data:
            rows = []
            for team, subs in all_data.items():
                for sub in subs:
                    sub_row = {"team": team}
                    sub_row.update(sub)
                    rows.append(sub_row)

            df = pd.DataFrame(rows)
            os.makedirs(config.DATA_DIR, exist_ok=True)
            out_path = os.path.join(config.DATA_DIR, f"submissions_{date_str}.csv")
            df.to_csv(out_path, index=False)
            print(f"\n\nSaved {len(df)} submission rows across {len(all_data)} teams to {out_path}")

            has_notebook = df["details"].astype(str).str.strip() != ""
            print(f"\nNotebook-sourced submissions: {has_notebook.sum()}")
            print(f"Raw CSV (no notebook) submissions: {(~has_notebook).sum()}")
        else:
            print("No data scraped. Check debug artifacts in data/ folder.")

        context.close()
        if browser:
            browser.close()


if __name__ == "__main__":
    main()
