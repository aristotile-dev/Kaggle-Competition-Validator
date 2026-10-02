"""
Central config for the Kaggle Submission Validator.
Fill in COMPETITION_URL with your actual private competition URL.
"""

# --- Kaggle competition ---
# e.g. "https://www.kaggle.com/competitions/your-competition-slug"
COMPETITION_URL = "PASTE_YOUR_COMPETITION_URL_HERE"

LEADERBOARD_URL = f"{COMPETITION_URL}/leaderboard"
TEAMS_SUBMISSIONS_URL = f"{COMPETITION_URL}/settings/teams-submissions"
CODE_URL = f"{COMPETITION_URL}/code"

# --- Notebook naming convention ---
# Students name notebooks as: <username>-<CompetitionName>-notebook
# Purely for the announcement text to students - our matching logic never
# actually checks notebook NAME, only username ownership, so this is safe
# to leave stale or change without touching any matching behavior.
# UPDATE this once you know what naming convention was announced for this
# competition specifically - placeholder for now.
COMPETITION_NAME_TAG = "AgenticRAG"  # change per competition

# --- Paths ---
# Two auth methods, auto-detected by auth/browser_context.py:
#   - CHROME_USER_DATA_DIR: real Chrome profile, used on Windows (local)
#   - SESSION_FILE: portable exported session, used on Linux/cloud servers
CHROME_USER_DATA_DIR = r"C:\kaggle-validator\chrome-profile"
SESSION_FILE = "kaggle_session.json"
DATA_DIR = "data"

# --- Behavior ---
HEADLESS = True  # unattended scheduled runs don't need a visible browser window.
# Flip to False temporarily if you need to debug a scraper visually.
