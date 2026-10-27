"""Offline tests for grading and the leaderboard: python -m unittest discover tests"""
import os
import tempfile
import unittest
from datetime import datetime, timezone

from tracker import db
from tracker.grader import grade_pick, units_net
from tracker.report import leaderboard, write_html
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
        r, net, _ = self.g(bet_type="parlay", legs=legs, units=1)
        self.assertEqual(r, "win")
        self.assertAlmostEqual(net, (1 + 100 / 110) ** 2 - 1, places=2)
        legs[1]["line"] = -3.5
        self.assertEqual(self.g(bet_type="parlay", legs=legs)[0], "loss")

    def test_units(self):
        self.assertAlmostEqual(units_net("win", -110, 1), 0.909, places=3)
        self.assertEqual(units_net("win", 150, 2), 3.0)
        self.assertEqual(units_net("loss", 150, 2), -2)
        self.assertEqual(units_net("push", -110, 1), 0)


class ReportTests(unittest.TestCase):
    def test_leaderboard_and_html(self):
        tmp = tempfile.mkdtemp()
        conn = db.connect(os.path.join(tmp, "t.db"))
        rows = [("a", "win", 0.909, 1), ("a", "win", 0.909, 1), ("a", "loss", -1, 1),
                ("b", "loss", -2, 2), ("b", "push", 0, 1), ("b", None, None, 1)]
        for i, (h, r, net, u) in enumerate(rows):
            conn.execute("INSERT INTO picks(post_id, handle, posted_at, league, bet_type, description, result, units_net, units) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (str(i), h, POSTED, "NBA", "moneyline", "x", r, net, u))
        board = leaderboard(conn)
        self.assertEqual(board[0]["handle"], "a")
        self.assertAlmostEqual(board[0]["units"], 0.818, places=3)
        self.assertAlmostEqual(board[0]["roi"], 0.818 / 3, places=3)
        self.assertEqual((board[1]["loss"], board[1]["push"], board[1]["pending"]), (1, 1, 1))
        path = write_html(conn, os.path.join(tmp, "lb.html"))
        self.assertIn("@a", open(path).read())


if __name__ == "__main__":
    unittest.main()
