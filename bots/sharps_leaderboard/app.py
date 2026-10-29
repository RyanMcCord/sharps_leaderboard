"""Composition root: wires the leaderboard's use cases to the real adapters.

This is the only module that knows which technology sits behind each port.
"""
from xbots.adapters import database
from xbots.adapters.x_api import XApiPostSource

from . import settings
from .adapters.claude_pick_reader import ClaudePickReader
from .adapters.espn_scores import EspnScores
from .adapters.postgres_repository import PostgresPickRepository
from .adapters.static_site import StaticSitePublisher
from .application import SharpsLeaderboard


def build(db=None, log=print):
    return SharpsLeaderboard(
        repo=PostgresPickRepository(db or database.connect(), settings.SCHEMA),
        posts=XApiPostSource(),
        reader=ClaudePickReader(),
        scores=EspnScores(),
        site=StaticSitePublisher(settings.SITE_PATH),
        settings=settings.from_env(),
        log=log,
    )
