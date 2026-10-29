"""The Postgres repository, and the use cases on top of it, against a real Postgres.

Runs only when PG_TEST_URL is set, e.g.
    PG_TEST_URL=postgresql://postgres@localhost/sharps_test python -m unittest tests.sharps_leaderboard.test_postgres_repository
or, without psycopg installed, PG_TEST_PSQL="<args for psql>" to go through the psql command line.
The database is wiped and rebuilt.
"""
import json
import os
import re
import shlex
import subprocess
import unittest
from datetime import datetime, timezone
from unittest import mock

from bots.sharps_leaderboard.adapters.postgres_repository import PostgresPickRepository
from bots.sharps_leaderboard.adapters.schema import STATEMENTS
from bots.sharps_leaderboard.application import Settings, SharpsLeaderboard
from bots.sharps_leaderboard.domain.picks import clean_pick
from xbots.adapters import database as dbmod
from xbots.adapters.x_api import XApiPostSource
from xbots.core import Post

from .fixtures import games_for
from .memory import CapturingSite, FakePickReader, FakePostSource, FakeScores

PSQL = os.getenv("PG_TEST_PSQL")
URL = os.getenv("PG_TEST_URL")


class PsqlDatabase:
    """Same interface as the classes in xbots/adapters/database.py, through the psql command line.

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


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def at(now):
    """Freeze 'now' for grading."""
    patcher = mock.patch("bots.sharps_leaderboard.domain.grading.datetime")
    dt = patcher.start()
    dt.now.return_value = now
    dt.fromisoformat = datetime.fromisoformat
    return patcher


@unittest.skipUnless(PSQL or URL, "set PG_TEST_URL or PG_TEST_PSQL to run Postgres tests")
class PostgresTests(unittest.TestCase):
    @classmethod
    def connect(cls):
        return make_db()

    @classmethod
    def setUpClass(cls):
        cls.db = cls.connect()
        cls.db.execute("DROP SCHEMA IF EXISTS sharps CASCADE")
        cls.db.execute("DROP SCHEMA public CASCADE")
        cls.db.execute("CREATE SCHEMA public")
        cls.repo = PostgresPickRepository(cls.db)
        cls.repo.setup([])
        cls.repo.setup([])  # safe to re-run

    def setUp(self):
        self.db.execute("TRUNCATE sharps.picks, sharps.posts, sharps.accounts CASCADE")

    def app(self, posts=None, reader=None, settings=None):
        return SharpsLeaderboard(repo=self.repo, posts=posts or FakePostSource(), reader=reader or FakePickReader(),
                                 scores=FakeScores(games_for), site=CapturingSite(),
                                 settings=settings or Settings(), log=lambda *_: None)

    def seed(self):
        self.repo.setup(["SharpPickss", "salbets_", "sharppickss"])  # duplicate in other case
        accts = {a["x_handle"]: a for a in self.repo.list_accounts()}
        self.repo.save_x_user("SharpPickss", "111", 128900)
        return accts

    def test_tables_live_in_their_own_schema(self):
        tables = self.db.query("SELECT table_schema, table_name FROM information_schema.tables "
                               "WHERE table_name IN ('accounts', 'posts', 'picks', 'leaderboard')")
        self.assertEqual({(t["table_schema"], t["table_name"]) for t in tables},
                         {("sharps", "accounts"), ("sharps", "posts"), ("sharps", "picks"), ("sharps", "leaderboard")})

    def test_accounts(self):
        accts = self.seed()
        self.assertEqual(sorted(accts, key=str.lower), ["salbets_", "SharpPickss"])
        a = accts["SharpPickss"]
        self.assertRegex(a["id"], r"^[0-9a-f-]{36}$")
        self.assertTrue(a["active"])
        self.assertTrue(a["created_at"].endswith("Z"))
        self.repo.add_account("new_one")
        self.assertTrue(self.repo.set_active("SALBETS_", False))
        self.assertFalse(self.repo.set_active("nobody", False))
        active = [a["x_handle"] for a in self.repo.list_accounts()]
        self.assertEqual(sorted(active, key=str.lower), ["new_one", "SharpPickss"])
        self.repo.setup(["salbets_"])  # re-seeding doesn't un-pause
        self.assertNotIn("salbets_", [a["x_handle"] for a in self.repo.list_accounts()])
        full = {a["x_handle"]: a for a in self.repo.list_accounts(active_only=False)}
        self.assertEqual(full["SharpPickss"]["followers"], 128900)
        self.assertEqual(full["SharpPickss"]["x_user_id"], "111")

    def test_full_pipeline(self):
        self.seed()

        # --- fetch, through the real X adapter with a fake HTTP layer ---
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

        def fake_get(path, params, token=None):
            calls.append((path, dict(params)))
            if path == "/users/by":
                return {"data": [{"id": "222", "username": "salbets_", "public_metrics": {"followers_count": 11900}}]}
            return timeline if path == "/users/111/tweets" else {"meta": {"result_count": 0}}

        multi_pick = [clean_pick(p) for p in [
            {"sport": "nba", "type": "spread", "team": "Lakers", "opponent": "Suns", "point": -4.5, "units": 2,
             "bet_description": "Lakers -4.5 vs Suns"},
            {"sport": "NBA", "type": "over_under", "team": "Lakers", "opponent": "Suns", "side": "over",
             "point": 215.5, "line": -115, "bet_description": "Lakers/Suns over 215.5"},
            {"sport": "NBA", "type": "prop", "side": "over", "point": 27.5, "bet_description": "LeBron over 27.5 points"},
            {"sport": "Cricket", "type": "weird", "bet_description": "unknown"},
        ]]
        parlay = [clean_pick({"sport": "NBA", "type": "parlay", "line": 264, "bet_description": "Lakers ML + Suns ML",
                              "legs": [{"type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML"},
                                       {"type": "moneyline", "team": "Suns", "bet_description": "Suns ML"}]})]
        reader = FakePickReader({"Lakers -4.5 (2u) and over 215.5 LAL/PHX": multi_pick, "🔒": parlay})
        app = self.app(posts=XApiPostSource(get=fake_get), reader=reader)

        self.assertEqual(app.fetch_posts(), 3)
        self.assertEqual(app.fetch_posts(), 3)  # same fake response; posts dedupe
        second_call = [p for path, p in calls if path == "/users/111/tweets"][-1]
        self.assertEqual(second_call["since_id"], "1850000000000000003")
        self.assertEqual(second_call["max_results"], 100)
        unparsed = {p["id"]: p for p in self.repo.unparsed_posts()}
        self.assertEqual(len(unparsed), 3)
        self.assertEqual(unparsed["1850000000000000001"]["media_urls"], ["https://pbs.twimg.com/a.jpg"])

        # --- read picks ---
        self.assertEqual(app.read_picks(), 5)
        self.assertEqual(self.repo.unparsed_posts(), [])
        self.assertNotIn("1850000000000000002", reader.read_posts)  # "good morning" skipped without Claude
        pending = self.repo.pending_picks()
        self.assertEqual(len(pending), 5)
        by_desc = {p["bet_description"]: p for p in pending}
        self.assertEqual((by_desc["unknown"]["sport"], by_desc["unknown"]["type"]), ("OTHER", "other"))
        self.assertEqual(by_desc["Lakers -4.5 vs Suns"]["point"], -4.5)
        self.assertEqual(by_desc["Lakers/Suns over 215.5"]["line"], -115)
        self.assertEqual(len(by_desc["Lakers ML + Suns ML"]["legs"]), 2)
        self.assertEqual(by_desc["Lakers -4.5 vs Suns"]["created_at"], "2026-10-25T16:00:00Z")

        # --- grade ---
        clock = at(NOW)
        try:
            app.grade()
        finally:
            clock.stop()
        results = {p["bet_description"]: (p["result"], p["units_net"]) for p in self.repo.all_picks()}
        self.assertEqual(results["Lakers -4.5 vs Suns"], ("win", 1.818))
        self.assertEqual(results["Lakers/Suns over 215.5"][0], "win")
        self.assertEqual(results["Lakers ML + Suns ML"], ("loss", -1.0))
        self.assertEqual(results["LeBron over 27.5 points"][0], "manual")
        self.assertEqual(results["unknown"][0], "manual")
        spread = [p for p in self.repo.all_picks() if p["bet_description"] == "Lakers -4.5 vs Suns"][0]
        self.assertEqual((spread["game_id"], spread["home_team"], spread["home_score"]), ("1", "Los Angeles Lakers", 112))
        self.assertEqual(spread["game_start"], "2026-10-25T23:30:00Z")
        self.assertIs(spread["posted_before_start"], True)
        self.assertEqual(spread["period"], "full_game")

        # --- hand grade ---
        prop = by_desc["LeBron over 27.5 points"]
        pick, net = app.grade_by_hand(prop["id"][:8], "win")
        self.assertEqual(pick["id"], prop["id"])
        self.assertIn("[graded by hand]", self.repo.find_pick(prop["id"])[0]["note"])
        self.assertEqual(len(self.repo.manual_picks()), 1)

        # --- standings view ---
        board = {r["x_handle"]: r for r in app.standings()}["SharpPickss"]
        self.assertEqual((board["wins"], board["losses"], board["needs_hand_grade"]), (3, 1, 1))
        self.assertAlmostEqual(board["units_won"], round(1.818 + 0.870 + 0.909 - 1.0, 2), places=1)

        # --- website ---
        app.publish()
        data = app.site.published[-1]
        self.assertEqual(len(data["picks"]), 5)
        self.assertEqual({a["h"] for a in data["accounts"]}, {"SharpPickss", "salbets_"})

        # paused accounts drop off the site but keep their history
        app.set_account_active("SharpPickss", False)
        self.assertEqual(app.site_data()["picks"], [])
        self.assertEqual(len(self.repo.find_pick(prop["id"])), 1)

    def test_late_and_deleted(self):
        accts = self.seed()
        aid = accts["SharpPickss"]["id"]
        for pid, t in [("p1", "2026-10-25T16:00:00Z"), ("p2", "2026-10-25T16:05:00Z"), ("p3", "2026-10-26T00:30:00Z"),
                       ("p4", "2026-10-25T16:10:00Z")]:
            self.repo.save_post(aid, Post(id=pid, created_at=t, text="x"))
        add = self.repo.add_pick
        add(aid, "p1", "2026-10-25T16:00:00Z", {"sport": "NBA", "type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML"})
        add(aid, "p2", "2026-10-25T16:05:00Z", {"sport": "NBA", "type": "moneyline", "team": "Suns", "bet_description": "Suns ML"})
        add(aid, "p3", "2026-10-26T00:30:00Z", {"sport": "NBA", "type": "moneyline", "team": "Lakers", "bet_description": "Lakers ML live"})
        add(aid, "p4", "2026-10-25T16:10:00Z", {"sport": "NBA", "type": "spread", "team": "Lakers", "point": -3.5,
                                                "period": "first_half", "bet_description": "Lakers 1H -3.5"})
        x = FakePostSource(deleted={"p2"})
        app = self.app(posts=x)
        clock = at(NOW)
        try:
            app.grade()
        finally:
            clock.stop()
        by = {p["bet_description"]: p for p in self.repo.all_picks()}
        self.assertEqual((by["Lakers 1H -3.5"]["result"], by["Lakers 1H -3.5"]["period"]), ("manual", "first_half"))
        self.assertIs(by["Lakers ML live"]["posted_before_start"], False)

        # nothing is ready to check until the grades have settled for 12 hours
        self.assertEqual(self.repo.posts_to_check_for_deletion(), [])
        self.db.execute("UPDATE sharps.picks SET graded_at = now() - interval '1 day'")
        self.assertEqual(sorted(self.repo.posts_to_check_for_deletion()), ["p1", "p2", "p3", "p4"])
        self.assertEqual(app.check_deletions(), 1)
        self.assertEqual(app.check_deletions(), 0)  # each post is only checked once
        self.assertEqual(len([c for c in x.calls if c[0] == "find_deleted"]), 1)
        by = {p["bet_description"]: p for p in self.repo.all_picks()}
        self.assertTrue(by["Suns ML"]["deleted"])
        self.assertFalse(by["Lakers ML"]["deleted"])

        board = {r["x_handle"]: r for r in app.standings()}["SharpPickss"]
        # the live pick won but doesn't count; the deleted Suns loss still does
        self.assertEqual((board["wins"], board["losses"], board["late_picks"], board["deleted_picks"]), (1, 1, 1, 1))
        flags = {p["d"]: (p["late"], p["del"]) for p in app.site_data()["picks"]}
        self.assertEqual(flags["Lakers ML live"], (True, False))
        self.assertEqual(flags["Suns ML"], (False, True))

    def test_site_paging(self):
        accts = self.seed()
        aid = accts["SharpPickss"]["id"]
        for i in range(7):
            self.repo.add_pick(aid, None, f"2026-10-{10 + i:02d}T12:00:00Z",
                               {"sport": "NFL", "type": "moneyline", "bet_description": f"pick {i}"})
        # same timestamp twice to exercise the id tie-break
        self.repo.add_pick(aid, None, "2026-10-16T12:00:00Z", {"sport": "NFL", "type": "moneyline", "bet_description": "tie"})
        rows = self.repo.all_picks(page_size=3)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({r["id"] for r in rows}), 8)
        self.assertEqual([r["created_at"] for r in rows], sorted((r["created_at"] for r in rows), reverse=True))

    def test_zz_moves_existing_data_out_of_public(self):
        """A database from before the multi-bot layout (tables in `public`) is moved into `sharps` intact."""
        self.db.execute("DROP SCHEMA sharps CASCADE")
        for statement in STATEMENTS[2:]:              # the old layout: same tables, in public
            self.db.execute(statement.replace("{S}", "public"))
        old = PostgresPickRepository(self.db, schema="public")
        old.setup(["SharpPickss"])
        aid = old.list_accounts()[0]["id"]
        old.save_post(aid, Post(id="p1", created_at="2026-10-25T16:00:00Z", text="Lakers ML"))
        old.add_pick(aid, "p1", "2026-10-25T16:00:00Z", {"sport": "NBA", "type": "moneyline", "team": "Lakers",
                                                         "bet_description": "Lakers ML"})
        old_pick = old.all_picks()[0]

        self.repo.setup(["salbets_"])   # what the next daily run does

        moved = self.repo.all_picks()
        self.assertEqual([(p["id"], p["bet_description"], p["x_handle"]) for p in moved],
                         [(old_pick["id"], "Lakers ML", "SharpPickss")])
        self.assertEqual({a["x_handle"] for a in self.repo.list_accounts()}, {"SharpPickss", "salbets_"})
        left = self.db.query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        self.assertEqual(left, [])
        types = self.db.query("SELECT n.nspname AS schema FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                              "WHERE t.typname IN ('sport', 'bet_type', 'pick_result', 'bet_period')")
        self.assertEqual({t["schema"] for t in types}, {"sharps"})
        # and it keeps working: new picks, grading, the standings view
        self.repo.add_pick(aid, "p1", "2026-10-25T16:00:00Z", {"sport": "NBA", "type": "spread", "team": "Suns",
                                                               "point": 3.5, "bet_description": "Suns +3.5"})
        clock = at(NOW)
        try:
            self.app().grade()
        finally:
            clock.stop()
        board = {r["x_handle"]: r for r in self.repo.standings()}["SharpPickss"]
        self.assertEqual((board["wins"], board["losses"]), (1, 1))
        self.repo.setup([])  # and setup stays safe to re-run afterwards


if __name__ == "__main__":
    unittest.main()
