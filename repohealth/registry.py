"""Resolve the latest published version of a dependency.

Phase 1 uses a canned lookup so the demo is offline and deterministic. The real
implementation hits the npm and PyPI registries (commented below) — both are
simple unauthenticated GET requests, no SDK required.
"""

from __future__ import annotations

import abc


class VersionRegistry(abc.ABC):
    @abc.abstractmethod
    def latest(self, ecosystem: str, name: str) -> str | None:
        """Latest version string, or None if unknown."""
        raise NotImplementedError


class MockVersionRegistry(VersionRegistry):
    """Canned 'latest' versions chosen so some deps read as outdated."""

    _LATEST = {
        "npm": {
            "express": "4.19.2",   # outdated vs 4.17.1
            "lodash": "4.17.21",   # outdated vs 4.17.20
            "axios": "1.6.0",      # current
            "jest": "29.7.0",      # outdated vs 29.0.0
            "eslint": "8.50.0",    # current
        },
        "pypi": {
            "requests": "2.32.3",  # outdated vs 2.28.0
            "flask": "3.0.3",      # outdated vs 2.2.0
            "pydantic": "2.5.0",   # current
            "pytest": "8.2.2",     # outdated vs 7.4.0
        },
    }

    def latest(self, ecosystem: str, name: str) -> str | None:
        return self._LATEST.get(ecosystem, {}).get(name)


class HttpVersionRegistry(VersionRegistry):  # pragma: no cover - network
    """Real registry lookups against npm and PyPI.

    Failures (network error, 404 for a private/renamed package) resolve to None
    rather than raising, so one bad dependency never aborts an ingest. Results
    are cached per process to avoid hammering the registries on re-runs."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], str | None] = {}

    def latest(self, ecosystem: str, name: str) -> str | None:
        key = (ecosystem, name)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = self._fetch(ecosystem, name)
        return self._cache[key]

    @staticmethod
    def _fetch(ecosystem: str, name: str) -> str | None:
        import json
        import urllib.request

        try:
            if ecosystem == "pypi":
                url = f"https://pypi.org/pypi/{name}/json"
                with urllib.request.urlopen(url, timeout=10) as r:
                    return json.load(r)["info"]["version"]
            if ecosystem == "npm":
                url = f"https://registry.npmjs.org/{name}/latest"
                with urllib.request.urlopen(url, timeout=10) as r:
                    return json.load(r)["version"]
        except Exception:
            return None
        return None
