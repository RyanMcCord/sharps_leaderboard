"""The IAM-token connection (tracker.db.IamPostgresDatabase).

psycopg and boto3 are replaced with fakes, so these run anywhere:
- the fake boto3 records the sign-in token requests
- the fake psycopg records how the connection was opened, can fail on purpose, and,
  when PG_TEST_PSQL is set, runs every query on a real Postgres through psql. That
  re-runs the whole Postgres suite through this connection class, including psycopg's
  %(name)s placeholder format.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import types
import unittest
from unittest import mock

from tracker import db as dbmod

PSQL = os.getenv("PG_TEST_PSQL")
HOST = "sharps-leaderboard.cluster-abc123xyz.us-east-2.rds.amazonaws.com"


class OperationalError(Exception):
    pass


class FakeCursor:
    PLACEHOLDER = re.compile(r"%%|%\((\w+)\)s")

    def __init__(self, conn):
        self.conn, self.rows = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @staticmethod
    def _lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        return "'" + str(v).replace("'", "''") + "'"   # psycopg sends str untyped, like a quoted literal

    def execute(self, sql, params):
        if self.conn.drop_next:
            self.conn.drop_next, self.conn.closed = False, True
            raise OperationalError("server closed the connection unexpectedly")
        sql = self.PLACEHOLDER.sub(lambda m: "%" if m.group(0) == "%%" else self._lit(params[m.group(1)]), sql)
        self.conn.statements.append(sql)
        if not PSQL:
            self.rows = []
            return
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            sql = f"WITH t AS ({sql}) SELECT coalesce(json_agg(t), '[]') FROM t"
        r = subprocess.run(["psql", *shlex.split(PSQL), "-AtqX", "-v", "ON_ERROR_STOP=1", "-c", sql],
                           capture_output=True, text=True)
        if r.returncode:
            raise Exception(r.stderr.strip())
        out = r.stdout.strip()
        self.rows = json.loads(out) if out.startswith("[") else []

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.closed, self.drop_next, self.statements = False, False, []

    def cursor(self):
        return FakeCursor(self)


class FakePsycopg:
    """Stands in for the psycopg module."""

    def __init__(self, failures=()):
        self.failures = list(failures)   # error messages to raise on the next connect() calls
        self.connects, self.conns = [], []
        self.OperationalError = OperationalError

    def connect(self, **kwargs):
        self.connects.append(kwargs)
        if self.failures:
            raise OperationalError(self.failures.pop(0))
        conn = FakeConn()
        self.conns.append(conn)
        return conn


class FakeRds:
    def __init__(self):
        self.tokens = []

    def generate_db_auth_token(self, **kw):
        self.tokens.append(kw)
        return f"token-{len(self.tokens)}"


def install(fake_psycopg, fake_rds):
    """Put the fakes where `import psycopg` / `import boto3` will find them."""
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    mod = types.ModuleType("psycopg")
    mod.connect, mod.OperationalError, mod.rows = fake_psycopg.connect, OperationalError, rows
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name, region_name=None: fake_rds
    return mock.patch.dict(sys.modules, {"psycopg": mod, "psycopg.rows": rows, "boto3": boto3})


class IamConnectionTests(unittest.TestCase):
    def setUp(self):
        self.pg, self.rds = FakePsycopg(), FakeRds()
        self.patch = install(self.pg, self.rds)
        self.patch.start()
        self.sleep = mock.patch("tracker.db.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.patch.stop()

    def test_signs_in_with_a_token_over_tls(self):
        db = dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
        self.assertEqual(db.region, "us-east-2")  # taken from the endpoint
        self.assertEqual(self.rds.tokens, [{"DBHostname": HOST, "Port": 5432, "DBUsername": "postgres", "Region": "us-east-2"}])
        kw = self.pg.connects[0]
        self.assertEqual((kw["host"], kw["user"], kw["password"], kw["dbname"]), (HOST, "postgres", "token-1", "postgres"))
        self.assertIn(kw["sslmode"], ("verify-full", "require"))
        self.assertTrue(kw["autocommit"])

    def test_waits_while_the_database_wakes_up(self):
        self.pg.failures = ["connection to server failed: timeout expired", "Connection timed out"]
        with mock.patch("builtins.print"):
            dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
        self.assertEqual(len(self.pg.connects), 3)
        self.assertEqual([t["DBHostname"] for t in self.rds.tokens], [HOST] * 3)  # fresh token each try

    def test_sign_in_problems_fail_fast_with_a_hint(self):
        self.pg.failures = ['FATAL:  PAM authentication failed for user "postgres"']
        with self.assertRaises(dbmod.DatabaseError) as ctx:
            dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
        self.assertEqual(len(self.pg.connects), 1)
        self.assertIn("rds-db:connect", str(ctx.exception))

    def test_wrong_database_name_hint(self):
        self.pg.failures = ['FATAL:  database "sharps" does not exist']
        with self.assertRaises(dbmod.DatabaseError) as ctx:
            dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "sharps")
        self.assertIn("set DB_NAME to postgres", str(ctx.exception))

    def test_bad_host_hint(self):
        self.pg.failures = ['could not translate host name "nope" to address: Name or service not known']
        with self.assertRaises(dbmod.DatabaseError) as ctx:
            dbmod.IamPostgresDatabase("nope", 5432, "postgres", "postgres", region="us-east-2")
        self.assertIn("DB_HOST", str(ctx.exception))

    def test_reconnects_once_if_the_connection_drops(self):
        db = dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
        db.conn.drop_next = True
        with mock.patch("builtins.print"):
            db.execute("SELECT 1")
        self.assertEqual(len(self.pg.connects), 2)
        self.assertEqual(self.pg.conns[1].statements, ["SELECT 1"])

    def test_placeholders(self):
        db = dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
        db.execute("SELECT :a, CAST(:b AS jsonb), 'abc' LIKE :c || '%', '12:30'", {"a": 5, "b": [1], "c": "ab"})
        self.assertEqual(db.conn.statements[-1], "SELECT 5, CAST('[1]' AS jsonb), 'abc' LIKE 'ab' || '%', '12:30'")

    def test_connect_picks_iam_when_db_host_is_set(self):
        with mock.patch.multiple("tracker.config", DB_HOST=HOST, DB_PORT=5432, DB_USER="postgres",
                                 DB_NAME="postgres", AWS_REGION="us-east-2"):
            self.assertIsInstance(dbmod.connect(), dbmod.IamPostgresDatabase)


if PSQL:
    from tests.test_postgres import PostgresTests

    class PostgresThroughIamTests(PostgresTests):
        """The full Postgres suite again, through IamPostgresDatabase and psycopg-style placeholders."""

        @classmethod
        def setUpClass(cls):
            cls._patch = install(FakePsycopg(), FakeRds())
            cls._patch.start()
            cls.db = dbmod.IamPostgresDatabase(HOST, 5432, "postgres", "postgres")
            cls.db.execute("DROP SCHEMA public CASCADE")
            cls.db.execute("CREATE SCHEMA public")
            dbmod.ensure_schema(cls.db)

        @classmethod
        def tearDownClass(cls):
            cls._patch.stop()


if __name__ == "__main__":
    unittest.main()
