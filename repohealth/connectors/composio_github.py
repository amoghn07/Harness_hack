"""Real GitHub ingest via Composio — the production replacement for the mock.

Left as a guided stub: the structure and the exact Composio actions to call are
spelled out so wiring it up is mechanical once you have an API key + a connected
GitHub account. Phase 1 ships with the mock; flip REPOHEALTH_GITHUB=composio to
use this.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Issue
from .base import DepFile, GitHubConnector, RepoSnapshot


class ComposioGitHubConnector(GitHubConnector):
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError(
                "COMPOSIO_API_KEY is required for the Composio GitHub connector."
            )
        self.api_key = api_key
        # Lazy import so the mock path needs nothing installed.
        try:
            from composio import ComposioToolSet  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise ImportError(
                "composio-core is not installed. `pip install composio-core` "
                "or use REPOHEALTH_GITHUB=mock."
            ) from exc
        self._toolset = ComposioToolSet(api_key=api_key)

    def fetch(self, repo: str) -> RepoSnapshot:  # pragma: no cover - needs creds
        owner, name = repo.split("/", 1)

        # The Composio actions to call (names per the GitHub connector):
        #   GITHUB_LIST_REPOSITORY_ISSUES   -> issues (filter state=all)
        #   GITHUB_LIST_PULL_REQUESTS       -> open PR count (state=open)
        #   GITHUB_GET_REPOSITORY_CONTENT   -> package.json / requirements.txt
        #   GITHUB_LIST_WORKFLOW_RUNS       -> recent CI runs
        #
        # Each returns JSON; map it onto the models below exactly as the mock
        # connector does. Sketch:
        #
        # raw_issues = self._toolset.execute_action(
        #     action="GITHUB_LIST_REPOSITORY_ISSUES",
        #     params={"owner": owner, "repo": name, "state": "all", "per_page": 100},
        # )
        # issues = [self._to_issue(repo, r) for r in raw_issues["data"]]

        raise NotImplementedError(
            "Composio ingest is stubbed for Phase 1. See inline notes for the "
            "exact actions to wire, then map responses onto RepoSnapshot."
        )

    @staticmethod
    def _to_issue(repo: str, raw: dict) -> Issue:  # pragma: no cover
        created = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - created).days
        return Issue(
            repo=repo,
            id=raw["number"],
            state=raw["state"],
            age_days=age,
            labels=[lbl["name"] for lbl in raw.get("labels", [])],
            title=raw.get("title", ""),
            created_at=created,
        )

    @staticmethod
    def _to_dep_file(raw: dict) -> DepFile:  # pragma: no cover
        ...
