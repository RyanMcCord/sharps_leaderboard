"""The Data API connector, with a fake AWS client (no AWS account needed)."""
import json
import unittest
from unittest import mock

from tracker.db import DataAPIDatabase, DatabaseError


class ResumingError(Exception):
    def __init__(self, code="DatabaseResumingException"):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class FakeClient:
    def __init__(self, fail_times=0, code="DatabaseResumingException"):
        self.calls, self.fail_times, self.code = [], fail_times, code

    def execute_statement(self, **req):
        self.calls.append(req)
        if len(self.calls) <= self.fail_times:
            raise ResumingError(self.code)
        return {"formattedRecords": json.dumps([{"x_handle": "SharpPickss", "n": 3}])}


def make(client):
    db = DataAPIDatabase.__new__(DataAPIDatabase)
    db.client = client
    db.base = {"resourceArn": "arn:cluster", "secretArn": "arn:secret", "database": "sharps"}
    return db


class DataAPITests(unittest.TestCase):
    def test_params_and_rows(self):
        c = FakeClient()
        rows = make(c).query("SELECT :a, :b, :c, :d, :e, :f", {"a": None, "b": True, "c": 3, "d": 1.5, "e": "x", "f": [1]})
        self.assertEqual(rows, [{"x_handle": "SharpPickss", "n": 3}])
        req = c.calls[0]
        self.assertEqual(req["formatRecordsAs"], "JSON")
        self.assertEqual(req["database"], "sharps")
        vals = {p["name"]: p["value"] for p in req["parameters"]}
        self.assertEqual(vals, {"a": {"isNull": True}, "b": {"booleanValue": True}, "c": {"longValue": 3},
                                "d": {"doubleValue": 1.5}, "e": {"stringValue": "x"}, "f": {"stringValue": "[1]"}})

    def test_waits_for_resume(self):
        c = FakeClient(fail_times=2)
        with mock.patch("tracker.db.time.sleep") as sleep:
            make(c).execute("SELECT 1")
        self.assertEqual(len(c.calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn("formatRecordsAs", c.calls[0])

    def test_unavailable_while_waking_is_retried(self):
        c = FakeClient(fail_times=1, code="DatabaseUnavailableException")
        with mock.patch("tracker.db.time.sleep"):
            make(c).execute("SELECT 1")
        self.assertEqual(len(c.calls), 2)

    def test_setup_errors_get_a_hint(self):
        for code, words in [("HttpEndpointNotEnabledException", "Actions > Enable RDS Data API"),
                            ("AccessDeniedException", "policy"),
                            ("DatabaseNotFoundException", "writer instance")]:
            c = FakeClient(fail_times=1, code=code)
            with mock.patch("tracker.db.time.sleep"), self.assertRaises(DatabaseError) as ctx:
                make(c).execute("SELECT 1")
            self.assertIn(words, str(ctx.exception))
            self.assertEqual(len(c.calls), 1)  # not retried

    def test_missing_database_hint(self):
        class Missing(FakeClient):
            def execute_statement(self, **req):
                self.calls.append(req)
                e = ResumingError("DatabaseErrorException")
                e.args = ('ERROR: database "sharps" does not exist',)
                raise e
        with self.assertRaises(DatabaseError) as ctx:
            make(Missing()).execute("SELECT 1")
        self.assertIn("Set DB_NAME", str(ctx.exception))

    def test_real_errors_are_not_retried(self):
        c = FakeClient(fail_times=1, code="BadRequestException")
        with mock.patch("tracker.db.time.sleep"), self.assertRaises(DatabaseError):
            make(c).execute("SELEC 1")
        self.assertEqual(len(c.calls), 1)

    def test_gives_up_eventually(self):
        c = FakeClient(fail_times=99)
        with mock.patch("tracker.db.time.sleep"), self.assertRaises(DatabaseError):
            make(c).execute("SELECT 1")
        self.assertEqual(len(c.calls), 8)


if __name__ == "__main__":
    unittest.main()
