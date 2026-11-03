"""Shared domain objects for anything a bot reads from or posts to X. Plain data, no dependencies."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class XUser:
    id: str
    handle: str
    followers: int | None = None


@dataclass(frozen=True)
class Post:
    id: str
    created_at: str                  # ISO 8601, UTC, e.g. 2026-10-28T16:00:00.000Z
    text: str
    media_urls: tuple[str, ...] = ()  # images (and video previews) attached to the post
    raw: dict = field(default_factory=dict, compare=False)  # the source's full record, kept for reference


@dataclass(frozen=True)
class Media:
    """An image to attach to a post."""
    data: bytes = field(repr=False)
    mime_type: str                    # image/jpeg, image/png or image/webp
    alt_text: str | None = None       # read aloud by screen readers; X allows up to 1,000 characters
