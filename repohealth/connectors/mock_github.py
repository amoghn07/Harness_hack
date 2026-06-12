"""Deterministic fake GitHub data for a fully offline Phase 1 demo.

The data is hand-built to exercise every downstream signal: some stale issues
(>90 days), some fresh; a mix of outdated and current deps across both npm and
PyPI; and a CI history with a realistic red rate. Ages are computed relative to
a `now` you pass in, so tests stay stable regardless of the wall clock.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..models import CiRun, Issue
from .base import DepFile, GitHubConnector, RepoSnapshot

# Reference "now" for the canned dataset (matches the project's current date).
DEFAULT_NOW = datetime(2026, 6, 12)


def _days_ago(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


class MockGitHubConnector(GitHubConnector):
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or DEFAULT_NOW

    def fetch(self, repo: str) -> RepoSnapshot:
        now = self.now

        issue_seed = [
            # (id, state, age_days, labels, title)
            (101, "open", 142, ["bug"], "Crash on empty config"),
            (102, "open", 118, ["enhancement", "help wanted"], "Add dark mode"),
            (103, "open", 95, ["bug", "stale-candidate"], "Flaky test on Windows"),
            (104, "open", 12, ["bug"], "Typo in README"),
            (105, "open", 3, ["question"], "How to configure proxy?"),
            (106, "closed", 200, ["bug"], "Old fixed memory leak"),
        ]
        issues = [
            Issue(
                repo=repo,
                id=iid,
                state=state,
                age_days=age,
                labels=labels,
                title=title,
                created_at=_days_ago(now, age),
                updated_at=_days_ago(now, max(0, age - 5)),
            )
            for (iid, state, age, labels, title) in issue_seed
        ]

        package_json = json.dumps(
            {
                "name": repo.split("/")[-1],
                "version": "1.4.0",
                "dependencies": {
                    "express": "^4.17.1",
                    "lodash": "^4.17.20",
                    "axios": "^1.6.0",
                },
                "devDependencies": {
                    "jest": "^29.0.0",
                    "eslint": "^8.50.0",
                },
            },
            indent=2,
        )
        requirements_txt = (
            "# core\n"
            "requests==2.28.0\n"
            "flask==2.2.0\n"
            "pydantic==2.5.0\n"
            "\n"
            "# tooling\n"
            "pytest==7.4.0\n"
        )
        dep_files = [
            DepFile("package.json", "npm", package_json),
            DepFile("requirements.txt", "pypi", requirements_txt),
        ]

        # 30 CI runs, ~30% red — exercises the "CI red rate" signal later.
        ci_runs: list[CiRun] = []
        red_positions = {2, 5, 6, 11, 17, 18, 24, 29, 30}
        for i in range(1, 31):
            ci_runs.append(
                CiRun(
                    repo=repo,
                    branch="main",
                    status="failure" if i in red_positions else "success",
                    timestamp=_days_ago(now, 30 - i),
                    workflow="ci.yml",
                )
            )

        return RepoSnapshot(
            repo=repo,
            issues=issues,
            open_pr_count=4,
            dep_files=dep_files,
            ci_runs=ci_runs,
        )
