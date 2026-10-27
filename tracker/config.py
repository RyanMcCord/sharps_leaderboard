import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PARSER_MODEL = os.getenv("PARSER_MODEL", "claude-sonnet-5-5")
DB_PATH = os.getenv("DB_PATH", str(ROOT / "tracker.db"))
MAX_POSTS_PER_ACCOUNT = max(5, min(100, int(os.getenv("MAX_POSTS_PER_ACCOUNT", "20"))))
DEFAULT_ODDS = int(os.getenv("DEFAULT_ODDS", "-110"))
ACCOUNTS_FILE = ROOT / "accounts.txt"

# League code -> ESPN scoreboard path
LEAGUES = {
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
}


def load_handles():
    handles = []
    for line in ACCOUNTS_FILE.read_text().splitlines():
        line = line.strip().lstrip("@")
        if line and not line.startswith("#"):
            handles.append(line)
    return handles
