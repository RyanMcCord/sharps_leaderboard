"""In-memory adapters for the leaderboard's ports, for testing the use cases without
a database, X, ESPN, Claude or the file system."""
import itertools
import uuid
from datetime import datetime, timedelta, timezone

from bots.sharps_leaderboard.ports import PickReader, PickRepository, ScoresProvider, SitePublisher
from xbots.core import Post, XUser
from xbots.ports import PostSource


def _now():
    return datetime.now(timezone.utc)


class InMemoryPickRepository(PickRepository):
    def __init__(self):
        self.accounts, self.posts, self.picks = {}, {}, {}
        self._order = itertools.count()

    def _account_by_handle(self, handle):
        return next((a for a in self.accounts.values() if a["x_handle"].lower() == handle.lower()), None)

    def setup(self, handles):
        for h in handles:
            if not self._account_by_handle(h):
                self.add_account(h)

    def list_accounts(self, active_only=True):
        rows = [dict(a) for a in self.accounts.values() if a["active"] or not active_only]
        return sorted(rows, key=lambda a: a["x_handle"].lower())

    def add_account(self, handle):
        a = self._account_by_handle(handle)
        if a:
            a["active"] = True
            return
        aid = str(uuid.uuid4())
        self.accounts[aid] = {"id": aid, "x_handle": handle, "active": True, "x_user_id": None, "followers": None,
                              "last_seen_post_id": None, "created_at": _now().isoformat()}

    def set_active(self, handle, active):
        a = self._account_by_handle(handle)
        if a:
            a["active"] = active
        return a is not None

    def save_x_user(self, handle, x_user_id, followers):
        a = self._account_by_handle(handle)
        if a:
            a.update(x_user_id=x_user_id, followers=followers, x_handle=handle)

    def set_last_seen(self, account_id, post_id):
        self.accounts[account_id]["last_seen_post_id"] = post_id

    def save_post(self, account_id, post):
        self.posts.setdefault(post.id, {"id": post.id, "sharp_account": account_id, "posted_at": post.created_at,
                                        "text": post.text, "media_urls": list(post.media_urls), "parsed": False,
                                        "deleted_at": None, "deletion_checked_at": None})

    def unparsed_posts(self, limit=500):
        rows = [dict(p, x_handle=self.accounts[p["sharp_account"]]["x_handle"])
                for p in self.posts.values() if not p["parsed"]]
        return sorted(rows, key=lambda p: p["posted_at"])[:limit]

    def mark_parsed(self, post_id):
        self.posts[post_id]["parsed"] = True

    def posts_to_check_for_deletion(self, settle_hours=12, limit=100):
        out = []
        for post in self.posts.values():
            mine = [p for p in self.picks.values() if p["post_id"] == post["id"]]
            if (post["deletion_checked_at"] is None and mine and all(p["result"] != "pending" for p in mine)
                    and max(p["graded_at"] for p in mine) <= _now() - timedelta(hours=settle_hours)):
                out.append(post["id"])
        return out[:limit]

    def record_deletion_check(self, post_ids, deleted_ids):
        for pid in post_ids:
            self.posts[pid]["deletion_checked_at"] = _now()
            if pid in deleted_ids:
                self.posts[pid]["deleted_at"] = _now()

    def add_pick(self, account_id, post_id, created_at, pick):
        pid = str(uuid.uuid4())
        self.picks[pid] = {**{k: None for k in ("team", "opponent", "side", "point", "units", "line")}, **pick,
                           "id": pid, "sharp_account": account_id, "post_id": post_id, "created_at": created_at,
                           "legs": pick.get("legs") or [], "period": pick.get("period") or "full_game",
                           "result": "pending", "units_net": None, "graded_at": None, "note": None,
                           "game_id": None, "game_start": None, "home_team": None, "away_team": None,
                           "home_score": None, "away_score": None, "posted_before_start": None,
                           "_order": next(self._order)}

    def _view(self, p):
        post = self.posts.get(p["post_id"]) or {}
        return dict(p, x_handle=self.accounts[p["sharp_account"]]["x_handle"],
                    deleted=post.get("deleted_at") is not None)

    def pending_picks(self):
        return [self._view(p) for p in sorted(self.picks.values(), key=lambda p: p["created_at"]) if p["result"] == "pending"]

    def manual_picks(self):
        return [self._view(p) for p in sorted(self.picks.values(), key=lambda p: p["created_at"]) if p["result"] == "manual"]

    def find_pick(self, id_prefix):
        return [self._view(p) for p in self.picks.values() if p["id"].startswith(id_prefix.lower())][:2]

    def set_result(self, pick_id, result, units_net, note=None, by_hand=False):
        p = self.picks[pick_id]
        p.update(result=result, units_net=units_net, graded_at=_now(),
                 note=" ".join(filter(None, [p["note"], "[graded by hand]"])) if by_hand else note)

    def set_game(self, pick_id, game):
        self.picks[pick_id].update(game_id=game.get("id"), game_start=game.get("start"),
                                   home_team=game.get("home_team"), away_team=game.get("away_team"),
                                   home_score=game.get("home_score"), away_score=game.get("away_score"),
                                   posted_before_start=game.get("before_start"))

    def all_picks(self):
        rows = [self._view(p) for p in self.picks.values() if self.accounts[p["sharp_account"]]["active"]]
        return sorted(rows, key=lambda p: (p["created_at"], p["_order"]), reverse=True)

    def standings(self):
        out = []
        for a in self.list_accounts():
            mine = [self._view(p) for p in self.picks.values() if p["sharp_account"] == a["id"]]
            counted = [p for p in mine if p["posted_before_start"] is not False]
            graded = [p for p in counted if p["result"] in ("win", "loss", "push")]
            risked = sum(p["units"] or 1 for p in graded if p["result"] != "push")
            won = round(sum(p["units_net"] or 0 for p in graded), 2)
            out.append({"x_handle": a["x_handle"],
                        "wins": sum(p["result"] == "win" for p in counted),
                        "losses": sum(p["result"] == "loss" for p in counted),
                        "pushes": sum(p["result"] == "push" for p in counted),
                        "units_won": won, "roi_pct": round(won / risked * 100, 1) if risked else None,
                        "pending": sum(p["result"] == "pending" for p in mine),
                        "needs_hand_grade": sum(p["result"] == "manual" for p in mine),
                        "late_picks": sum(p["posted_before_start"] is False for p in mine),
                        "deleted_picks": sum(p["deleted"] for p in mine)})
        return sorted(out, key=lambda r: r["units_won"], reverse=True)


