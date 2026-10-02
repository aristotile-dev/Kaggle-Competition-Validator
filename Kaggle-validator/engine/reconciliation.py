"""
Reconciliation engine.

Key design decision: we do NOT just check a team's CURRENT best submission.
If we did, invalidating it would just cause their next-best submission to
surface as their new score - possibly ALSO invalid, requiring another
day's run to catch. Instead: audit EVERY scored submission a team has, in
one pass. Find the best-ranked one that's actually valid (their "true"
effective score), and collect every invalid submission ID so they can all
be invalidated together.

IMPORTANT - metric direction is auto-detected, not assumed: different
competitions use different metrics (RMSE = lower is better, accuracy =
higher is better). This was a REAL bug we caught in production once - the
code used to assume lower-is-better always, which silently picked each
team's WORST submission as their "current best" on a higher-is-better
competition, inverting every decision. Fixed by reading the actual
leaderboard rank order to determine direction empirically each run.

Validity rule per submission:
  - Details empty (raw CSV)          -> INVALID
  - Details non-empty (notebook) AND
    username owns a shared notebook  -> VALID
  - Details non-empty AND username
    NOT found in shared notebooks    -> INVALID (not shared)
  - state == "warning" (already
    invalidated by the host)         -> INVALID, but excluded from the
    "needs action" list/count - already handled, shouldn't keep showing up
    as fresh work forever. (Separately, submissions invalidated through
    THIS automation naturally get a blank score on re-scrape, which
    already excludes them from consideration before this check even runs -
    this state check specifically catches the host's own manual
    invalidations via Kaggle's native UI.)

Run from the project root:
    python engine/reconciliation.py
"""

import sys
import os
import argparse
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def detect_metric_direction(leaderboard_df):
    """Determines whether this competition's metric is higher-is-better
    (e.g. accuracy, F1) or lower-is-better (e.g. RMSE/RMSLE) by looking at
    the ACTUAL leaderboard order we scraped, rather than assuming one
    direction always applies. Comparing rank #1's score against the
    worst-ranked team's score tells us which way Kaggle is sorting."""
    if leaderboard_df.empty or "rank" not in leaderboard_df.columns:
        return False  # default to lower-is-better if we can't tell

    lb = leaderboard_df.copy()
    lb["rank_num"] = pd.to_numeric(lb["rank"], errors="coerce")
    lb["score_num"] = pd.to_numeric(lb["score"], errors="coerce")
    lb = lb.dropna(subset=["rank_num", "score_num"]).sort_values("rank_num")

    if len(lb) < 2:
        return False

    best_score = lb.iloc[0]["score_num"]
    worst_score = lb.iloc[-1]["score_num"]
    higher_is_better = best_score > worst_score
    print(
        f"Detected metric direction: {'HIGHER' if higher_is_better else 'LOWER'} is better "
        f"(rank 1 score={best_score}, last rank score={worst_score})"
    )
    return higher_is_better


def submission_validity(details_present, username, shared_usernames_lower):
    if not details_present:
        return False, "Raw CSV submission (no notebook)"
    if not username:
        return False, "Cannot verify - no username mapping for this team"
    if username.lower() in shared_usernames_lower:
        return True, "Notebook shared and verified"
    return False, "Notebook not shared with evaluator account"


