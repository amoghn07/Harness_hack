"""Abstract GitHub connector.

A connector's job is narrow: hand back the raw material for one repo — issues,
open PRs, dependency-manifest file contents, and recent CI runs. Parsing deps
and computing health lives downstream, so swapping Composio for the mock (or a
raw REST client) changes nothing in the pipeline.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..models import CiRun, Issue


@dataclass
class DepFile:
    """A dependency manifest as fetched — still unparsed text."""

    path: str  # "package.json" | "requirements.txt"
    ecosystem: str  # "npm" | "pypi"
    content: str


@dataclass
class RepoSnapshot:
    """Everything a connector returns for one repo at one point in time."""

    repo: str
    issues: list[Issue] = field(default_factory=list)
    open_pr_count: int = 0
    dep_files: list[DepFile] = field(default_factory=list)
    ci_runs: list[CiRun] = field(default_factory=list)


class GitHubConnector(abc.ABC):
    """Pulls a point-in-time snapshot of a repo's health-relevant data."""

    @abc.abstractmethod
    def fetch(self, repo: str) -> RepoSnapshot:
        """`repo` is "owner/name"."""
        raise NotImplementedError
