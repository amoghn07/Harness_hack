"""Phase 1 pipeline: connector snapshot -> normalized records -> storage.

This is the only place that knows the end-to-end flow. It is backend-agnostic:
it takes a `GitHubConnector` and a `Storage`, and the `build_*` factories pick
mock vs. real implementations from config.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .connectors import GitHubConnector, RepoSnapshot
from .connectors.base import DepFile
from .models import Dep, Event
from .parsers import parse_package_json, parse_requirements_txt
from .registry import MockVersionRegistry, VersionRegistry
from .storage import Storage


@dataclass
class IngestResult:
    repo: str
    issues: int
    deps: int
    outdated_deps: int
    ci_runs: int
    events: int


def build_connector(cfg: Config) -> GitHubConnector:
    if cfg.github_backend == "mock":
        from .connectors.mock_github import MockGitHubConnector

        return MockGitHubConnector()
    if cfg.github_backend == "composio":
        from .connectors.composio_github import ComposioGitHubConnector

        return ComposioGitHubConnector(cfg.composio_api_key, cfg.composio_user_id)
    raise ValueError(f"Unknown github backend: {cfg.github_backend!r}")


def build_storage(cfg: Config) -> Storage:
    if cfg.store_backend == "sqlite":
        from .storage.sqlite_store import SQLiteStorage

        return SQLiteStorage(cfg.sqlite_path)
    if cfg.store_backend == "clickhouse":
        from .storage.clickhouse_store import ClickHouseStorage

        return ClickHouseStorage(
            cfg.clickhouse_host, cfg.clickhouse_port, cfg.clickhouse_user,
            cfg.clickhouse_password, cfg.clickhouse_database,
        )
    raise ValueError(f"Unknown store backend: {cfg.store_backend!r}")


def _resolve_deps(
    repo: str, dep_files: list[DepFile], registry: VersionRegistry
) -> list[Dep]:
    deps: list[Dep] = []
    for f in dep_files:
        pairs = (
            parse_package_json(f.content)
            if f.ecosystem == "npm"
            else parse_requirements_txt(f.content)
        )
        for name, current in pairs:
            latest = registry.latest(f.ecosystem, name) or current
            deps.append(
                Dep(
                    repo=repo,
                    name=name,
                    current_ver=current,
                    latest_ver=latest,
                    # Outdated only when we know both versions and they differ.
                    outdated=bool(current and latest and current != latest),
                    ecosystem=f.ecosystem,
                    source_file=f.path,
                )
            )
    return deps


def _snapshot_to_events(snap: RepoSnapshot, deps: list[Dep]) -> list[Event]:
    """Flatten the snapshot into the append-only firehose."""
    events: list[Event] = []
    for i in snap.issues:
        events.append(Event(snap.repo, "issue", i.created_at,
                             {"id": i.id, "state": i.state, "labels": i.labels}))
    for d in deps:
        # Deps carry no inherent timestamp; we stamp them below with the
        # snapshot's "as observed at" time.
        events.append(Event(snap.repo, "dependency", None,
                            {"name": d.name, "current": d.current_ver,
                             "latest": d.latest_ver, "outdated": d.outdated}))
    for r in snap.ci_runs:
        events.append(Event(snap.repo, "ci_run", r.timestamp,
                            {"branch": r.branch, "status": r.status}))
    # Deps have no natural timestamp; stamp them with the snapshot's newest CI
    # time (a reasonable "as observed at" marker) or fall back to None handling.
    observed = max((r.timestamp for r in snap.ci_runs), default=None)
    for e in events:
        if e.event_type == "dependency" and e.timestamp is None:
            e.timestamp = observed
    return events


def ingest_repo(
    repo: str,
    connector: GitHubConnector,
    storage: Storage,
    registry: VersionRegistry | None = None,
) -> IngestResult:
    registry = registry or MockVersionRegistry()
    storage.init_schema()

    snap = connector.fetch(repo)
    deps = _resolve_deps(repo, snap.dep_files, registry)
    events = _snapshot_to_events(snap, deps)

    storage.insert_issues(snap.issues)
    storage.insert_deps(deps)
    storage.insert_ci_runs(snap.ci_runs)
    storage.insert_events(events)

    return IngestResult(
        repo=repo,
        issues=len(snap.issues),
        deps=len(deps),
        outdated_deps=sum(1 for d in deps if d.outdated),
        ci_runs=len(snap.ci_runs),
        events=len(events),
    )
