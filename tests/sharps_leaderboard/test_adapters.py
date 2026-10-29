"""Leaderboard adapters, each tested against a fake of the outside service it wraps."""
import os
import tempfile
import unittest

from bots.sharps_leaderboard.adapters.claude_pick_reader import ClaudePickReader
from bots.sharps_leaderboard.adapters.espn_scores import EspnScores, parse_scoreboard
from bots.sharps_leaderboard.adapters.static_site import StaticSitePublisher, write_csv

from .fixtures import NBA_DAY, POSTED


def espn_team(display, short, abbr, location):
    return {"displayName": display, "shortDisplayName": short, "name": short, "abbreviation": abbr, "location": location}


def espn_event(eid, start, home, hs, away, as_, state="STATUS_FINAL"):
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


ESPN_NBA_DAY = {"events": [
    espn_event("1", "2026-10-25T23:30Z", espn_team("Los Angeles Lakers", "Lakers", "LAL", "Los Angeles"), 112,
               espn_team("Phoenix Suns", "Suns", "PHX", "Phoenix"), 105),
    espn_event("3", "2026-10-26T02:00Z", espn_team("LA Clippers", "Clippers", "LAC", "LA"), 99,
               espn_team("Utah Jazz", "Jazz", "UTAH", "Utah"), 101, "STATUS_POSTPONED"),
]}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class EspnTests(unittest.TestCase):
    def test_scoreboard_becomes_domain_games(self):
        games = parse_scoreboard(ESPN_NBA_DAY)
        expected = {g["id"]: g for g in NBA_DAY}
        for g in games:
            want = expected[g["id"]]
            self.assertEqual((g["start"], g["completed"], g["state"]), (want["start"], want["completed"], want["state"]))
            for side in ("home", "away"):
                self.assertEqual(g[side]["name"], want[side]["name"])
                self.assertEqual(g[side]["score"], want[side]["score"])
                self.assertTrue(set(want[side]["names"]) <= set(g[side]["names"]))

    def test_each_day_is_fetched_once(self):
        calls = []

        def get(url, params, timeout):
            calls.append((url, params))
            return FakeResponse(ESPN_NBA_DAY)

        scores = EspnScores(get=get)
        self.assertEqual(len(scores.games("NBA", "20261025")), 2)
        scores.games("NBA", "20261025")
        self.assertEqual(len(calls), 1)
        self.assertIn("basketball/nba", calls[0][0])
        self.assertEqual(scores.games("OTHER", "20261025"), [])

    def test_college_asks_for_every_team(self):
        calls = []
        EspnScores(get=lambda url, params, timeout: calls.append(params) or FakeResponse({"events": []})).games("NCAAF", "20261025")
        self.assertEqual(calls[0]["groups"], "80")


class FakeBlock:
    def __init__(self, type, name=None, input=None, text=None):
        self.type, self.name, self.input, self.text = type, name, input, text


class FakeMessages:
    def __init__(self, blocks):
        self.blocks, self.calls = blocks, []

    def create(self, **kw):
        self.calls.append(kw)
        return type("Msg", (), {"content": self.blocks})()


class ClaudeReaderTests(unittest.TestCase):
    POST = {"x_handle": "SharpPickss", "posted_at": POSTED, "text": "Lakers -4.5 (2u)",
            "media_urls": ["https://pbs.twimg.com/slip.jpg"]}

    def read(self, blocks):
        msgs = FakeMessages(blocks)
        reader = ClaudePickReader(client=type("Client", (), {"messages": msgs})(), model="test-model")
        return reader.read(self.POST), msgs.calls[0]

    def test_asks_for_the_tool_without_forcing_it(self):
        picks, req = self.read([FakeBlock("text", text="Here you go."), FakeBlock(
            "tool_use", "record_picks", {"picks": [{"sport": "nba", "type": "spread", "team": "Lakers", "point": -4.5,
                                                    "units": 2, "bet_description": "Lakers -4.5"}]})])
        self.assertEqual(req["tool_choice"], {"type": "auto"})  # forced tool choice is rejected by newer models
        self.assertEqual(req["model"], "test-model")
        self.assertEqual(req["messages"][0]["content"][0], {"type": "image", "source": {"type": "url", "url": "https://pbs.twimg.com/slip.jpg"}})
        self.assertEqual([(p["sport"], p["team"], p["point"], p["units"]) for p in picks], [("NBA", "Lakers", -4.5, 2)])

    def test_plain_text_answer_means_no_picks(self):
        picks, _ = self.read([FakeBlock("text", text="No picks in this post.")])
        self.assertEqual(picks, [])


class SiteTests(unittest.TestCase):
    DATA = {"generated": "2026-10-26T21:00:00+00:00", "defaultOdds": -110, "minSample": 30,
            "accounts": [{"h": "SharpPickss", "f": 1}],
            "picks": [{"h": "SharpPickss", "t": POSTED, "lg": "NBA", "d": "Tricky </script><b>x</b>",
                       "o": None, "u": 1, "r": "win", "n": 0.909, "id": "1", "late": False, "del": False}]}

    def test_site_embeds_picks_safely(self):
        path = os.path.join(tempfile.mkdtemp(), "docs", "index.html")
        StaticSitePublisher(path).publish(self.DATA)
        with open(path, encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("__DATA__", page)
        self.assertIn("SharpPickss", page)
        self.assertEqual(page.count("</script>"), 2)  # post text can't close the data block early

    def test_csv_is_oldest_first(self):
        path = os.path.join(tempfile.mkdtemp(), "picks.csv")
        write_csv([{"id": "b", "bet_description": "new"}, {"id": "a", "bet_description": "old"}], path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertTrue(lines[0].startswith("id,x_handle,sport"))
        self.assertTrue(lines[1].startswith("a,"))


if __name__ == "__main__":
    unittest.main()
