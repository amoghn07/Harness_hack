"""Real GitHub ingest via Composio (composio SDK 1.x).

Pulls the same RepoSnapshot the mock produces, but from live GitHub through
Composio's tool-execution API. Requires a connected GitHub account on the
Composio project (OAuth) — see `repohealth.connect` for the link flow.

Action slugs (verified against the live toolkit):
  GITHUB_LIST_REPOSITORY_ISSUES            -> issues (state=all)
  GITHUB_FIND_PULL_REQUESTS                -> open PR count
  GITHUB_GET_REPOSITORY_CONTENT            -> package.json / requirements.txt
  GITHUB_LIST_WORKFLOW_RUNS_FOR_A_REPOSITORY -> recent CI runs
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from typing import Any

from ..models import CiRun, Issue
from .base import DepFile, GitHubConnector, RepoSnapshot

_DEP_PATHS = [("package.json", "npm"), ("requirements.txt", "pypi")]
# GitHub workflow-run conclusions we treat as a red build.
_FAILURE_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_list(data: Any, *keys: str) -> list[dict]:
    """Composio wraps GitHub responses inconsistently across actions: sometimes
    the array is at `data` directly, sometimes under `details`/`items`/a named
    key. Normalize to a list of dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in (*keys, "details", "items", "data", "response_data"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        # Single object -> wrap.
        if any(k in data for k in ("number", "name", "id")):
            return [data]
    return []


class ComposioGitHubConnector(GitHubConnector):
    def __init__(self, api_key: str, user_id: str, now: datetime | None = None) -> None:
        if not api_key:
            raise ValueError("COMPOSIO_API_KEY is required for the Composio connector.")
        try:
            from composio import Composio  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ImportError(
                "composio is not installed. `pip install composio` or use "
                "REPOHEALTH_GITHUB=mock."
            ) from exc
        self._client = Composio(api_key=api_key)
        self._user_id = user_id
        self.now = now or datetime.now(timezone.utc)
        # Manual execution requires an explicit toolkit version ("latest" is
        # rejected). Resolve the current one once; allow an env override pin.
        self._version = os.getenv("COMPOSIO_GITHUB_VERSION") or self._resolve_version()

    def _resolve_version(self) -> str | None:
        try:
            return self._client.toolkits.get("github").meta.version
        except Exception:  # pragma: no cover - network/SDK shape drift
            return None

    def _exec(self, slug: str, **arguments: Any) -> Any:
        kwargs: dict[str, Any] = {"arguments": arguments, "user_id": self._user_id}
        if self._version:
            kwargs["version"] = self._version
        else:
            kwargs["dangerously_skip_version_check"] = True
        resp = self._client.tools.execute(slug, **kwargs)
        # The SDK returns a plain dict: {"data": ..., "error": ..., "successful": bool}
        if isinstance(resp, dict):
            if not resp.get("successful", True):
                raise RuntimeError(f"{slug} failed: {resp.get('error')}")
            return resp.get("data")
        # Defensive fallback if a future SDK returns an object instead.
        if not getattr(resp, "successful", True):
            raise RuntimeError(f"{slug} failed: {getattr(resp, 'error', resp)}")
        return getattr(resp, "data", resp)

    def fetch(self, repo: str) -> RepoSnapshot:
        owner, name = repo.split("/", 1)

        issues = self._fetch_issues(repo, owner, name)
        open_prs = self._fetch_open_pr_count(owner, name)
        dep_files = self._fetch_dep_files(owner, name)
        ci_runs = self._fetch_ci_runs(repo, owner, name)

        return RepoSnapshot(
            repo=repo,
            issues=issues,
            open_pr_count=open_prs,
            dep_files=dep_files,
            ci_runs=ci_runs,
        )

    def _fetch_issues(self, repo: str, owner: str, name: str) -> list[Issue]:
        data = self._exec(
            "GITHUB_LIST_REPOSITORY_ISSUES",
            owner=owner, repo=name, state="all", per_page=100,
        )
        out: list[Issue] = []
        for raw in _as_list(data, "issues"):
            # The issues endpoint also returns PRs; skip them.
            if raw.get("pull_request"):
                continue
            created = _parse_dt(raw.get("created_at"))
            age = (self.now - created).days if created else 0
            out.append(
                Issue(
                    repo=repo,
                    id=raw.get("number", 0),
                    state=raw.get("state", "open"),
                    age_days=age,
                    labels=[
                        (lbl.get("name") if isinstance(lbl, dict) else lbl)
                        for lbl in (raw.get("labels") or [])
                    ],
                    title=raw.get("title", ""),
                    created_at=created,
                    updated_at=_parse_dt(raw.get("updated_at")),
                )
            )
        return out

    def _fetch_open_pr_count(self, owner: str, name: str) -> int:
        data = self._exec(
            "GITHUB_FIND_PULL_REQUESTS",
            owner=owner, repo=name, state="open", per_page=100,
        )
        if isinstance(data, dict) and isinstance(data.get("total_count"), int):
            return data["total_count"]
        return len(_as_list(data, "pull_requests", "items"))

    def _fetch_dep_files(self, owner: str, name: str) -> list[DepFile]:
        files: list[DepFile] = []
        for path, ecosystem in _DEP_PATHS:
            try:
                data = self._exec(
                    "GITHUB_GET_REPOSITORY_CONTENT", owner=owner, repo=name, path=path
                )
            except RuntimeError:
                continue  # file absent in this repo
            content = self._decode_content(data)
            if content:
                files.append(DepFile(path=path, ecosystem=ecosystem, content=content))
        return files

    @staticmethod
    def _decode_content(data: Any) -> str | None:
        node = data
        if isinstance(data, dict):
            # content may be nested under a wrapper key
            node = data.get("content") if "content" in data else data
            if isinstance(node, dict):
                data = node
        if isinstance(data, dict) and "content" in data:
            raw, enc = data.get("content"), data.get("encoding")
            if enc == "base64" and isinstance(raw, str):
                return base64.b64decode(raw).decode("utf-8", errors="replace")
            if isinstance(raw, str):
                return raw
        if isinstance(node, str):
            return node
        return None

    def _fetch_ci_runs(self, repo: str, owner: str, name: str) -> list[CiRun]:
        data = self._exec(
            "GITHUB_LIST_WORKFLOW_RUNS_FOR_A_REPOSITORY",
            owner=owner, repo=name, per_page=30,
        )
        out: list[CiRun] = []
        for raw in _as_list(data, "workflow_runs"):
            conclusion = (raw.get("conclusion") or raw.get("status") or "").lower()
            status = "failure" if conclusion in _FAILURE_CONCLUSIONS else (
                "success" if conclusion in ("success", "completed") else conclusion
            )
            out.append(
                CiRun(
                    repo=repo,
                    branch=raw.get("head_branch") or "",
                    status=status or "unknown",
                    timestamp=_parse_dt(raw.get("created_at")) or self.now,
                    workflow=raw.get("name") or "",
                )
            )
        return out
