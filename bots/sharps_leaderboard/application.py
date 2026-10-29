"""The sharps leaderboard's use cases (its input port).

Everything here works through ports, so it runs the same against the real X API,
Aurora, ESPN and Claude (see app.py) as against in-memory test adapters.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from xbots.ports import PostSource

from .domain.grading import grade_pick, to_grader, units_net
from .domain.picks import DEFAULT_ODDS, might_be_pick
from .ports import PickReader, PickRepository, ScoresProvider, SitePublisher

RESULTS_BY_HAND = ("win", "loss", "push", "void")


@dataclass
class Settings:
    default_odds: int = DEFAULT_ODDS
    check_deletions: bool = True       # re-read each pick post once after grading (one paid X read per post)
    max_posts_per_account: int = 100   # per page, once an account has been fetched before
    first_run_posts: int = 20          # backfill for an account's first fetch
    max_pages: int = 5                 # pages of new posts per account per run
    settle_hours: int = 12             # wait after grading before checking for deletion
    min_sample: int = 30               # graded picks before an account counts on the leaderboard


class SharpsLeaderboard:
    def __init__(self, repo: PickRepository, posts: PostSource, reader: PickReader, scores: ScoresProvider,
                 site: SitePublisher, settings: Settings | None = None, log=print):
        self.repo, self.posts, self.reader, self.scores, self.site = repo, posts, reader, scores, site
        self.settings = settings or Settings()
        self.log = log

    # --- the daily pipeline -------------------------------------------------

    def setup(self, handles):
        """Create or upgrade storage and add any new handles."""
        self.repo.setup(handles)

    def fetch_posts(self):
        """Store posts made since the last run, for every active account. Returns how many."""
        unresolved = [a["x_handle"] for a in self.repo.list_accounts() if not a["x_user_id"]]
        if unresolved:
            found, missing = self.posts.lookup_users(unresolved)
            for user in found:
                self.repo.save_x_user(user.handle, user.id, user.followers)
            for handle in missing:
                self.log(f"  could not find account: {handle}")
        total = 0
        s = self.settings
        for acct in self.repo.list_accounts():
            if not acct["x_user_id"]:
                continue
            since = acct["last_seen_post_id"]
            posts = self.posts.fetch_timeline(acct["x_user_id"], since,
                                              s.max_posts_per_account if since else s.first_run_posts, s.max_pages)
            for post in posts:
                self.repo.save_post(acct["id"], post)
            if posts:
                self.repo.set_last_seen(acct["id"], max(posts, key=lambda p: int(p.id)).id)
            self.log(f"  @{acct['x_handle']}: {len(posts)} new posts")
            total += len(posts)
        return total

    def read_picks(self, limit=500):
        """Read the picks out of stored posts. Obvious non-picks are skipped without a Claude call.
        A post that fails to read stays queued for the next run. Returns how many picks were found."""
        posts = self.repo.unparsed_posts(limit)
        found = skipped = 0
        for post in posts:
            if not might_be_pick(post):
                self.repo.mark_parsed(post["id"])
                skipped += 1
                continue
            try:
                picks = self.reader.read(post)
            except Exception as e:
                self.log(f"  could not read post {post['id']}: {e}")
                continue
            for pick in picks:
                self.repo.add_pick(post["sharp_account"], post["id"], post["posted_at"], pick)
            self.repo.mark_parsed(post["id"])
            found += len(picks)
        self.log(f"  checked {len(posts)} posts ({skipped} skipped as not picks), found {found} picks")
        return found

    def grade(self, now=None):
        """Match pending picks to games, record whether each went out before the start, and grade
        the ones whose games are final. Returns counts by result."""
        def games_for(league, day):
            try:
                return self.scores.games(league, day)
            except Exception as e:
                self.log(f"  could not load {league} scores for {day}: {e}")
                return []

        rows = self.repo.pending_picks()
        counts, late = {}, 0
        for row in rows:
            result, net, note, game = grade_pick(to_grader(row), games_for, now, self.settings.default_odds)
            if game:
                self.repo.set_game(row["id"], game)
                late += game.get("before_start") is False
            if result is None:
                continue
            self.repo.set_result(row["id"], result, net, note)
            counts[result] = counts.get(result, 0) + 1
        self.log(f"  graded {sum(counts.values())} of {len(rows)} pending picks {counts or ''}"
                 f"{f', {late} posted after the game started' if late else ''}")
        return counts

    def check_deletions(self):
        """Check each graded pick post once to see whether the tipster deleted it. Returns how many were."""
        if not self.settings.check_deletions:
            self.log("  skipped (CHECK_DELETIONS is off)")
            return 0
        found = 0
        while True:
            ids = self.repo.posts_to_check_for_deletion(self.settings.settle_hours)
            if not ids:
                break
            deleted = self.posts.find_deleted(ids)
            self.repo.record_deletion_check(ids, deleted)
            found += len(deleted)
            if len(ids) < 100:
                break
        self.log(f"  {found} deleted pick posts found")
        return found

    def site_data(self):
        """What the leaderboard page shows. Standings are worked out in the browser from these picks."""
        picks = [{
            "h": p["x_handle"], "t": p["created_at"], "lg": p["sport"], "d": p["bet_description"],
            "o": p["line"], "u": p["units"], "r": p["result"], "n": p["units_net"], "id": p["post_id"],
            "late": p["posted_before_start"] is False, "del": bool(p["deleted"]),
        } for p in self.repo.all_picks()]
        return {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "defaultOdds": self.settings.default_odds,
            "minSample": self.settings.min_sample,
            "accounts": [{"h": a["x_handle"], "f": a["followers"]} for a in self.repo.list_accounts()],
            "picks": picks,
        }

    def publish(self):
        where = self.site.publish(self.site_data())
        self.log(f"  wrote {where}")
        return where

    STEPS = [("setup", "Checking tables"), ("fetch", "Fetching posts"), ("parse", "Reading picks"),
             ("grade", "Grading"), ("deletions", "Checking for deleted posts"), ("report", "Building leaderboard")]

    def run(self, steps, handles, stop_on_error=False):
        """Run pipeline steps in order. One failing step doesn't stop the rest (say, X credits ran
        out: grading and the site still update), except setup, which everything needs.
        Returns the names of the steps that failed."""
        actions = {"setup": lambda: self.setup(handles), "fetch": self.fetch_posts, "parse": self.read_picks,
                   "grade": self.grade, "deletions": self.check_deletions, "report": self.publish}
        failed = []
        for key, label in self.STEPS:
            if key not in steps:
                continue
            self.log(f"{label}...")
            try:
                actions[key]()
            except Exception as e:
                self.log(f"  {label} failed: {e}")
                failed.append(label)
                if stop_on_error or key == "setup":
                    break
        return failed

    # --- by hand ------------------------------------------------------------

    def add_account(self, handle):
        self.repo.add_account(handle.lstrip("@"))

    def set_account_active(self, handle, active):
        return self.repo.set_active(handle.lstrip("@"), active)

    def accounts(self):
        return self.repo.list_accounts(active_only=False)

    def standings(self):
        return self.repo.standings()

    def picks_needing_hand_grade(self):
        return self.repo.manual_picks()

    def grade_by_hand(self, id_prefix, result, units=None):
        """Grade one pick by hand. Returns (pick, units_net). Raises ValueError with a reason if it can't."""
        if result not in RESULTS_BY_HAND:
            raise ValueError(f"result must be one of {', '.join(RESULTS_BY_HAND)}")
        matches = self.repo.find_pick(id_prefix)
        if len(matches) != 1:
            raise ValueError("No pick with that id." if not matches
                             else "More than one pick starts with that id; use more characters.")
        pick = matches[0]
        net = units if units is not None else (
            None if result == "void" else units_net(result, pick["line"] or self.settings.default_odds, pick["units"]))
        self.repo.set_result(pick["id"], result, net, by_hand=True)
        return pick, net

    def all_picks(self):
        return self.repo.all_picks()
