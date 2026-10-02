"""
Automatically invalidate submissions that reconciliation marked invalid.

Selectors CONFIRMED against real Kaggle UI via recon (not guessed):
  - Checkbox: row -> [data-field="__check__"] -> input[type="checkbox"]
  - Action button: button[aria-label="Invalidate selected submissions"]

Kaggle's own button title is "Invalidate SELECTED submissions" - confirmed
via DOM inspection that this only acts on whichever checkboxes are ticked.
So as long as we only ever tick a team's specific individually-invalid
submission IDs (never all of them blindly), a team's valid submissions
are never touched.

Batches by team: opens each team's panel ONCE, ticks every one of that
team's pending-invalid submission checkboxes, then clicks Invalidate ONCE
for the whole batch.

Safety model:
  - Defaults to dry-run (no --apply flag = nothing happens on Kaggle)
  - --apply requires typing "CONFIRM" after seeing the exact team-by-team
    breakdown of what's about to be invalidated
  - --no-prompt skips that typed confirmation - ONLY meant for
    telegram_poller.py to use after a real human tap already served as
    the confirmation. Never pass this yourself by hand.
  - Already-invalidated submissions (state == "warning") are skipped
  - Every action, skip, and error is logged to invalidation_actions_<date>.csv

Run from the project root:
    python actions/invalidate_submissions.py              (dry-run, default)
    python actions/invalidate_submissions.py --apply       (requires typed CONFIRM)
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

import pandas as pd
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from auth.browser_context import get_browser_context


def today():
    return datetime.now().strftime("%Y-%m-%d")


def truthy_false(value):
    return str(value).strip().lower() in {"false", "0", "no", "nan", ""}


def load_targets(date_str):
    """Returns {team_name: [{"submission_id", "reason", "status", "message"}, ...]}"""
    audit_path = os.path.join(config.DATA_DIR, f"reconciliation_audit_{date_str}.csv")
    submissions_path = os.path.join(config.DATA_DIR, f"submissions_{date_str}.csv")

    if not os.path.exists(audit_path):
        raise FileNotFoundError(f"{audit_path} not found. Run engine/reconciliation.py first.")
    if not os.path.exists(submissions_path):
        raise FileNotFoundError(
            f"{submissions_path} not found. Run scrapers/submissions_scraper.py first."
        )

    audit_df = pd.read_csv(audit_path, dtype=str).fillna("")
    submissions_df = pd.read_csv(submissions_path, dtype=str).fillna("")

    invalid_df = audit_df[audit_df["valid"].apply(truthy_false)].copy()
    if invalid_df.empty:
        return {}

    invalid_df["submission_id"] = invalid_df["submission_id"].astype(str)
    submissions_df["submission_id"] = submissions_df["submission_id"].astype(str)

    merged = invalid_df.merge(
        submissions_df[["team", "submission_id", "state"]],
        on=["team", "submission_id"],
        how="left",
    )

    grouped = defaultdict(list)
    for _, row in merged.iterrows():
        state = str(row.get("state", "")).strip().lower()
        entry = {"submission_id": row["submission_id"], "reason": row.get("reason", "")}
        if "warning" in state:
            entry["status"] = "skipped"
            entry["message"] = "Already invalidated according to warning state"
        else:
            entry["status"] = "pending"
            entry["message"] = ""
        grouped[row["team"]].append(entry)

    return grouped


def dismiss_cookie_banner(page):
    try:
        page.click("text=OK, Got it", timeout=2500)
    except Exception:
        pass


def open_team_panel(page, team_name):
    """Search the paginated roster grid for an EXACT team name match (not a
    substring match) and open its submissions panel."""
    for attempt in range(2):
        if attempt == 1:
            page.goto(config.TEAMS_SUBMISSIONS_URL)
            dismiss_cookie_banner(page)

        page.wait_for_selector('[role="grid"]', timeout=15000)

        for _ in range(50):
            page.wait_for_timeout(500)
            rows = page.locator('[role="grid"] [role="row"][data-id]')
            match = None
            for i in range(rows.count()):
                row = rows.nth(i)
                name_cell = row.locator('[data-field="name"]').first
                if name_cell.count() > 0 and name_cell.inner_text().strip() == team_name:
                    match = name_cell
                    break
            if match is not None:
                match.click()
                page.wait_for_selector("text=Team submissions", timeout=10000)
                page.wait_for_timeout(700)
                return True

            next_btn = page.locator('button[aria-label="Go to next page"]').first
            if next_btn.count() == 0 or next_btn.is_disabled():
                break
            next_btn.click()

    return False


def close_team_panel(page):
    close_btn = page.locator('button[aria-label="Close"]').first
    if close_btn.count() > 0:
        close_btn.click()
    else:
        page.keyboard.press("Escape")
    page.wait_for_timeout(500)


def tick_submission_checkbox(page, submission_id):
    """Find this specific submission's row (by exact data-id) in the open
    panel and tick ONLY its checkbox."""
    selector = f'.MuiDrawer-paper [role="row"][data-id="{submission_id}"]'

    for _ in range(25):
        row = page.locator(selector)
        if row.count() > 0:
            checkbox = row.locator('[data-field="__check__"] input[type="checkbox"]').first
            if checkbox.count() == 0:
                return False
            if not checkbox.is_checked():
                checkbox.click()
            return True

        visible_rows = page.locator('.MuiDrawer-paper [role="row"][data-id]')
        if visible_rows.count() > 0:
            visible_rows.nth(visible_rows.count() - 1).scroll_into_view_if_needed()
        page.wait_for_timeout(400)

    return False


def click_invalidate_button(page, apply_changes):
    if not apply_changes:
        return "dry_run", "Would click 'Invalidate selected submissions'"

    button = page.locator('button[aria-label="Invalidate selected submissions"]')
    if button.count() == 0:
        return "error", "Invalidate button not found"
    if button.first.get_attribute("aria-disabled") == "true":
        return "error", "Invalidate button is disabled - nothing ticked?"

    button.first.click()
    page.wait_for_timeout(1200)

    for label in ["Confirm", "Yes"]:
        confirm_btn = page.get_by_role("button", name=label)
        if confirm_btn.count() > 0 and confirm_btn.first.is_visible():
            confirm_btn.first.click()
            page.wait_for_timeout(800)
            break

    return "invalidated", "Clicked Invalidate for selected submissions"


def dump_debug(page, label):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = os.path.join(config.DATA_DIR, f"debug_{label}_{ts}.png")
    html_path = os.path.join(config.DATA_DIR, f"debug_{label}_{ts}.html")
    page.screenshot(path=screenshot_path, full_page=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page.content())
    return screenshot_path, html_path


def confirm_before_apply(grouped_targets):
    pending = {
        team: [e for e in entries if e["status"] == "pending"]
        for team, entries in grouped_targets.items()
    }
    pending = {t: e for t, e in pending.items() if e}
    total = sum(len(e) for e in pending.values())

    print("\n" + "=" * 60)
    print(f"ABOUT TO INVALIDATE {total} submission(s) across {len(pending)} team(s):")
    for team, entries in pending.items():
        ids = ", ".join(e["submission_id"] for e in entries)
        print(f"  {team}: {ids}")
    print("=" * 60)

    response = input("\nType CONFIRM (all caps) to proceed, anything else to abort: ")
    return response.strip() == "CONFIRM"


def run(date_str, apply_changes, skip_prompt=False):
    grouped_targets = load_targets(date_str)
    if not grouped_targets:
        print("No invalid submissions found in reconciliation audit.")
        return

    total = sum(len(v) for v in grouped_targets.values())
    pending_total = sum(
        len([e for e in v if e["status"] == "pending"]) for v in grouped_targets.values()
    )
    skipped_total = total - pending_total

    print(f"Loaded {total} invalid audit row(s) across {len(grouped_targets)} team(s).")
    print(f"Pending invalidation: {pending_total}")
    print(f"Already skipped (previously invalidated): {skipped_total}")
    print(f"Mode: {'APPLY' if apply_changes else 'DRY RUN'}")

    if apply_changes and pending_total > 0 and not skip_prompt:
        if not confirm_before_apply(grouped_targets):
            print("Aborted - no changes made.")
            return

    results = []

    with sync_playwright() as p:
        context, browser = get_browser_context(p)
        page = context.new_page()
        page.goto(config.TEAMS_SUBMISSIONS_URL)
        dismiss_cookie_banner(page)

        for team, entries in grouped_targets.items():
            pending_entries = [e for e in entries if e["status"] == "pending"]
            skipped_entries = [e for e in entries if e["status"] == "skipped"]

            for e in skipped_entries:
                print(f'SKIP already invalidated: {team} / {e["submission_id"]}')
                results.append({"team": team, **e})

            if not pending_entries:
                continue

            print(f"\nProcessing {team}: {len(pending_entries)} submission(s) to invalidate")

            try:
                if not open_team_panel(page, team):
                    for e in pending_entries:
                        e["status"] = "error"
                        e["message"] = "Could not find team row"
                    results.extend({"team": team, **e} for e in pending_entries)
                    continue

                ticked_any = False
                for e in pending_entries:
                    if tick_submission_checkbox(page, e["submission_id"]):
                        ticked_any = True
                    else:
                        e["status"] = "error"
                        e["message"] = "Could not find/tick submission row"

                if ticked_any:
                    status, message = click_invalidate_button(page, apply_changes)
                    for e in pending_entries:
                        if e["status"] == "pending":
                            e["status"] = status
                            e["message"] = message

                for e in pending_entries:
                    results.append({"team": team, **e})
                    print(f'{e["status"].upper()}: {team} / {e["submission_id"]} - {e["message"]}')

                close_team_panel(page)

            except Exception as ex:
                screenshot_path, html_path = dump_debug(page, f"invalidate_{team}")
                for e in pending_entries:
                    if e["status"] == "pending":
                        e["status"] = "error"
                        e["message"] = f"{ex}; debug: {screenshot_path}, {html_path}"
                    results.append({"team": team, **e})
                print(f"ERROR processing {team}: {ex}")
                try:
                    close_team_panel(page)
                except Exception:
                    pass

        context.close()
        if browser:
            browser.close()

    out_path = os.path.join(config.DATA_DIR, f"invalidation_actions_{date_str}.csv")
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"\nSaved invalidation action log to {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today(), help="Date suffix to use, e.g. 2026-09-11")
    parser.add_argument("--apply", action="store_true", help="Actually click invalidate in Kaggle")
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Skip the interactive CONFIRM prompt. Only meant for telegram_poller.py to use "
        "after a real human tap has already been confirmed via Telegram - never pass this "
        "flag yourself by hand.",
    )
    args = parser.parse_args()

    if not os.path.exists(config.SESSION_FILE) and not os.path.exists(config.CHROME_USER_DATA_DIR):
        print("ERROR: No auth method found (kaggle_session.json or chrome-profile).")
        return

    run(args.date, apply_changes=args.apply, skip_prompt=args.no_prompt)


if __name__ == "__main__":
    main()
