"""Ports for talking to X. Bots depend on these interfaces, never on the X API directly."""
from abc import ABC, abstractmethod

from ..core import Post, XUser


class PostSource(ABC):
    """Reading posts from X."""

    @abstractmethod
    def lookup_users(self, handles: list[str]) -> tuple[list[XUser], list[str]]:
        """Resolve handles to users. Returns (found users, handles that couldn't be found)."""

    @abstractmethod
    def fetch_timeline(self, user_id: str, since_id: str | None, max_results: int, max_pages: int) -> list[Post]:
        """A user's own posts (no reposts), newest first.

        With since_id, only posts newer than it, following pages up to max_pages.
        Without it, just the latest max_results posts.
        """

    @abstractmethod
    def find_deleted(self, post_ids: list[str]) -> set[str]:
        """Which of these posts (up to 100) no longer exist. Posts that can't be checked
        (private or suspended accounts) are not reported as deleted."""


class PostPublisher(ABC):
    """Publishing to X from a bot account. No bot posts yet; see README "Posting to X"."""

    @abstractmethod
    def publish(self, text: str, reply_to: str | None = None) -> str:
        """Post text (optionally as a reply). Returns the new post's id."""
