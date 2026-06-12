"""Normalized records the pipeline stores.

These are backend-agnostic: the GitHub connector produces them, the storage
backend persists them. The raw `Event` is the firehose (repo, event_type,
timestamp, payload); the others are the derived tables the scorer queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Event:
    """Raw, append-only firehose row. Mirrors the spec's core schema:
    repo, event_type, timestamp, payload."""

    repo: str
    event_type: str  # "issue" | "pull_request" | "dependency" | "ci_run"
    timestamp: datetime
    payload: dict[str, Any]


@dataclass
class Issue:
    repo: str
    id: int
    state: str  # "open" | "closed"
    age_days: int
    labels: list[str] = field(default_factory=list)
    title: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Dep:
    repo: str
    name: str
    current_ver: str
    latest_ver: str
    outdated: bool
    ecosystem: str  # "npm" | "pypi"
    source_file: str  # "package.json" | "requirements.txt"


@dataclass
class CiRun:
    repo: str
    branch: str
    status: str  # "success" | "failure" | "cancelled"
    timestamp: datetime
    workflow: str = ""
