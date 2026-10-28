"""End-to-end tests against a real Postgres.

Runs only when PG_TEST_URL is set, e.g.
    PG_TEST_URL=postgresql://postgres@localhost/sharps_test python -m unittest tests.test_postgres
or, without psycopg installed, PG_TEST_PSQL="<args for psql>" to go through the psql command line.
The database is wiped and rebuilt.
"""
import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from tracker import db as dbmod, store
from tracker.grader import grade_pick, to_grader

PSQL = os.getenv("PG_TEST_PSQL")
URL = os.getenv("PG_TEST_URL")


class PsqlDatabase:
    """Same interface as tracker.db's classes, through the psql command line.

    It mimics the RDS Data API on purpose, so these tests catch what would break on Aurora:
    - parameters arrive typed: ints as bigint, floats as double, strings as text
    - only SELECT statements return rows (the Data API ignores formatRecordsAs otherwise)
    """
    PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")

    def __init__(self, args):
        self.args = shlex.split(args)

    @staticmethod
    def _lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return f"CAST({v} AS bigint)"
        if isinstance(v, float):
            return f"CAST({v!r} AS float8)"
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        return "CAST('" + str(v).replace("'", "''") + "' AS text)"

    def _sql(self, sql, params):
        params = params or {}
        return self.PARAM.sub(lambda m: self._lit(params[m.group(1)]), sql)

    def _run(self, sql):
        r = subprocess.run(["psql", *self.args, "-AtqX", "-v", "ON_ERROR_STOP=1", "-c", sql],
                           capture_output=True, text=True)
        if r.returncode:
            raise dbmod.DatabaseError(r.stderr.strip())
        return r.stdout

    def query(self, sql, params=None):
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self._run(self._sql(sql, params))
            return []  # like the Data API: no rows back from INSERT/UPDATE ... RETURNING
        out = self._run(f"WITH t AS ({self._sql(sql, params)}) SELECT coalesce(json_agg(t), '[]') FROM t")
        return json.loads(out)

    def execute(self, sql, params=None):
        self._run(self._sql(sql, params))


def make_db():
    if PSQL:
        return PsqlDatabase(PSQL)
    return dbmod.PostgresDatabase(URL)


def team(display, short, abbr, location):
    return {"displayName": display, "shortDisplayName": short, "name": short, "abbreviation": abbr, "location": location}


def games_for(league, day):
    from tracker.results import parse_scoreboard
    if league == "NBA" and day == "20261025":
        return parse_scoreboard({"events": [
            {"id": "1", "date": "2026-10-25T23:30Z", "competitions": [{
                "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
                "competitors": [
                    {"homeAway": "home", "team": team("Los Angeles Lakers", "Lakers", "LAL", "Los Angeles"), "score": "112"},
                    {"homeAway": "away", "team": team("Phoenix Suns", "Suns", "PHX", "Phoenix"), "score": "105"},
                ]}]}]})
    return []


