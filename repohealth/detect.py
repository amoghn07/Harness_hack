"""Phase 2: detect — turn a low score into a concrete, actionable list.

The score answers "is this repo unhealthy?"; detection answers "*what* is wrong,
specifically?" — the exact stale issues to close and outdated deps to bump.

This is the hand-off payload for Phase 3 (act & publish): the orchestrator
feeds it to Bedrock for prioritization, and Composio executes against these
records (draft a PR per `StaleDep`, comment+label per `StaleIssue`).

Like the scorer, it reads only from the stored memory via SQL aggregation — no
live GitHub calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .scoring import HealthScore, stale_age_days
from .storage import Storage


@dataclass
class StaleIssue:
    id: int
    title: str
    age_days: int
    labels: list[str] = field(default_factory=list)


@dataclass
class OutdatedDep:
    name: str
    current_ver: str
    latest_ver: str
    ecosystem: str
    source_file: str

    @property
    def branch(self) -> str:
        """Branch name Phase 3 drafts the bump PR on (spec format)."""
        return f"bot/bump-{self.name}-{self.latest_ver}"


@dataclass
class Detection:
    repo: str
    score: int
    threshold: int
    needs_attention: bool          # score < threshold → escalate to Bedrock
    stale_issues: list[StaleIssue] = field(default_factory=list)
    outdated_deps: list[OutdatedDep] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.stale_issues and not self.outdated_deps


def _labels(raw) -> list[str]:
    """`labels` is stored as a JSON string (SQLite) or Array(String) (ClickHouse)."""
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw:
        import json

        try:
            return json.loads(raw)
        except ValueError:
            return [raw]
    return []


def detect(storage: Storage, health: HealthScore, threshold: int) -> Detection:
    """Collect the specific offenders behind a score."""
    repo = health.repo

    stale_rows = storage.query(
        "SELECT id, title, age_days, labels FROM issues "
        "WHERE repo=? AND state='open' AND age_days > ? "
        "ORDER BY age_days DESC",
        [repo, stale_age_days()],
    )
    stale_issues = [
        StaleIssue(id=int(r[0]), title=r[1] or "", age_days=int(r[2]),
                   labels=_labels(r[3]))
        for r in stale_rows
    ]

    dep_rows = storage.query(
        "SELECT name, current_ver, latest_ver, ecosystem, source_file FROM deps "
        "WHERE repo=? AND outdated=1 ORDER BY ecosystem, name",
        [repo],
    )
    outdated_deps = [
        OutdatedDep(name=r[0], current_ver=r[1] or "", latest_ver=r[2] or "",
                    ecosystem=r[3], source_file=r[4])
        for r in dep_rows
    ]

    return Detection(
        repo=repo,
        score=health.score,
        threshold=threshold,
        needs_attention=health.score < threshold,
        stale_issues=stale_issues,
        outdated_deps=outdated_deps,
    )
