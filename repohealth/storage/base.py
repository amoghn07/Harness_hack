"""Abstract storage — the agent's memory.

Phase 1 only needs to create the schema and append rows. Phase 2's scorer will
add read methods (or call `query()` directly); the SQL-aggregation contract from
the spec lives behind this interface so the scorer never knows whether it's
talking to SQLite or ClickHouse.
"""

from __future__ import annotations

import abc
from typing import Any, Sequence

from ..models import CiRun, Dep, Event, Issue


class Storage(abc.ABC):
    @abc.abstractmethod
    def init_schema(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        raise NotImplementedError

    @abc.abstractmethod
    def insert_events(self, events: Sequence[Event]) -> None: ...

    @abc.abstractmethod
    def insert_issues(self, issues: Sequence[Issue]) -> None: ...

    @abc.abstractmethod
    def insert_deps(self, deps: Sequence[Dep]) -> None: ...

    @abc.abstractmethod
    def insert_ci_runs(self, runs: Sequence[CiRun]) -> None: ...

    @abc.abstractmethod
    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        """Run a read query (used by Phase 2 scoring + the verify counts)."""
        raise NotImplementedError

    @abc.abstractmethod
    def record_score(self, repo: str, score: int, timestamp) -> None:
        """Append a health-score sample to the history.

        Unlike the derived tables, this is append-only — Phase 3's weekly report
        reads it back as the trend chart. `timestamp` is a datetime."""
        raise NotImplementedError

    def close(self) -> None:  # optional override
        pass
