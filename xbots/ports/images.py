"""Port for preparing images: making them fit X's limits, and small enough for Claude to read."""
from abc import ABC, abstractmethod

from ..core import Media


class UnusableImage(ValueError):
    """The file isn't an image we can post (unreadable, or a format X doesn't take)."""


class ImageProcessor(ABC):
    @abstractmethod
    def for_post(self, data: bytes, mime_type: str) -> Media:
        """The image as it should be uploaded to X: a supported format, within X's size limit,
        at the highest quality that fits. Raises UnusableImage."""

    @abstractmethod
    def for_reading(self, data: bytes, mime_type: str) -> Media:
        """A smaller copy for an AI model to look at. Raises UnusableImage."""
