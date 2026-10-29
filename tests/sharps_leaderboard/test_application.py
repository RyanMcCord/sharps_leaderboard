"""The leaderboard's use cases, run end to end against in-memory adapters."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from bots.sharps_leaderboard.application import Settings, SharpsLeaderboard
from bots.sharps_leaderboard.domain.picks import clean_pick

from .fixtures import games_for
from .memory import (CapturingSite, FakePickReader, FakePostSource, FakeScores, InMemoryPickRepository, post, user)

LAKERS_SPREAD = clean_pick({"sport": "NBA", "type": "spread", "team": "Lakers", "opponent": "Suns", "point": -4.5,
                            "units": 2, "bet_description": "Lakers -4.5 vs Suns"})
SUNS_ML = clean_pick({"sport": "NBA", "type": "moneyline", "team": "Suns", "bet_description": "Suns moneyline"})
PROP = clean_pick({"sport": "NBA", "type": "prop", "side": "over", "point": 27.5, "bet_description": "LeBron over 27.5"})
LIVE = clean_pick({"sport": "NBA", "type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML live"})
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def build(posts=None, reader=None, settings=None):
    lines = []
    app = SharpsLeaderboard(
        repo=InMemoryPickRepository(),
        posts=posts or FakePostSource(
            users={"sharppickss": user(111, "SharpPickss", 128900), "salbets_": user(222, "salbets_", 11900)},
            timelines={"111": [
                post(105, "2026-10-26T00:30:00.000Z", "Lakers ML live"),             # after tip-off
                post(104, "2026-10-25T16:10:00.000Z", "LeBron o27.5"),
                post(103, "2026-10-25T16:05:00.000Z", "Suns ML"),
                post(102, "2026-10-25T16:00:00.000Z", "Lakers -4.5 (2u)"),
                post(101, "2026-10-25T15:00:00.000Z", "good morning fam"),           # not a pick
            ]}),
        reader=reader or FakePickReader({"Lakers -4.5 (2u)": [LAKERS_SPREAD], "Suns ML": [SUNS_ML],
                                         "LeBron o27.5": [PROP], "Lakers ML live": [LIVE]}),
        scores=FakeScores(games_for),
        site=CapturingSite(),
        settings=settings or Settings(settle_hours=0),
        log=lines.append,
    )
    return app, lines


class DailyPipelineTests(unittest.TestCase):
    def test_full_daily_run(self):
        app, log = build()
        with mock.patch("bots.sharps_leaderboard.domain.grading.datetime") as dt:
            dt.now.return_value = NOW
            dt.fromisoformat = datetime.fromisoformat
            failed = app.run(["setup", "fetch", "parse", "grade", "deletions", "report"], ["SharpPickss", "salbets_"])
        self.assertEqual(failed, [])

        accounts = {a["x_handle"]: a for a in app.accounts()}
        self.assertEqual(accounts["SharpPickss"]["followers"], 128900)
        self.assertEqual(accounts["SharpPickss"]["last_seen_post_id"], "105")
        self.assertIn("  @SharpPickss: 5 new posts", log)
        self.assertIn("  checked 5 posts (1 skipped as not picks), found 4 picks", log)
        self.assertEqual(app.reader.read_posts, ["102", "103", "104", "105"])  # "good morning fam" never sent to Claude

        picks = {p["bet_description"]: p for p in app.all_picks()}
        self.assertEqual((picks["Lakers -4.5 vs Suns"]["result"], picks["Lakers -4.5 vs Suns"]["units_net"]), ("win", 1.818))
        self.assertEqual(picks["Suns moneyline"]["result"], "loss")
        self.assertEqual(picks["LeBron over 27.5"]["result"], "manual")
        self.assertIs(picks["Lakers ML live"]["posted_before_start"], False)
        self.assertEqual(picks["Lakers -4.5 vs Suns"]["home_team"], "Los Angeles Lakers")

        board = {r["x_handle"]: r for r in app.standings()}["SharpPickss"]
        self.assertEqual((board["wins"], board["losses"], board["late_picks"], board["needs_hand_grade"]), (1, 1, 1, 1))

        site = app.site.published[-1]
        self.assertEqual({a["h"] for a in site["accounts"]}, {"SharpPickss", "salbets_"})
        flags = {p["d"]: (p["r"], p["late"], p["del"]) for p in site["picks"]}
        self.assertEqual(flags["Lakers ML live"], ("win", True, False))

    def test_second_run_only_fetches_new_posts(self):
        app, _ = build()
        app.run(["setup", "fetch"], ["SharpPickss"])
        app.run(["fetch"], ["SharpPickss"])
        timeline_calls = [c for c in app.posts.calls if c[0] == "timeline"]
        self.assertEqual(timeline_calls[0][2:], (None, 20))   # first run: small backfill
        self.assertEqual(timeline_calls[1][2:], ("105", 100))  # then only newer than the last post seen

    def test_one_failing_step_doesnt_stop_the_rest(self):
        app, log = build(posts=FakePostSource(fail=RuntimeError("402 payment required")))
        failed = app.run(["setup", "fetch", "parse", "grade", "deletions", "report"], ["SharpPickss"])
        self.assertEqual(failed, ["Fetching posts"])
        self.assertEqual(len(app.site.published), 1)  # the site still updated
        self.assertIn("  Fetching posts failed: 402 payment required", log)

    def test_setup_failure_stops_the_run(self):
        app, _ = build()
        app.repo.setup = mock.Mock(side_effect=RuntimeError("database unreachable"))
        self.assertEqual(app.run(["setup", "fetch", "report"], ["SharpPickss"]), ["Checking tables"])
        self.assertEqual(app.site.published, [])

    def test_a_post_that_fails_to_read_is_retried_next_run(self):
        app, log = build(reader=FakePickReader({"Suns ML": [SUNS_ML]}, fail_on={"Suns ML"}))
        app.run(["setup", "fetch", "parse"], ["SharpPickss"])
        self.assertTrue(any("could not read post 103" in line for line in log))
        self.assertEqual([p["id"] for p in app.repo.unparsed_posts()], ["103"])
        app.reader.fail_on.clear()
        app.read_picks()
        self.assertEqual([p["bet_description"] for p in app.all_picks()], ["Suns moneyline"])

    def test_deleted_posts_are_found_once(self):
        posts = FakePostSource(users={"sharppickss": user(111, "SharpPickss")},
                               timelines={"111": [post(103, "2026-10-25T16:05:00.000Z", "Suns ML")]}, deleted={"103"})
        app, _ = build(posts=posts)
        app.run(["setup", "fetch", "parse", "grade"], ["SharpPickss"])
        self.assertEqual(app.check_deletions(), 1)
        self.assertEqual(app.check_deletions(), 0)
        self.assertEqual(len([c for c in posts.calls if c[0] == "find_deleted"]), 1)
        self.assertTrue(app.all_picks()[0]["deleted"])

    def test_deletion_checks_can_be_turned_off(self):
        app, _ = build(settings=Settings(check_deletions=False, settle_hours=0))
        app.run(["setup", "fetch", "parse", "grade"], ["SharpPickss"])
        self.assertEqual(app.check_deletions(), 0)
        self.assertFalse([c for c in app.posts.calls if c[0] == "find_deleted"])


class ByHandTests(unittest.TestCase):
    def setUp(self):
        self.app, _ = build()
        self.app.run(["setup", "fetch", "parse", "grade"], ["SharpPickss"])

    def test_grade_by_hand(self):
        prop = self.app.picks_needing_hand_grade()[0]
        pick, net = self.app.grade_by_hand(prop["id"][:8], "win")
        self.assertAlmostEqual(net, 0.909, places=3)
        self.assertEqual(self.app.picks_needing_hand_grade(), [])
        with self.assertRaisesRegex(ValueError, "No pick"):
            self.app.grade_by_hand("zzzz", "win")
        with self.assertRaisesRegex(ValueError, "result must be"):
            self.app.grade_by_hand(prop["id"], "maybe")

    def test_pause_resume_and_add(self):
        self.assertTrue(self.app.set_account_active("@SharpPickss", False))
        self.assertEqual(self.app.all_picks(), [])  # paused accounts drop off the site
        self.assertFalse(self.app.set_account_active("nobody", False))
        self.app.add_account("@SharpPickss")
        self.assertTrue(self.app.all_picks())
        self.app.add_account("newsharp")
        self.assertIn("newsharp", [a["x_handle"] for a in self.app.accounts()])


if __name__ == "__main__":
    unittest.main()