@unittest.skipUnless(PSQL or URL, "set PG_TEST_URL or PG_TEST_PSQL to run Postgres tests")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = make_db()
        cls.db.execute("DROP SCHEMA public CASCADE")
        cls.db.execute("CREATE SCHEMA public")
        dbmod.ensure_schema(cls.db)
        dbmod.ensure_schema(cls.db)  # safe to re-run

    def setUp(self):
        self.db.execute("TRUNCATE picks, posts, accounts CASCADE")

    def seed(self):
        store.seed_accounts(self.db, ["SharpPickss", "salbets_", "sharppickss"])  # duplicate in other case
        accts = {a["x_handle"]: a for a in store.list_accounts(self.db)}
        store.save_x_user(self.db, "SharpPickss", "111", 128900)
        return accts

    def test_accounts(self):
        accts = self.seed()
        self.assertEqual(sorted(accts, key=str.lower), ["salbets_", "SharpPickss"])
        a = accts["SharpPickss"]
        self.assertRegex(a["id"], r"^[0-9a-f-]{36}$")
        self.assertTrue(a["active"])
        self.assertTrue(a["created_at"].endswith("Z"))
        store.add_account(self.db, "@new_one".lstrip("@"))
        self.assertTrue(store.set_active(self.db, "SALBETS_", False))
        self.assertFalse(store.set_active(self.db, "nobody", False))
        active = [a["x_handle"] for a in store.list_accounts(self.db)]
        self.assertEqual(sorted(active, key=str.lower), ["new_one", "SharpPickss"])
        store.seed_accounts(self.db, ["salbets_"])  # re-seeding doesn't un-pause
        self.assertNotIn("salbets_", [a["x_handle"] for a in store.list_accounts(self.db)])
        full = {a["x_handle"]: a for a in store.list_accounts(self.db, active_only=False)}
        self.assertEqual(full["SharpPickss"]["followers"], 128900)
        self.assertEqual(full["SharpPickss"]["x_user_id"], "111")

    def test_full_pipeline(self):
        accts = self.seed()
        sharp = accts["SharpPickss"]

        # --- fetch: fake X API ---
        from tracker import xapi
        timeline = {
            "data": [
                {"id": "1850000000000000003", "created_at": "2026-10-25T16:00:00.000Z",
                 "text": "Lakers -4.5 (2u) and over 215.5 LAL/PHX", "attachments": {}},
                {"id": "1850000000000000002", "created_at": "2026-10-25T15:00:00.000Z",
                 "text": "Good morning fam, it's a great day", "attachments": {}},
                {"id": "1850000000000000001", "created_at": "2026-10-25T14:00:00.000Z",
                 "text": "🔒", "attachments": {"media_keys": ["m1"]}},
            ],
            "includes": {"media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg"}]},
            "meta": {"newest_id": "1850000000000000003"},
        }
        calls = []

        def fake_get(path, params):
            calls.append((path, params))
            if path == "/users/by":
                return {"data": [{"id": "222", "username": "salbets_", "public_metrics": {"followers_count": 11900}}]}
            return timeline if path == "/users/111/tweets" else {"meta": {"result_count": 0}}

        with mock.patch.object(xapi, "_get", fake_get):
            self.assertEqual(xapi.fetch_new_posts(self.db), 3)
            self.assertEqual(xapi.fetch_new_posts(self.db), 3)  # same fake response; posts dedupe
        second_call = [p for path, p in calls if path == "/users/111/tweets"][-1]
        self.assertEqual(second_call["since_id"], "1850000000000000003")
        self.assertEqual(second_call["max_results"], 100)

        # more than 100 new posts since the last run: follow next_token until it runs out
        pages = [
            {"data": [{"id": "1850000000000000006", "created_at": "2026-10-25T18:00:00.000Z", "text": "a"}],
             "meta": {"next_token": "page2"}},
            {"data": [{"id": "1850000000000000005", "created_at": "2026-10-25T17:30:00.000Z", "text": "b"},
                      {"id": "1850000000000000004", "created_at": "2026-10-25T17:00:00.000Z", "text": "c"}],
             "meta": {}},
        ]
        paged = []

        def paged_get(path, params):
            if path != "/users/111/tweets":
                return {"meta": {}}
            paged.append(dict(params))
            return pages[len(paged) - 1]

        with mock.patch.object(xapi, "_get", paged_get):
            self.assertEqual(xapi.fetch_new_posts(self.db), 3)
        self.assertEqual(paged[1]["pagination_token"], "page2")
        self.assertEqual(paged[1]["since_id"], "1850000000000000003")
        acct = {a["x_handle"]: a for a in store.list_accounts(self.db)}["SharpPickss"]
        self.assertEqual(acct["last_seen_post_id"], "1850000000000000006")
        self.db.execute("DELETE FROM posts WHERE id IN ('1850000000000000004', '1850000000000000005', '1850000000000000006')")
        self.assertEqual(len(store.unparsed_posts(self.db)), 3)

        # --- parse: fake Claude ---
        from tracker import parser

        def fake_extract(client, post):
            if post["media_urls"]:
                self.assertEqual(post["media_urls"], ["https://pbs.twimg.com/a.jpg"])
                return [parser.clean_pick({"sport": "NBA", "type": "parlay", "line": 264, "bet_description": "Lakers ML + Suns ML",
                                           "legs": [{"type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML"},
                                                    {"type": "moneyline", "team": "Suns", "bet_description": "Suns ML"}]})]
            return [parser.clean_pick(p) for p in [
                {"sport": "nba", "type": "spread", "team": "Lakers", "opponent": "Suns", "point": -4.5, "units": 2,
                 "bet_description": "Lakers -4.5 vs Suns"},
                {"sport": "NBA", "type": "over_under", "team": "Lakers", "opponent": "Suns", "side": "over",
                 "point": 215.5, "line": -115, "bet_description": "Lakers/Suns over 215.5"},
                {"sport": "NBA", "type": "prop", "side": "over", "point": 27.5, "bet_description": "LeBron over 27.5 points"},
                {"sport": "Cricket", "type": "weird", "bet_description": "unknown"},
            ]]

        with mock.patch.object(parser, "extract_picks", fake_extract), mock.patch.object(parser, "_client", lambda: object()):
            self.assertEqual(parser.parse_pending(self.db), 5)
        self.assertEqual(store.unparsed_posts(self.db), [])

        pending = store.pending_picks(self.db)
        self.assertEqual(len(pending), 5)
        by_desc = {p["bet_description"]: p for p in pending}
        self.assertEqual(by_desc["unknown"]["sport"], "OTHER")
        self.assertEqual(by_desc["unknown"]["type"], "other")
        self.assertEqual(by_desc["Lakers -4.5 vs Suns"]["point"], -4.5)
        self.assertEqual(by_desc["Lakers/Suns over 215.5"]["line"], -115)
        self.assertEqual(len(by_desc["Lakers ML + Suns ML"]["legs"]), 2)
        self.assertEqual(by_desc["Lakers -4.5 vs Suns"]["created_at"], "2026-10-25T16:00:00Z")

        # --- grade ---
        from datetime import datetime, timezone
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for p in pending:
            result, net, note, game = grade_pick(to_grader(p), games_for, now)
            if game:
                store.set_game(self.db, p["id"], game)
            store.set_result(self.db, p["id"], result, net, note)
        results = {p["bet_description"]: (p["result"], p["units_net"]) for p in store.site_picks(self.db)}
        self.assertEqual(results["Lakers -4.5 vs Suns"], ("win", 1.818))
        self.assertEqual(results["Lakers/Suns over 215.5"][0], "win")
        self.assertEqual(results["Lakers ML + Suns ML"], ("loss", -1.0))
        self.assertEqual(results["LeBron over 27.5 points"][0], "manual")
        self.assertEqual(results["unknown"][0], "manual")
        spread = [p for p in store.site_picks(self.db) if p["bet_description"] == "Lakers -4.5 vs Suns"][0]
        self.assertEqual((spread["game_id"], spread["home_team"], spread["home_score"]), ("1", "Los Angeles Lakers", 112))
        self.assertEqual(spread["game_start"], "2026-10-25T23:30:00Z")
        self.assertIs(spread["posted_before_start"], True)
        self.assertEqual(spread["period"], "full_game")

        # --- hand grade ---
        prop = by_desc["LeBron over 27.5 points"]
        found = store.find_pick(self.db, prop["id"][:8])
        self.assertEqual([f["id"] for f in found], [prop["id"]])
        store.set_result(self.db, prop["id"], "win", 0.909, by_hand=True)
        self.assertIn("[graded by hand]", store.find_pick(self.db, prop["id"])[0]["note"])
        self.assertEqual(len(store.manual_picks(self.db)), 1)

        # --- standings view ---
        board = {r["x_handle"]: r for r in store.leaderboard(self.db)}
        s = board["SharpPickss"]
        self.assertEqual((s["wins"], s["losses"], s["needs_hand_grade"]), (3, 1, 1))
        self.assertAlmostEqual(s["units_won"], round(1.818 + 0.870 + 0.909 - 1.0, 2), places=1)

        # --- website ---
        from tracker.report import site_data, write_site
        data = site_data(self.db)
        self.assertEqual(len(data["picks"]), 5)
        self.assertEqual({a["h"] for a in data["accounts"]}, {"SharpPickss", "salbets_"})
        tmp = tempfile.mkdtemp()
        page = open(write_site(self.db, os.path.join(tmp, "index.html")), encoding="utf-8").read()
        self.assertIn("Lakers -4.5 vs Suns", page)

        # paused accounts drop off the site but keep their history
        store.set_active(self.db, "SharpPickss", False)
        self.assertEqual(site_data(self.db)["picks"], [])
        self.assertEqual(len(store.find_pick(self.db, prop["id"])), 1)

    def test_late_and_deleted(self):
        from tracker import xapi
        accts = self.seed()
        aid = accts["SharpPickss"]["id"]
        for pid, t in [("p1", "2026-10-25T16:00:00Z"), ("p2", "2026-10-25T16:05:00Z"), ("p3", "2026-10-26T00:30:00Z"),
                       ("p4", "2026-10-25T16:10:00Z")]:
            store.insert_post(self.db, pid, aid, t, "x", [], {})
        store.insert_pick(self.db, aid, "p1", "2026-10-25T16:00:00Z", {"sport": "NBA", "type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML"})
        store.insert_pick(self.db, aid, "p2", "2026-10-25T16:05:00Z", {"sport": "NBA", "type": "moneyline", "team": "Suns", "bet_description": "Suns ML"})
        store.insert_pick(self.db, aid, "p3", "2026-10-26T00:30:00Z", {"sport": "NBA", "type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML live"})
        store.insert_pick(self.db, aid, "p4", "2026-10-25T16:10:00Z", {"sport": "NBA", "type": "spread", "team": "Lakers", "point": -3.5,
                                                                       "period": "first_half", "bet_description": "Lakers 1H -3.5"})
        from datetime import datetime, timezone
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for p in store.pending_picks(self.db):
            result, net, note, game = grade_pick(to_grader(p), games_for, now)
            if game:
                store.set_game(self.db, p["id"], game)
            store.set_result(self.db, p["id"], result, net, note)
        by = {p["bet_description"]: p for p in store.site_picks(self.db)}
        self.assertEqual(by["Lakers 1H -3.5"]["result"], "manual")
        self.assertEqual(by["Lakers 1H -3.5"]["period"], "first_half")
        self.assertIs(by["Lakers ML live"]["posted_before_start"], False)

        # nothing is ready to check until the grades have settled for 12 hours
        self.assertEqual(store.posts_to_check_for_deletion(self.db), [])
        self.db.execute("UPDATE picks SET graded_at = now() - interval '1 day'")
        self.assertEqual(sorted(store.posts_to_check_for_deletion(self.db)), ["p1", "p2", "p3", "p4"])

        seen = []
        def fake_get(path, params):
            seen.append(params["ids"])
            return {"data": [{"id": "p1"}, {"id": "p4"}],
                    "errors": [{"value": "p2", "title": "Not Found Error", "resource_type": "tweet"},
                               {"value": "p3", "title": "Authorization Error", "resource_type": "tweet"}]}
        with mock.patch.object(xapi, "_get", fake_get):
            self.assertEqual(xapi.check_deletions(self.db), 1)
            self.assertEqual(xapi.check_deletions(self.db), 0)  # each post is only checked once
        self.assertEqual(len(seen), 1)
        by = {p["bet_description"]: p for p in store.site_picks(self.db)}
        self.assertTrue(by["Suns ML"]["deleted"])
        self.assertFalse(by["Lakers ML"]["deleted"])
        self.assertFalse(by["Lakers ML live"]["deleted"])  # unclear isn't counted as deleted

        board = {r["x_handle"]: r for r in store.leaderboard(self.db)}["SharpPickss"]
        # the live pick won but doesn't count; the deleted Suns loss still does
        self.assertEqual((board["wins"], board["losses"], board["late_picks"], board["deleted_picks"]), (1, 1, 1, 1))

        from tracker.report import site_data
        flags = {p["d"]: (p["late"], p["del"]) for p in site_data(self.db)["picks"]}
        self.assertEqual(flags["Lakers ML live"], (True, False))
        self.assertEqual(flags["Suns ML"], (False, True))

    def test_site_paging(self):
        accts = self.seed()
        aid = accts["SharpPickss"]["id"]
        for i in range(7):
            store.insert_pick(self.db, aid, None, f"2026-10-{10 + i:02d}T12:00:00Z",
                              {"sport": "NFL", "type": "moneyline", "bet_description": f"pick {i}"})
        # same timestamp twice to exercise the id tie-break
        store.insert_pick(self.db, aid, None, "2026-10-16T12:00:00Z", {"sport": "NFL", "type": "moneyline", "bet_description": "tie"})
        rows = store.site_picks(self.db, page_size=3)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({r["id"] for r in rows}), 8)
        self.assertEqual([r["created_at"] for r in rows], sorted((r["created_at"] for r in rows), reverse=True))


if __name__ == "__main__":
    unittest.main()
