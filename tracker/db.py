"""Database connection.

Three ways to connect, picked by which settings are present:
  DB_HOST                          Aurora, signed in with a temporary IAM token (express clusters)
  DB_CLUSTER_ARN + DB_SECRET_ARN   Aurora through the RDS Data API, password in Secrets Manager
  DATABASE_URL                     any Postgres, e.g. a local one

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

    # Errors that mean "the database is waking up from auto-pause or briefly busy, try again shortly"
    RETRYABLE = ("DatabaseResumingException", "DatabaseUnavailableException", "ServiceUnavailableError",
                 "InternalServerErrorException")

    # Plain-language hints for the setup mistakes these errors usually mean
    HINTS = {
        "HttpEndpointNotEnabledException": "The RDS Data API is turned off for this cluster. In the RDS console, "
                                           "open the cluster, then choose Actions > Enable RDS Data API.",
        "AccessDeniedException": "The AWS user isn't allowed to do this. Check its policy lists the right cluster "
                                 "and secret ARNs, and that AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are right.",
        "SecretsErrorException": "The Data API couldn't read the database password. Check DB_SECRET_ARN, and that "
                                 "the AWS user's policy allows secretsmanager:GetSecretValue on it.",
        "InvalidSecretException": "The database password secret isn't usable. It should be an 'RDS database' secret "
                                  "with the username and password.",
        "DatabaseNotFoundException": "The cluster has no database instance. Check DB_CLUSTER_ARN points at a cluster "
                                     "with a Serverless v2 writer instance.",
        "UnrecognizedClientException": "AWS doesn't recognise the access key. Check AWS_ACCESS_KEY_ID and "
                                       "AWS_SECRET_ACCESS_KEY.",
        "InvalidSignatureException": "AWS rejected the secret access key. Check AWS_SECRET_ACCESS_KEY.",
    }

    def __init__(self, cluster_arn, secret_arn, database, region=None):
        import boto3
        if not region:
            # The cluster ARN names its region (arn:aws:rds:us-east-2:...), so use that if none was set
            parts = cluster_arn.split(":")
            region = parts[3] if len(parts) > 3 else None
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
                    raise DatabaseError(self._explain(code, msg)) from e
                if attempt == 0:
                    print("  database is waking up, waiting...")
                time.sleep(delay)
                delay = min(delay * 2, 40)

    def _explain(self, code, msg):
        hint = self.HINTS.get(code)
        if not hint and "does not exist" in msg and "database" in msg:
            hint = (f"There's no database named '{self.base['database']}' in the cluster. Set DB_NAME to the "
                    "database's name (postgres if you skipped 'Initial database name' when creating it).")
        if not hint and ("password authentication failed" in msg or "28P01" in msg
                         or "PAM authentication failed" in msg):
            hint = ("The username or password in the DB_SECRET_ARN secret doesn't match the database. Set the master "
                    "password on the cluster (Modify > Self managed, Apply immediately) and save the same username "
                    "and password in that secret. If the password settings are greyed out, the cluster was made with "
                    "express configuration, which has no password: set DB_HOST to its writer endpoint instead "
                    "(see README, setup step 4).")
        if not hint and "resourceArn" in msg:
            hint = "DB_CLUSTER_ARN doesn't look right, or the cluster is in a different region from AWS_REGION."
        text = f"{code or 'Error'}: {msg[:400]}"
        return f"{text}\n    -> {hint}" if hint else text

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


class IamPostgresDatabase(PostgresDatabase):
    """Aurora signed in with a temporary IAM token instead of a password.

    This is how Aurora "express configuration" clusters work (the only kind on the AWS Free
    plan): the database has no password, and each connection presents a 15-minute token
    that boto3 signs with the AWS access key. The connection is TLS, checked against the
    server's certificate. A paused cluster wakes up when we connect, so connecting retries
    for a few minutes; a connection that drops mid-run is reopened with a fresh token.
    """

    WAKE_ATTEMPTS = 8

    def __init__(self, host, port, user, dbname, region=None):
        import boto3
        self.host, self.port, self.user, self.dbname = host, int(port), user, dbname
        if not region:
            # endpoints look like name.cluster-abc123.us-east-2.rds.amazonaws.com
            parts = host.split(".")
            region = parts[2] if len(parts) > 4 and parts[-2:] == ["amazonaws", "com"] else None
        self.region = region
        self.rds = boto3.client("rds", region_name=region)
        self.conn = None
        self._connect()

    def _ssl_args(self):
        try:
            import certifi
            return {"sslmode": "verify-full", "sslrootcert": certifi.where()}
        except ImportError:
            return {"sslmode": "require"}

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        delay = 5
        for attempt in range(self.WAKE_ATTEMPTS):
            token = self.rds.generate_db_auth_token(DBHostname=self.host, Port=self.port,
                                                    DBUsername=self.user, Region=self.region)
            try:
                self.conn = psycopg.connect(host=self.host, port=self.port, user=self.user, password=token,
                                            dbname=self.dbname, connect_timeout=30, autocommit=True,
                                            row_factory=dict_row, **self._ssl_args())
                return
            except psycopg.OperationalError as e:
                msg = str(e)
                if self._is_setup_error(msg) or attempt == self.WAKE_ATTEMPTS - 1:
                    raise DatabaseError(self._explain(msg)) from e
                if attempt == 0:
                    print("  database is waking up, waiting...")
                time.sleep(delay)
                delay = min(delay * 2, 40)

    @staticmethod
    def _is_setup_error(msg):
        m = msg.lower()
        return any(k in m for k in ("authentication failed", "does not exist", "could not translate host name",
                                    "name or service not known", "certificate verify failed", "no pg_hba.conf"))

    def _explain(self, msg):
        m = msg.lower()
        hint = None
        if "pam authentication failed" in m or "password authentication failed" in m:
            hint = (f"AWS didn't accept the sign-in token for user '{self.user}'. Check the AWS user's policy allows "
                    "rds-db:connect for this user, that AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are right, and that "
                    f"AWS_REGION matches the cluster's region ({self.region}).")
        elif "does not exist" in m and "database" in m:
            hint = (f"There's no database named '{self.dbname}'. Express clusters only have 'postgres', so set "
                    "DB_NAME to postgres.")
        elif "could not translate host name" in m or "name or service not known" in m:
            hint = "DB_HOST isn't a valid endpoint. Copy the cluster's writer endpoint from Connectivity & security."
        elif "timeout" in m or "timed out" in m or "could not connect" in m or "connection refused" in m:
            hint = ("Couldn't reach the database. Check DB_HOST is the cluster's writer endpoint and that the cluster "
                    "isn't stopped. Express clusters are reachable over the internet; other clusters may not be.")
        text = f"Could not connect to {self.host}: {msg.strip()[:400]}"
        return f"{text}\n    -> {hint}" if hint else text

    def _run(self, method, sql, params):
        import psycopg
        try:
            return getattr(PostgresDatabase, method)(self, sql, params)
        except psycopg.OperationalError:
            if self.conn is not None and not self.conn.closed:
                raise  # a real error on a live connection, not a dropped one
            print("  database connection dropped, reconnecting...")
            self._connect()
            return getattr(PostgresDatabase, method)(self, sql, params)

    def query(self, sql, params=None):
        return self._run("query", sql, params)

    def execute(self, sql, params=None):
        return self._run("execute", sql, params)


def connect():
    if config.DB_HOST:
        return IamPostgresDatabase(config.DB_HOST, config.DB_PORT, config.DB_USER, config.DB_NAME, config.AWS_REGION)
    if config.DB_CLUSTER_ARN and config.DB_SECRET_ARN:
        return DataAPIDatabase(config.DB_CLUSTER_ARN, config.DB_SECRET_ARN, config.DB_NAME, config.AWS_REGION)
    if config.DATABASE_URL:
        return PostgresDatabase(config.DATABASE_URL)
    raise DatabaseError("No database configured. Set DB_HOST (Aurora with IAM sign-in), DB_CLUSTER_ARN and "
                        "DB_SECRET_ARN (Aurora through the Data API), or DATABASE_URL (any Postgres) "
                        "as GitHub secrets or in .env.")


def ensure_schema(db):
    for statement in SCHEMA:
        db.execute(statement)
