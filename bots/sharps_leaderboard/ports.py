"""Output ports for the sharps leaderboard: what its use cases need from the outside world.

Reading posts from X uses the shared PostSource port (xbots/ports). The adapters for
these ports live in adapters/; tests use in-memory versions.
"""
from abc import ABC, abstractmethod

from xbots.core import Post


class PickRepository(ABC):
    """Where accounts, posts and picks are stored (production: Postgres, in the `sharps` schema)."""

    # --- setup and accounts
    @abstractmethod
    def setup(self, handles: list[str]) -> None:
        """Create or upgrade storage, then add any handles not tracked yet (existing ones keep their active flag)."""

    @abstractmethod
    def list_accounts(self, active_only: bool = True) -> list[dict]:
        """dicts with id, x_handle, active, x_user_id, followers, last_seen_post_id, created_at."""

    @abstractmethod
    def add_account(self, handle: str) -> None:
        """Track a handle (or start tracking it again)."""

    @abstractmethod
    def set_active(self, handle: str, active: bool) -> bool:
        """Pause or resume an account. False if there's no such account."""

    @abstractmethod
    def save_x_user(self, handle: str, x_user_id: str, followers: int | None) -> None: ...

    @abstractmethod
    def set_last_seen(self, account_id: str, post_id: str) -> None: ...

    # --- posts
    @abstractmethod
    def save_post(self, account_id: str, post: Post) -> None:
        """Keep a copy of a post (ignored if it's already stored)."""

    @abstractmethod
    def unparsed_posts(self, limit: int = 500) -> list[dict]:
        """Posts not read yet: dicts with id, sharp_account, x_handle, posted_at, text, media_urls."""

    @abstractmethod
    def mark_parsed(self, post_id: str) -> None: ...

    @abstractmethod
    def posts_to_check_for_deletion(self, settle_hours: int = 12, limit: int = 100) -> list[str]:
        """Pick posts not checked yet whose picks have all been graded for at least settle_hours."""

    @abstractmethod
    def record_deletion_check(self, post_ids: list[str], deleted_ids: set[str]) -> None: ...

    # --- picks
    @abstractmethod
    def add_pick(self, account_id: str, post_id: str | None, created_at: str, pick: dict) -> None: ...

    @abstractmethod
    def pending_picks(self) -> list[dict]: ...

    @abstractmethod
    def manual_picks(self) -> list[dict]: ...

    @abstractmethod
    def find_pick(self, id_prefix: str) -> list[dict]:
        """Picks whose id starts with id_prefix (at most 2, to detect ambiguity)."""

    @abstractmethod
    def set_result(self, pick_id: str, result: str, units_net: float | None,
                   note: str | None = None, by_hand: bool = False) -> None: ...

    @abstractmethod
    def set_game(self, pick_id: str, game: dict) -> None:
        """Record the matched game: id, start, home_team, away_team, home_score, away_score, before_start."""

    @abstractmethod
    def all_picks(self) -> list[dict]:
        """Every pick from active accounts, newest first (each with x_handle, result, units_net, deleted, ...)."""

    @abstractmethod
    def standings(self) -> list[dict]:
        """Per active account: x_handle, wins, losses, pushes, units_won, roi_pct, pending,
        needs_hand_grade, late_picks, deleted_picks."""


class ScoresProvider(ABC):
    """Final scores (production: ESPN's scoreboard)."""

    @abstractmethod
    def games(self, league: str, day: str) -> list[dict]:
        """Games for a league on a YYYYMMDD date, each with id, start, home, away, completed, state."""


class PickReader(ABC):
    """Reads the betting picks out of a post (production: Claude, including bet-slip images)."""

    @abstractmethod
    def read(self, post: dict) -> list[dict]:
        """post: dict with x_handle, posted_at, text, media_urls. Returns cleaned picks (see domain/picks.py)."""


class SitePublisher(ABC):
    """Publishes the leaderboard (production: a static page for GitHub Pages)."""

    @abstractmethod
    def publish(self, data: dict) -> str:
        """Publish the leaderboard data. Returns where it went."""
