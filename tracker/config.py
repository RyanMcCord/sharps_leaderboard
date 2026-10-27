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
# Aurora Serverless (through the RDS Data API)
DB_CLUSTER_ARN = os.getenv("DB_CLUSTER_ARN", "")
DB_SECRET_ARN = os.getenv("DB_SECRET_ARN", "")
DB_NAME = os.getenv("DB_NAME", "sharps")
AWS_REGION = os.getenv("AWS_REGION", "") or os.getenv("AWS_DEFAULT_REGION", "")
# Or any Postgres directly, e.g. for local development: postgresql://user:pass@localhost/sharps
DATABASE_URL = os.getenv("DATABASE_URL", "")
MAX_POSTS_PER_ACCOUNT = max(5, min(100, int(os.getenv("MAX_POSTS_PER_ACCOUNT", "100"))))
FIRST_RUN_POSTS = max(5, min(100, int(os.getenv("FIRST_RUN_POSTS", "20"))))
DEFAULT_ODDS = int(os.getenv("DEFAULT_ODDS", "-110"))
# Re-read each pick post once after grading to catch deleted picks (one paid read per post)
CHECK_DELETIONS = os.getenv("CHECK_DELETIONS", "true").lower() not in ("0", "false", "no", "off")
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
