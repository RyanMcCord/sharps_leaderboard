"""Settings that only this bot uses. Shared ones (keys, database) are in xbots/config.py."""
import os
from pathlib import Path

from xbots.config import ROOT  # noqa: F401  (also loads .env)

from .application import Settings

HERE = Path(__file__).resolve().parent

ACCOUNTS_FILE = HERE / "accounts.txt"
SCHEMA = "sharps"                          # the bot's Postgres schema
SITE_PATH = ROOT / "docs" / "index.html"   # published by GitHub Pages (main branch, /docs)


def _int(name, default, lo=None, hi=None):
    value = int(os.getenv(name, "") or default)
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def from_env():
    return Settings(
        default_odds=_int("DEFAULT_ODDS", -110),
        check_deletions=os.getenv("CHECK_DELETIONS", "true").lower() not in ("0", "false", "no", "off"),
        max_posts_per_account=_int("MAX_POSTS_PER_ACCOUNT", 100, 5, 100),
        first_run_posts=_int("FIRST_RUN_POSTS", 20, 5, 100),
    )


def load_handles():
    """The starter list in accounts.txt. Each run adds any handle not tracked yet."""
    handles = []
    for line in ACCOUNTS_FILE.read_text().splitlines():
        line = line.strip().lstrip("@")
        if line and not line.startswith("#"):
            handles.append(line)
    return handles
