"""
Builds/updates a persistent Team -> Email lookup from Kaggle's "Download All
Scores" export (Settings > Teams & Submissions > Download All Scores).

This is a MANUAL, occasional step - not part of the nightly automated
pipeline - since it requires you to click that button and download the
file yourself. Run this whenever you have a fresh export to refresh the
lookup with the latest known emails.

IMPORTANT LIMITATION: this export only includes teams that have had at
least one SCORED submission, ever. Teams with zero valid submissions never
appear in it at all, so this can't give you emails for that group - a
different data source would be needed for those (an internal NIAT roster,
if one exists).

The lookup accumulates over time - if you run this on multiple different
export downloads across the competition, newly-appearing teams get added
without erasing previously-found emails, so coverage only grows.

Usage:
    python output/build_email_lookup.py path/to/downloaded_scores.csv
"""

import sys
import os
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

LOOKUP_PATH = os.path.join(config.DATA_DIR, "team_emails.csv")


def main():
    if len(sys.argv) < 2:
        print("Usage: python output/build_email_lookup.py path/to/downloaded_scores.csv")
        return

    export_path = sys.argv[1]
    if not os.path.exists(export_path):
        print(f"ERROR: {export_path} not found.")
        return

    # Kaggle's export can be tab or comma separated depending on how it was saved
    new_df = pd.read_csv(export_path, sep=None, engine="python")

    if "TeamName" not in new_df.columns or "UserEmail" not in new_df.columns:
        print("ERROR: expected columns 'TeamName' and 'UserEmail' not found in this file.")
        print(f"Found columns: {list(new_df.columns)}")
        return

    new_lookup = (
        new_df[new_df["UserEmail"] != "[private]"]
        .drop_duplicates(subset="TeamName", keep="last")[["TeamName", "UserEmail"]]
    )

    if os.path.exists(LOOKUP_PATH):
        existing = pd.read_csv(LOOKUP_PATH)
        combined = pd.concat([existing, new_lookup]).drop_duplicates(subset="TeamName", keep="last")
    else:
        combined = new_lookup

    combined.to_csv(LOOKUP_PATH, index=False)
    print(f"Lookup now has {len(combined)} team(s) with known emails, saved to {LOOKUP_PATH}")

    private_count = (new_df["UserEmail"] == "[private]").sum()
    if private_count:
        private_teams = new_df[new_df["UserEmail"] == "[private]"]["TeamName"].unique()
        print(f"\n{private_count} row(s) had a private email, from these team(s):")
        for t in private_teams:
            print(f"  - {t}")


if __name__ == "__main__":
    main()
