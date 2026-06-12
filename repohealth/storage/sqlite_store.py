"""SQLite-backed mock store.

Stdlib-only, persists to a file, and speaks real SQL — so the Phase 2 scoring
queries can be written and tested for real before a ClickHouse instance exists.
The schema mirrors the spec's tables (events, issues, deps, ci_runs).

Ingest is idempotent per repo: each table is cleared for the target repo before
inserting, so re-running ingest reflects the current snapshot rather than
piling up duplicates. (The `events` firehose is append-only by nature, but we
scope deletes to the repo so multi-repo stores stay intact.)
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Sequence

from ..models import CiRun, Dep, Event, Issue
from .base import Storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    repo       TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp  TEXT,            -- nullable: dep-observation events have no natural time
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
    repo       TEXT NOT NULL,
    id         INTEGER NOT NULL,
    state      TEXT NOT NULL,
    age_days   INTEGER NOT NULL,
    labels     TEXT NOT NULL,
    title      TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS deps (
    repo        TEXT NOT NULL,
    name        TEXT NOT NULL,
    current_ver TEXT,
    latest_ver  TEXT,
    outdated    INTEGER NOT NULL,
    ecosystem   TEXT NOT NULL,
    source_file TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ci_runs (
    repo      TEXT NOT NULL,
    branch    TEXT NOT NULL,
    status    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    workflow  TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    repo      TEXT NOT NULL,
    score     INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);
"""


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


class SQLiteStorage(Storage):
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)

    def init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def insert_events(self, events: Sequence[Event]) -> None:
        self._conn.executemany(
            "INSERT INTO events (repo, event_type, timestamp, payload) "
            "VALUES (?, ?, ?, ?)",
            [(e.repo, e.event_type, _iso(e.timestamp), json.dumps(e.payload))
             for e in events],
        )
        self._conn.commit()

    def insert_issues(self, issues: Sequence[Issue]) -> None:
        self._replace_repo("issues", {i.repo for i in issues})
        self._conn.executemany(
            "INSERT INTO issues (repo, id, state, age_days, labels, title, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(i.repo, i.id, i.state, i.age_days, json.dumps(i.labels), i.title,
              _iso(i.created_at), _iso(i.updated_at)) for i in issues],
        )
        self._conn.commit()

    def insert_deps(self, deps: Sequence[Dep]) -> None:
        self._replace_repo("deps", {d.repo for d in deps})
        self._conn.executemany(
            "INSERT INTO deps (repo, name, current_ver, latest_ver, outdated, "
            "ecosystem, source_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(d.repo, d.name, d.current_ver, d.latest_ver, int(d.outdated),
              d.ecosystem, d.source_file) for d in deps],
        )
        self._conn.commit()

    def insert_ci_runs(self, runs: Sequence[CiRun]) -> None:
        self._replace_repo("ci_runs", {r.repo for r in runs})
        self._conn.executemany(
            "INSERT INTO ci_runs (repo, branch, status, timestamp, workflow) "
            "VALUES (?, ?, ?, ?, ?)",
            [(r.repo, r.branch, r.status, _iso(r.timestamp), r.workflow)
             for r in runs],
        )
        self._conn.commit()

    def record_score(self, repo: str, score: int, timestamp) -> None:
        # Append-only: the weekly report reads this back as the trend.
        self._conn.execute(
            "INSERT INTO scores (repo, score, timestamp) VALUES (?, ?, ?)",
            (repo, int(score), _iso(timestamp)),
        )
        self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        cur = self._conn.execute(sql, tuple(params or ()))
        return cur.fetchall()

    def close(self) -> None:
        self._conn.close()

    def _replace_repo(self, table: str, repos: set[str]) -> None:
        """Clear a derived table for the repos we're about to (re)insert."""
        for repo in repos:
            self._conn.execute(f"DELETE FROM {table} WHERE repo = ?", (repo,))
