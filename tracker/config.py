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
# Aurora signed in with IAM tokens (express clusters): the cluster's writer endpoint
DB_HOST = os.getenv("DB_HOST", "").strip()
DB_PORT = int(os.getenv("DB_PORT", "") or 5432)
DB_USER = os.getenv("DB_USER", "") or "postgres"
# Aurora through the RDS Data API (full-configuration clusters)
DB_CLUSTER_ARN = os.getenv("DB_CLUSTER_ARN", "")
DB_SECRET_ARN = os.getenv("DB_SECRET_ARN", "")
DB_NAME = os.getenv("DB_NAME", "") or "postgres"
AWS_REGION = os.getenv("AWS_REGION", "") or os.getenv("AWS_DEFAULT_REGION", "")
# Or any Postgres directly, e.g. for local development: postgresql://user:pass@localhost/sharps
DATABASE_URL = os.getenv("DATABASE_URL", "")
MAX_POSTS_PER_ACCOUNT = max(5, min(100, int(os.getenv("MAX_POSTS_PER_ACCOUNT", "100"))))
FIRST_RUN_POSTS = max(5, min(100, int(os.getenv("FIRST_RUN_POSTS", "20"))))
DEFAULT_ODDS = int(os.getenv("DEFAULT_ODDS", "-110"))
# Re-read each pick post once after grading to catch deleted picks (one paid read per post)
CHECK_DELETIONS = os.getenv("CHECK_DELETIONS", "true").lower() not in ("0", "false", "no", "off")
ACCOUNTS_FILE = ROOT / "accounts.txt"

# Name or ARN of the AWS Secrets Manager secret holding the app's keys (see load_secrets)
APP_SECRET_ID = os.getenv("APP_SECRET_ID", "")
SECRET_KEYS = ("X_BEARER_TOKEN", "ANTHROPIC_API_KEY", "DB_CLUSTER_ARN", "DB_SECRET_ARN", "DB_NAME")


def load_secrets(client=None):
    """Fill in keys from AWS Secrets Manager.

    The secret is a JSON object, e.g.
        {"X_BEARER_TOKEN": "...", "ANTHROPIC_API_KEY": "...",
         "DB_CLUSTER_ARN": "arn:aws:rds:...", "DB_SECRET_ARN": "arn:aws:secretsmanager:..."}

    A value already set in the environment (or .env) wins, so you can override one
    key locally without touching the shared secret. Returns the keys that were loaded.
    """
    import json
    if not APP_SECRET_ID:
        return []
    if client is None:
        import boto3
        client = boto3.client("secretsmanager", region_name=AWS_REGION or None)
    try:
        raw = client.get_secret_value(SecretId=APP_SECRET_ID)["SecretString"]
    except Exception as e:
        raise RuntimeError(f"Could not read secret {APP_SECRET_ID!r} from AWS Secrets Manager: {e}") from e
    try:
        values = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"Secret {APP_SECRET_ID!r} isn't valid JSON. Use the key/value editor in Secrets Manager.")
    loaded = []
    for key in SECRET_KEYS:
        if values.get(key) and not os.getenv(key):
            globals()[key] = str(values[key]).strip()
            loaded.append(key)
    return loaded

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
