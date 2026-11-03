"""Composition root: wires the ai-art use cases to the real adapters.

This is the only module that knows which technology sits behind each port.
"""
from xbots.adapters import database
from xbots.adapters.images import PillowImageProcessor
from xbots.adapters.x_api import XApiPostPublisher

from . import settings
from .adapters.claude_captioner import ClaudeCaptioner
from .adapters.drive_queue import DriveImageQueue
from .adapters.postgres_post_log import PostgresPostLog
from .application import AiArtPoster


def build(db=None, log=print):
    return AiArtPoster(
        queue=DriveImageQueue(settings.DRIVE_FOLDER_ID),
        images=PillowImageProcessor(),
        captioner=ClaudeCaptioner(),
        log_store=PostgresPostLog(db or database.connect(), settings.SCHEMA),
        publisher=XApiPostPublisher(settings.X_ACCESS_TOKEN, settings.X_ACCESS_SECRET, log=log),
        settings=settings.from_env(),
        log=log,
    )
