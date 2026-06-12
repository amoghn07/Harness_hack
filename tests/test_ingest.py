"""End-to-end Phase 1 tests against the mock connector + in-memory SQLite."""

from __future__ import annotations

from repohealth.connectors.mock_github import MockGitHubConnector
from repohealth.ingest import ingest_repo
from repohealth.parsers import parse_package_json, parse_requirements_txt
from repohealth.registry import MockVersionRegistry
from repohealth.storage.sqlite_store import SQLiteStorage

REPO = "acme/widget"


def _store() -> SQLiteStorage:
    return SQLiteStorage(":memory:")


def test_ingest_counts():
    storage = _store()
    result = ingest_repo(REPO, MockGitHubConnector(), storage)
    assert result.issues == 6
    assert result.ci_runs == 30
    assert result.deps == 9  # 5 npm + 4 pypi
    assert result.outdated_deps == 6
    storage.close()


def test_stored_signals_queryable():
    storage = _store()
    ingest_repo(REPO, MockGitHubConnector(), storage)

    stale = storage.query(
        "SELECT COUNT(*) FROM issues WHERE repo=? AND state='open' "
        "AND age_days > 90", [REPO],
    )[0][0]
    assert stale == 3  # ids 101, 102, 103

    red = storage.query(
        "SELECT AVG(CASE WHEN status='failure' THEN 1.0 ELSE 0.0 END) "
        "FROM ci_runs WHERE repo=?", [REPO],
    )[0][0]
    assert abs(red - 9 / 30) < 1e-9
    storage.close()


def test_ingest_is_idempotent():
    storage = _store()
    ingest_repo(REPO, MockGitHubConnector(), storage)
    ingest_repo(REPO, MockGitHubConnector(), storage)
    n = storage.query("SELECT COUNT(*) FROM issues WHERE repo=?", [REPO])[0][0]
    assert n == 6  # re-ingest replaces, not appends
    storage.close()


def test_parse_package_json():
    pairs = dict(parse_package_json(
        '{"dependencies": {"express": "^4.17.1"}, '
        '"devDependencies": {"jest": "~29.0.0"}}'
    ))
    assert pairs["express"] == "4.17.1"
    assert pairs["jest"] == "29.0.0"


def test_parse_requirements_txt():
    pairs = dict(parse_requirements_txt(
        "# comment\nrequests==2.28.0\nflask>=2.0  # range, skipped\n"
    ))
    assert pairs["requests"] == "2.28.0"
    assert "flask" not in pairs


def test_outdated_detection():
    reg = MockVersionRegistry()
    assert reg.latest("pypi", "requests") == "2.32.3"
    assert reg.latest("npm", "axios") == "1.6.0"
