"""Port for a SQL database. Adapters: Aurora with IAM sign-in, Aurora through the RDS Data API,
or any Postgres (see xbots/adapters/database.py)."""
from typing import Protocol


class SqlDatabase(Protocol):
    """SQL uses :name placeholders. Only SELECT statements return rows."""

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        ...

    def execute(self, sql: str, params: dict | None = None) -> None:
        ...
