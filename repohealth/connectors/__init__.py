"""GitHub data sources. The pipeline depends only on `GitHubConnector`."""

from .base import GitHubConnector, RepoSnapshot

__all__ = ["GitHubConnector", "RepoSnapshot"]