class FakePostSource(PostSource):
    """users: {handle: XUser}; timelines: {user_id: [Post, ...] newest first}; deleted: set of post ids."""

    def __init__(self, users=None, timelines=None, deleted=None, fail=None):
        self.users, self.timelines, self.deleted = users or {}, timelines or {}, set(deleted or ())
        self.fail = fail
        self.calls = []

    def lookup_users(self, handles):
        if self.fail:
            raise self.fail
        found = [self.users[h.lower()] for h in handles if h.lower() in self.users]
        return found, [h for h in handles if h.lower() not in self.users]

    def fetch_timeline(self, user_id, since_id, max_results, max_pages):
        self.calls.append(("timeline", user_id, since_id, max_results))
        if self.fail:
            raise self.fail
        posts = self.timelines.get(user_id, [])
        if since_id:
            return [p for p in posts if int(p.id) > int(since_id)]
        return posts[:max_results]

    def find_deleted(self, post_ids):
        self.calls.append(("find_deleted", tuple(post_ids)))
        return {p for p in post_ids if p in self.deleted}


class FakePickReader(PickReader):
    """picks_by_text: {post text: [pick, ...]}. Text not listed has no picks."""

    def __init__(self, picks_by_text=None, fail_on=()):
        self.picks_by_text, self.fail_on, self.read_posts = picks_by_text or {}, set(fail_on), []

    def read(self, post):
        self.read_posts.append(post["id"])
        if post["text"] in self.fail_on:
            raise RuntimeError("reader unavailable")
        return list(self.picks_by_text.get(post["text"], []))


class FakeScores(ScoresProvider):
    def __init__(self, games_for):
        self.games_for = games_for

    def games(self, league, day):
        return self.games_for(league, day)


class CapturingSite(SitePublisher):
    def __init__(self):
        self.published = []

    def publish(self, data):
        self.published.append(data)
        return "memory://site"


def post(pid, created_at, text, media=()):
    return Post(id=str(pid), created_at=created_at, text=text, media_urls=tuple(media))


def user(uid, handle, followers=None):
    return XUser(id=str(uid), handle=handle, followers=followers)
