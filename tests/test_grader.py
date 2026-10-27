"""Offline tests for grading and the leaderboard: python -m unittest discover tests"""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from unittest import mock
from tracker.grader import grade_pick, units_net
from tracker.parser import might_be_pick
from tracker.report import write_site
from tracker.results import parse_scoreboard


def team(display, short, abbr, location):
    return {"displayName": display, "shortDisplayName": short, "name": short, "abbreviation": abbr, "location": location}


def event(eid, start, home, hs, away, as_, state="STATUS_FINAL"):
    return {
        "id": eid, "date": start,
        "competitions": [{
            "status": {"type": {"completed": state == "STATUS_FINAL", "name": state}},
            "competitors": [
                {"homeAway": "home", "team": home, "score": str(hs)},
                {"homeAway": "away", "team": away, "score": str(as_)},
            ],
        }],
    }


LAKERS = team("Los Angeles Lakers", "Lakers", "LAL", "Los Angeles")
CLIPPERS = team("LA Clippers", "Clippers", "LAC", "LA")
SUNS = team("Phoenix Suns", "Suns", "PHX", "Phoenix")
CELTICS = team("Boston Celtics", "Celtics", "BOS", "Boston")
KNICKS = team("New York Knicks", "Knicks", "NY", "New York")
CHIEFS = team("Kansas City Chiefs", "Chiefs", "KC", "Kansas City")
RAIDERS = team("Las Vegas Raiders", "Raiders", "LV", "Las Vegas")

NBA_DAY = parse_scoreboard({"events": [
    event("1", "2026-10-25T23:30Z", LAKERS, 112, SUNS, 105),       # Lakers win by 7, total 217
    event("2", "2026-10-26T00:00Z", CELTICS, 100, KNICKS, 100 - 3), # Celtics win by 3, total 197
    event("3", "2026-10-26T02:00Z", CLIPPERS, 99, team("Utah Jazz", "Jazz", "UTAH", "Utah"), 101, "STATUS_POSTPONED"),
]})
NFL_SUNDAY = parse_scoreboard({"events": [event("9", "2026-11-01T17:00Z", CHIEFS, 27, RAIDERS, 20)]})


def games_for(league, day):
    if league == "NBA" and day == "20261025":
        return NBA_DAY
    if league == "NFL" and day == "20261101":
        return NFL_SUNDAY
    return []


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
POSTED = "2026-10-25T16:00:00Z"  # noon ET on game day


def pick(**kw):
    base = {"league": "NBA", "posted_at": POSTED, "odds": None, "units": None, "line": None, "side": None,
            "team": None, "opponent": None, "legs": "[]"}
    base.update(kw)
    return base


