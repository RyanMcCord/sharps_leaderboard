"""PostLog adapter for Postgres (Aurora in production), in the bot's own schema (normally `ai_art`).

Works with any SqlDatabase adapter from xbots/adapters/database.py.
"""
import re

from ..domain.queue import POSTED, POSTING, SKIPPED
from ..ports import PostLog

DEFAULT_SCHEMA = "ai_art"

STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS {S}",
    f"""CREATE TABLE IF NOT EXISTS {{S}}.posts (
        image_id    text PRIMARY KEY,            -- the file's id in the queue folder
        file_name   text NOT NULL,
        status      text NOT NULL CHECK (status IN ('{POSTING}', '{POSTED}', '{SKIPPED}')),
        reason      text,                        -- why it was skipped
        caption     text,
        alt_text    text,
        x_post_id   text,
        started_at  timestamptz NOT NULL DEFAULT now(),
        posted_at   timestamptz
    )""",
    "CREATE INDEX IF NOT EXISTS posts_status ON {S}.posts (status)",
]


def _iso(col):
    return f"(to_json({col} AT TIME ZONE 'UTC') #>> '{{}}') || 'Z' AS {col}"


class PostgresPostLog(PostLog):
    def __init__(self, db, schema=DEFAULT_SCHEMA):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        self.db, self.schema = db, schema

    def _q(self, sql):
        return sql.replace("{S}", self.schema)

    def _execute(self, sql, params=None):
        self.db.execute(self._q(sql), params)

    def _query(self, sql, params=None):
        return self.db.query(self._q(sql), params)

    def setup(self):
        for statement in STATEMENTS:
            self._execute(statement)

    def handled_ids(self):
        return {r["image_id"] for r in self._query("SELECT image_id FROM {S}.posts")}

    def start(self, image, caption, alt_text):
        # A plain INSERT on purpose: if the image is already recorded (another run got there first),
        # this fails and nothing is posted.
        self._execute(f"INSERT INTO {{S}}.posts (image_id, file_name, status, caption, alt_text) "
                      f"VALUES (:id, :name, '{POSTING}', :caption, :alt)",
                      {"id": image.id, "name": image.name, "caption": caption, "alt": alt_text})

    def mark_posted(self, image_id, post_id):
        self._execute(f"UPDATE {{S}}.posts SET status = '{POSTED}', x_post_id = :post, posted_at = now() "
                      "WHERE image_id = :id", {"id": image_id, "post": post_id})

    def release(self, image_id):
        self._execute(f"DELETE FROM {{S}}.posts WHERE image_id = :id AND status = '{POSTING}'", {"id": image_id})

    def mark_skipped(self, image_id, name, reason):
        self._execute(f"INSERT INTO {{S}}.posts (image_id, file_name, status, reason) "
                      f"VALUES (:id, :name, '{SKIPPED}', :reason) ON CONFLICT (image_id) DO NOTHING",
                      {"id": image_id, "name": name, "reason": reason})

    def requeue(self, name_or_id):
        where = "WHERE image_id = :k OR lower(file_name) = lower(:k)"
        if not self._query(f"SELECT image_id FROM {{S}}.posts {where}", {"k": name_or_id}):
            return False
        self._execute(f"DELETE FROM {{S}}.posts {where}", {"k": name_or_id})
        return True

    def entries(self, status=None, limit=20):
        where = "WHERE status = :status" if status else ""
        params = {"n": limit, **({"status": status} if status else {})}
        return self._query(
            f"SELECT image_id, file_name, status, reason, caption, x_post_id, {_iso('started_at')}, "
            f"{_iso('posted_at')} FROM {{S}}.posts {where} ORDER BY started_at DESC, image_id LIMIT :n", params)

    def counts(self):
        rows = self._query("SELECT status, CAST(count(*) AS int) AS n FROM {S}.posts GROUP BY status")
        out = {POSTED: 0, SKIPPED: 0, POSTING: 0}
        out.update({r["status"]: int(r["n"]) for r in rows})
        return out
