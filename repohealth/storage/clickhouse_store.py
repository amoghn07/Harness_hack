"""ClickHouse-backed store — the production memory.

Guided stub. The schema below is the ClickHouse-dialect mirror of the SQLite
mock (MergeTree engines, native types). Insert methods follow the same shape as
the SQLite store using clickhouse-connect's `insert`. Flip REPOHEALTH_STORE=
clickhouse to use this once a server is reachable.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..models import CiRun, Dep, Event, Issue
from .base import Storage

# ClickHouse DDL — note ReplacingMergeTree for the derived tables so re-ingesting
# a repo's snapshot collapses to the latest rows rather than duplicating.
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS events (
        repo String, event_type String, timestamp DateTime, payload String
    ) ENGINE = MergeTree ORDER BY (repo, timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS issues (
        repo String, id UInt64, state String, age_days UInt32,
        labels Array(String), title String,
        created_at Nullable(DateTime), updated_at Nullable(DateTime),
        _ingested DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(_ingested) ORDER BY (repo, id)
    """,
    """
    CREATE TABLE IF NOT EXISTS deps (
        repo String, name String, current_ver String, latest_ver String,
        outdated UInt8, ecosystem String, source_file String,
        _ingested DateTime DEFAULT now()
    ) ENGINE = ReplacingMergeTree(_ingested) ORDER BY (repo, name, source_file)
    """,
    """
    CREATE TABLE IF NOT EXISTS ci_runs (
        repo String, branch String, status String, timestamp DateTime,
        workflow String
    ) ENGINE = MergeTree ORDER BY (repo, timestamp)
    """,
]


class ClickHouseStorage(Storage):  # pragma: no cover - needs a server
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str) -> None:
        try:
            import clickhouse_connect  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "clickhouse-connect is not installed. `pip install "
                "clickhouse-connect` or use REPOHEALTH_STORE=sqlite."
            ) from exc
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=password,
            database=database,
        )

    def init_schema(self) -> None:
        for ddl in _DDL:
            self._client.command(ddl)

    def insert_events(self, events: Sequence[Event]) -> None:
        self._client.insert(
            "events",
            [[e.repo, e.event_type, e.timestamp, __import__("json").dumps(e.payload)]
             for e in events],
            column_names=["repo", "event_type", "timestamp", "payload"],
        )

    def insert_issues(self, issues: Sequence[Issue]) -> None:
        self._client.insert(
            "issues",
            [[i.repo, i.id, i.state, i.age_days, i.labels, i.title,
              i.created_at, i.updated_at] for i in issues],
            column_names=["repo", "id", "state", "age_days", "labels", "title",
                          "created_at", "updated_at"],
        )

    def insert_deps(self, deps: Sequence[Dep]) -> None:
        self._client.insert(
            "deps",
            [[d.repo, d.name, d.current_ver, d.latest_ver, int(d.outdated),
              d.ecosystem, d.source_file] for d in deps],
            column_names=["repo", "name", "current_ver", "latest_ver",
                          "outdated", "ecosystem", "source_file"],
        )

    def insert_ci_runs(self, runs: Sequence[CiRun]) -> None:
        self._client.insert(
            "ci_runs",
            [[r.repo, r.branch, r.status, r.timestamp, r.workflow] for r in runs],
            column_names=["repo", "branch", "status", "timestamp", "workflow"],
        )

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        # clickhouse-connect uses %s-style or named params; adapt as needed.
        return self._client.query(sql).result_rows
