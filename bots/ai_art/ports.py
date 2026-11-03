"""Output ports for the ai-art bot: what its use cases need from the outside world.

Posting to X uses the shared PostPublisher port, and preparing images the shared
ImageProcessor port (xbots/ports). The adapters for these live in adapters/; tests use
in-memory versions.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from xbots.core import Media

from .domain.queue import QueuedImage, QueueFile


class ImageQueue(ABC):
    """Where you drop the images to post (production: a Google Drive folder)."""

    @abstractmethod
    def list_files(self) -> list[QueueFile]:
        """Every file in the queue folder."""

    @abstractmethod
    def download(self, file_id: str) -> bytes:
        """One file's contents."""


@dataclass(frozen=True)
class Caption:
    text: str
    alt_text: str


class Captioner(ABC):
    """Writes a post's caption and alt text by looking at the image (production: Claude)."""

    @abstractmethod
    def describe(self, image: Media, style: str) -> Caption:
        """A caption in the given style, and a plain description for alt text."""


class PostLog(ABC):
    """What has been posted, so nothing posts twice (production: Postgres, `ai_art` schema)."""

    @abstractmethod
    def setup(self) -> None:
        """Create or upgrade storage."""

    @abstractmethod
    def handled_ids(self) -> set[str]:
        """Ids of every image that's posted, skipped or mid-post: none of them post again."""

    @abstractmethod
    def start(self, image: QueuedImage, caption: str, alt_text: str) -> None:
        """Record that posting this image has begun, before calling X."""

    @abstractmethod
    def mark_posted(self, image_id: str, post_id: str) -> None: ...

    @abstractmethod
    def release(self, image_id: str) -> None:
        """Forget a post that X refused, so the image is tried again next run."""

    @abstractmethod
    def mark_skipped(self, image_id: str, name: str, reason: str) -> None:
        """Record an image that can't be posted, so the queue moves past it."""

    @abstractmethod
    def requeue(self, name_or_id: str) -> bool:
        """Make an image postable again (e.g. one stuck mid-post that you've checked). False if not found."""

    @abstractmethod
    def entries(self, status: str | None = None, limit: int = 20) -> list[dict]:
        """Recent entries, newest first: dicts with image_id, file_name, status, reason, caption,
        x_post_id, started_at, posted_at."""

    @abstractmethod
    def counts(self) -> dict:
        """How many entries have each status."""
