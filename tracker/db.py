"""Database connection.

Everything talks to the database through two methods:

    db.query(sql, params)    -> list of dict rows
    db.execute(sql, params)  -> None

SQL uses :name placeholders and explicit CAST(...) for uuid, timestamps, enums and
jsonb, so the same statements work through the RDS Data API (production, Aurora)
and through a direct Postgres connection (local development and tests).
"""
import json
import time
from datetime import date, datetime

from . import config
from .schema import SCHEMA


class DatabaseError(RuntimeError):
    pass


def _jsonable(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


class DataAPIDatabase:
    """Aurora through the RDS Data API: HTTPS + IAM, no open database port."""

    # Errors that mean "the cluster is waking up from auto-pause, try again shortly"
    RETRYABLE = ("DatabaseResumingException", "DatabaseNotFoundException", "ServiceUnavailableError",
                 "StatementTimeoutException")

    def __init__(self, cluster_arn, secret_arn, database, region=None):
        import boto3
        self.client = boto3.client("rds-data", region_name=region or None)
        self.base = {"resourceArn": cluster_arn, "secretArn": secret_arn, "database": database}

    @staticmethod
    def _param(name, value):
        value = _jsonable(value)
        if value is None:
            v = {"isNull": True}
        elif isinstance(value, bool):
            v = {"booleanValue": value}
        elif isinstance(value, int):
            v = {"longValue": value}
        elif isinstance(value, float):
            v = {"doubleValue": value}
        elif isinstance(value, (dict, list)):
            v = {"stringValue": json.dumps(value, ensure_ascii=False)}
        else:
            v = {"stringValue": str(value)}
        return {"name": name, "value": v}

    def _call(self, sql, params, want_rows):
        req = dict(self.base, sql=sql, parameters=[self._param(k, v) for k, v in (params or {}).items()])
        if want_rows:
            req["formatRecordsAs"] = "JSON"
        delay = 5
        for attempt in range(8):  # about 3 minutes in total, enough for a cold resume
            try:
                return self.client.execute_statement(**req)
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                msg = str(e)
                waking = code in self.RETRYABLE or "resum" in msg.lower() or "Communications link failure" in msg
                if not waking or attempt == 7:
                    raise DatabaseError(f"{code or type(e).__name__}: {msg[:400]}") from e
                if attempt == 0:
                    print("  database is waking up, waiting...")
                time.sleep(delay)
                delay = min(delay * 2, 40)

    def query(self, sql, params=None):
        resp = self._call(sql, params, want_rows=True)
        return json.loads(resp.get("formattedRecords") or "[]")

    def execute(self, sql, params=None):
        self._call(sql, params, want_rows=False)


class PostgresDatabase:
    """Direct connection with psycopg (pip install "psycopg[binary]")."""

    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    @staticmethod
    def _convert(sql, params):
        import re
        params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else _jsonable(v)) for k, v in (params or {}).items()}
        return re.sub(r"(?<![:\w]):([A-Za-z_]\w*)", r"%(\1)s", sql.replace("%", "%%")), params

    def query(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(*self._convert(sql, params))
            return [{k: _jsonable(v) for k, v in row.items()} for row in cur.fetchall()]

    def execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(*self._convert(sql, params))


def connect():
    if config.DB_CLUSTER_ARN and config.DB_SECRET_ARN:
        return DataAPIDatabase(config.DB_CLUSTER_ARN, config.DB_SECRET_ARN, config.DB_NAME, config.AWS_REGION)
    if config.DATABASE_URL:
        return PostgresDatabase(config.DATABASE_URL)
    raise DatabaseError("No database configured. Set DB_CLUSTER_ARN and DB_SECRET_ARN (Aurora) "
                        "or DATABASE_URL (any Postgres) in your .env or GitHub secrets.")


def ensure_schema(db):
    for statement in SCHEMA:
        db.execute(statement)