class GradeTests(unittest.TestCase):
    def g(self, **kw):
        return grade_pick(pick(**kw), games_for, NOW)

    def test_moneyline(self):
        self.assertEqual(self.g(bet_type="moneyline", team="Lakers")[0], "win")
        self.assertEqual(self.g(bet_type="moneyline", team="Suns")[0], "loss")
        self.assertEqual(self.g(bet_type="moneyline", team="PHX", odds=150, units=2)[:2], ("loss", -2))

    def test_spread(self):
        self.assertEqual(self.g(bet_type="spread", team="Lakers", line=-6.5)[0], "win")
        self.assertEqual(self.g(bet_type="spread", team="Lakers", line=-7)[0], "push")
        self.assertEqual(self.g(bet_type="spread", team="Lakers", line=-7.5)[0], "loss")
        self.assertEqual(self.g(bet_type="spread", team="Knicks", line=3.5)[0], "win")
        self.assertEqual(self.g(bet_type="spread", team="New York Knicks", line=2.5)[0], "loss")

    def test_total(self):
        self.assertEqual(self.g(bet_type="total", team="Lakers", opponent="Suns", side="over", line=215.5)[0], "win")
        self.assertEqual(self.g(bet_type="total", team="Suns", opponent="Lakers", side="under", line=215.5)[0], "loss")
        self.assertEqual(self.g(bet_type="total", team="Celtics", side="under", line=197)[0], "push")

    def test_wrong_opponent_does_not_match(self):
        self.assertEqual(self.g(bet_type="moneyline", team="Lakers", opponent="Celtics")[0], "manual")

    def test_postponed_is_void(self):
        self.assertEqual(self.g(bet_type="moneyline", team="Clippers")[0], "void")

    def test_city_name_alone_is_ambiguous_only_when_needed(self):
        # "Los Angeles" only matches the Lakers here (Clippers' location is "LA")
        self.assertEqual(self.g(bet_type="moneyline", team="Los Angeles")[0], "win")

    def test_props_go_to_manual(self):
        self.assertEqual(self.g(bet_type="player_prop", description="LeBron o27.5 pts")[0], "manual")

    def test_pending_before_window_closes(self):
        early = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
        self.assertIsNone(grade_pick(pick(bet_type="moneyline", team="Warriors"), games_for, early)[0])

    def test_nfl_pick_posted_midweek(self):
        p = pick(league="NFL", bet_type="spread", team="Chiefs", line=-3.5, posted_at="2026-10-28T14:00:00Z")
        self.assertEqual(grade_pick(p, games_for, NOW)[0], "win")

    def test_parlay(self):
        legs = [{"bet_type": "moneyline", "team": "Lakers"}, {"bet_type": "spread", "team": "Celtics", "line": -2.5}]
        r, net, _, info = self.g(bet_type="parlay", legs=legs, units=1)
        self.assertEqual(r, "win")
        self.assertAlmostEqual(net, (1 + 100 / 110) ** 2 - 1, places=2)
        self.assertEqual(info["start"], "2026-10-25T23:30Z")  # earliest leg
        self.assertTrue(info["before_start"])
        legs[1]["line"] = -3.5
        self.assertEqual(self.g(bet_type="parlay", legs=legs)[0], "loss")

    def test_game_is_linked(self):
        r, net, note, game = self.g(bet_type="spread", team="Lakers", line=-6.5)
        self.assertEqual((game["id"], game["home_team"], game["away_team"]), ("1", "Los Angeles Lakers", "Phoenix Suns"))
        self.assertEqual((game["home_score"], game["away_score"]), (112, 105))
        self.assertTrue(game["before_start"])

    def test_late_pick_is_flagged(self):
        late = pick(bet_type="moneyline", team="Lakers", posted_at="2026-10-26T00:30:00Z")  # an hour after tip
        r, net, note, game = grade_pick(late, games_for, NOW)
        self.assertEqual(r, "win")
        self.assertFalse(game["before_start"])
        self.assertIn("after game start", note)

    def test_partial_game_bets_go_to_hand_grade(self):
        r, net, note, game = self.g(bet_type="spread", team="Lakers", line=-3.5, period="first_half")
        self.assertEqual(r, "manual")
        self.assertIn("1st half", note)
        self.assertEqual(game["id"], "1")  # still linked, so the timing check works
        legs = [{"bet_type": "moneyline", "team": "Lakers"}, {"bet_type": "moneyline", "team": "Celtics", "period": "first_quarter"}]
        self.assertEqual(self.g(bet_type="parlay", legs=legs)[0], "manual")

    def test_db_pick_conversion(self):
        from tracker.grader import to_grader
        g = to_grader({"sport": "NBA", "type": "over_under", "line": -115, "point": 215.5, "side": "over", "team": "Lakers",
                       "period": "full_game", "created_at": POSTED,
                       "legs": [{"sport": "OTHER", "type": "moneyline", "team": "Suns"}]})
        self.assertEqual((g["bet_type"], g["odds"], g["line"], g["league"]), ("total", -115, 215.5, "NBA"))
        self.assertEqual(g["legs"][0]["league"], "NBA")  # leg without a league takes the parlay's

    def test_units(self):
        self.assertAlmostEqual(units_net("win", -110, 1), 0.909, places=3)
        self.assertEqual(units_net("win", 150, 2), 3.0)
        self.assertEqual(units_net("loss", 150, 2), -2)
        self.assertEqual(units_net("push", -110, 1), 0)


class ReportTests(unittest.TestCase):
    def test_site_embeds_picks_safely(self):
        data = {"generated": "2026-10-26T21:00:00+00:00", "defaultOdds": -110, "minSample": 30,
                "accounts": [{"h": "SharpPickss", "f": 1}],
                "picks": [{"h": "SharpPickss", "t": POSTED, "lg": "NBA", "d": "Tricky </script><b>x</b>",
                           "o": None, "u": 1, "r": "win", "n": 0.909, "id": "1"}]}
        tmp = tempfile.mkdtemp()
        with mock.patch("tracker.report.site_data", lambda db: data):
            page = open(write_site(None, os.path.join(tmp, "docs", "index.html")), encoding="utf-8").read()
        self.assertNotIn("__DATA__", page)
        self.assertIn("SharpPickss", page)
        self.assertEqual(page.count("</script>"), 2)  # post text can't close the data block early


class PrefilterTests(unittest.TestCase):
    def post(self, text, media="[]"):
        return {"text": text, "media_urls": media}

    def test_obvious_non_picks_are_skipped(self):
        self.assertFalse(might_be_pick(self.post("Good morning fam, big day today")))
        self.assertFalse(might_be_pick(self.post("Join the VIP, link in bio")))

    def test_picks_get_through(self):
        for t in ["Lakers -4.5 (2u)", "LeBron o27.5 pts", "Chiefs ML", "Over 47 KC/LV", "Duke vs UNC under", "3u play: Celtics"]:
            self.assertTrue(might_be_pick(self.post(t)), t)

    def test_images_always_checked(self):
        self.assertTrue(might_be_pick(self.post("🔒🔒", '["https://pbs.twimg.com/x.jpg"]')))


if __name__ == "__main__":
    unittest.main()
