"""Settings that only this bot uses. Shared ones (app keys, database, Google) are in xbots/config.py."""
import os

from xbots.config import ROOT  # noqa: F401  (also loads .env)

from .application import DEFAULT_STYLE, Settings

SCHEMA = "ai_art"                                            # the bot's Postgres schema
DRIVE_FOLDER_ID = os.getenv("AI_ART_DRIVE_FOLDER_ID", "")    # the queue folder
# The posting account's own access token and secret (from `python -m xbots ai-art authorize`)
X_ACCESS_TOKEN = os.getenv("AI_ART_X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("AI_ART_X_ACCESS_SECRET", "")


def from_env():
    return Settings(
        caption_style=os.getenv("AI_ART_CAPTION_STYLE", "").strip() or DEFAULT_STYLE,
        hours_between_posts=max(1, int(os.getenv("AI_ART_HOURS_BETWEEN_POSTS", "") or 4)),
    )