def build_reconciliation(roster_df, submissions_df, notebooks_df, leaderboard_df):
    team_to_username = dict(zip(leaderboard_df["team"], leaderboard_df["username"]))
    shared_usernames_lower = set(
        notebooks_df["owner_username"].dropna().astype(str).str.lower()
    )

    higher_is_better = detect_metric_direction(leaderboard_df)

    subs = submissions_df.copy()
    subs["public_score_num"] = pd.to_numeric(subs["public_score"], errors="coerce")
    subs["private_score_num"] = pd.to_numeric(subs["private_score"], errors="coerce")
    subs["details_present"] = subs["details"].fillna("").astype(str).str.strip() != ""

    team_results = []
    submission_audit = []

    for _, roster_row in roster_df.iterrows():
        team = roster_row["team"]
        username = team_to_username.get(team, "")
        current_public = roster_row["public_score"]
        current_private = roster_row["private_score"]

        team_subs = subs[subs["team"] == team]
        valid_scored = team_subs.dropna(subset=["public_score_num"]).sort_values(
            "public_score_num", ascending=not higher_is_better
        )

        if valid_scored.empty:
            team_results.append(
                {
                    "team": team,
                    "username": username,
                    "current_public_score": current_public,
                    "current_private_score": current_private,
                    "decision": "Invalid",
                    "reason": "No scored submission at all",
                    "effective_valid_score": None,
                    "effective_valid_private_score": None,
                    "invalid_submission_ids": "",
                }
            )
            continue

        invalid_ids = []
        effective_valid_row = None
        current_best_id = valid_scored.iloc[0]["submission_id"]

        for _, sub in valid_scored.iterrows():
            state = str(sub.get("state", "")).strip().lower()
            already_invalidated = "warning" in state

            if already_invalidated:
                is_valid, reason = False, "Already invalidated (previously actioned)"
            else:
                is_valid, reason = submission_validity(
                    sub["details_present"], username, shared_usernames_lower
                )

            submission_audit.append(
                {
                    "team": team,
                    "submission_id": sub["submission_id"],
                    "public_score": sub["public_score_num"],
                    "details_present": sub["details_present"],
                    "valid": is_valid,
                    "reason": reason,
                    "is_current_best": sub["submission_id"] == current_best_id,
                    "already_invalidated": already_invalidated,
                }
            )
            if is_valid and effective_valid_row is None:
                effective_valid_row = sub
            if not is_valid and not already_invalidated:
                invalid_ids.append(str(sub["submission_id"]))

        if effective_valid_row is not None:
            is_current_best_valid = effective_valid_row["submission_id"] == current_best_id
            decision = "Valid"
            if is_current_best_valid:
                reason = "Current best submission is valid"
            else:
                reason = (
                    f"Current best is INVALID, but submission "
                    f"{int(effective_valid_row['submission_id'])} "
                    f"(score {effective_valid_row['public_score_num']}) is a valid fallback"
                )
        else:
            decision = "Invalid"
            reason = "ALL scored submissions are invalid (raw CSV or unshared notebook)"

        team_results.append(
            {
                "team": team,
                "username": username,
                "current_public_score": current_public,
                "current_private_score": current_private,
                "decision": decision,
                "reason": reason,
                "effective_valid_score": (
                    effective_valid_row["public_score_num"]
                    if effective_valid_row is not None
                    else None
                ),
                "effective_valid_private_score": (
                    effective_valid_row["private_score_num"]
                    if effective_valid_row is not None
                    else None
                ),
                "invalid_submission_ids": ", ".join(invalid_ids),
            }
        )

    return pd.DataFrame(team_results), pd.DataFrame(submission_audit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Pin the date to use (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()
    date_str = args.date if args.date else pd.Timestamp.now().strftime("%Y-%m-%d")
    try:
        roster_df = pd.read_csv(os.path.join(config.DATA_DIR, f"teams_roster_{date_str}.csv"))
        submissions_df = pd.read_csv(os.path.join(config.DATA_DIR, f"submissions_{date_str}.csv"))
        notebooks_df = pd.read_csv(
            os.path.join(config.DATA_DIR, f"shared_notebooks_{date_str}.csv")
        )
        leaderboard_df = pd.read_csv(
            os.path.join(config.DATA_DIR, f"leaderboard_{date_str}.csv")
        )
    except FileNotFoundError as e:
        print(f"ERROR: Missing input file - {e}")
        print("Make sure today's leaderboard/roster/submissions/shared_notebooks CSVs all exist in data/")
        return

    team_results, submission_audit = build_reconciliation(
        roster_df, submissions_df, notebooks_df, leaderboard_df
    )

    team_out = os.path.join(config.DATA_DIR, f"reconciliation_teams_{date_str}.csv")
    audit_out = os.path.join(config.DATA_DIR, f"reconciliation_audit_{date_str}.csv")
    team_results.to_csv(team_out, index=False)
    submission_audit.to_csv(audit_out, index=False)

    print(f"Saved team-level decisions to {team_out}")
    print(f"Saved full submission audit to {audit_out}\n")
    print(team_results.to_string(index=False))

    print(f"\n\nValid: {(team_results['decision'] == 'Valid').sum()}")
    print(f"Invalid: {(team_results['decision'] == 'Invalid').sum()}")


if __name__ == "__main__":
    main()
