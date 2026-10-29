"""Domain tests: grading and pick rules, with no database, network or files."""
import unittest
from datetime import datetime, timezone

from bots.sharps_leaderboard.domain.grading import grade_pick, to_grader, units_net
from bots.sharps_leaderboard.domain.picks import clean_pick, might_be_pick

from .fixtures import NOW, POSTED, games_for


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


class PickRuleTests(unittest.TestCase):
    def test_clean_pick_keeps_values_valid(self):
        p = clean_pick({"sport": "nba", "type": "Spread", "line": "-115", "point": -4.5, "side": "sideways",
                        "period": "1H??", "bet_description": "  Lakers -4.5  "})
        self.assertEqual((p["sport"], p["type"], p["line"], p["side"], p["period"], p["bet_description"]),
                         ("NBA", "spread", -115, None, "other", "Lakers -4.5"))
        self.assertEqual(clean_pick({"sport": "cricket", "type": "weird", "line": "even"})["sport"], "OTHER")
        self.assertIsNone(clean_pick({"line": "even"})["line"])

    def test_parlay_legs_are_cleaned(self):
        p = clean_pick({"sport": "NBA", "type": "parlay", "legs": [{"type": "moneyline", "team": "Suns"}]})
        self.assertEqual(p["legs"][0]["type"], "moneyline")
        self.assertEqual(p["legs"][0]["sport"], "OTHER")  # grading falls back to the parlay's league

    def test_default_odds_can_be_changed(self):
        r, net, _, _ = grade_pick(pick(bet_type="moneyline", team="Lakers"), games_for, NOW, default_odds=-120)
        self.assertAlmostEqual(net, 100 / 120, places=3)


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
