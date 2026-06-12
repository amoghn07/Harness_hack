"""Phase 3 tests: actions, the weekly report, and the act-and-publish cycle.

All against the mock connector + in-memory SQLite + MockActuator — no network,
deterministic, `now` pinned to the mock dataset's reference date."""

from __future__ import annotations

from repohealth.actions import (
    STALE_COMMENT,
    STALE_LABEL,
    MockActuator,
    changelog_summary,
    pr_body,
)
from repohealth.config import Config
from repohealth.connectors.mock_github import DEFAULT_NOW, MockGitHubConnector
from repohealth.detect import detect
from repohealth.ingest import ingest_repo
from repohealth.orchestrator import run_cycle
from repohealth.report import build_report, score_history
from repohealth.scoring import score_repo
from repohealth.storage.sqlite_store import SQLiteStorage

REPO = "acme/widget"


def _ingested_store() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    ingest_repo(REPO, MockGitHubConnector(), storage)
    return storage


def _detection(storage):
    score = score_repo(storage, REPO, now=DEFAULT_NOW)
    return score, detect(storage, score, threshold=60)


# ── Actions ──────────────────────────────────────────────────────────────────

def test_mock_pr_uses_spec_branch_and_changelog():
    storage = _ingested_store()
    _, det = _detection(storage)
    dep = next(d for d in det.outdated_deps if d.name == "express")
    res = MockActuator().draft_dep_pr(REPO, dep)
    assert res.kind == "pull_request"
    assert res.branch == "bot/bump-express-4.19.2"
    assert res.url.startswith(f"https://github.com/{REPO}/pull/")
    # PR body carries the changelog diff summary.
    body = pr_body(dep)
    assert "4.17.1" in body and "4.19.2" in body
    assert "npmjs.com" in changelog_summary(dep)
    storage.close()


def test_mock_close_issue_uses_spec_comment_and_label():
    storage = _ingested_store()
    _, det = _detection(storage)
    res = MockActuator().close_stale_issue(REPO, det.stale_issues[0])
    assert res.kind == "close_issue"
    assert STALE_LABEL in res.detail
    # The exact stale notice is a module constant the real connector posts.
    assert STALE_COMMENT == "Closing as stale after 90 days; reopen if still relevant"
    storage.close()


def test_actuator_records_everything_performed():
    storage = _ingested_store()
    score, det = _detection(storage)
    act = MockActuator()
    for d in det.outdated_deps:
        act.draft_dep_pr(REPO, d)
    for i in det.stale_issues:
        act.close_stale_issue(REPO, i)
    assert len(act.performed) == 6 + 3
    storage.close()


# ── Report ───────────────────────────────────────────────────────────────────

def test_report_includes_score_trend_and_links():
    storage = _ingested_store()
    score, det = _detection(storage)
    storage.record_score(REPO, 80, DEFAULT_NOW)
    storage.record_score(REPO, score.score, DEFAULT_NOW)

    act = MockActuator()
    prs = [act.draft_dep_pr(REPO, d) for d in det.outdated_deps]
    issues = [act.close_stale_issue(REPO, i) for i in det.stale_issues]
    report = build_report(score, score_history(storage, REPO),
                          pr_results=prs, issue_results=issues)

    assert f"{score.score}/100" in report.markdown
    assert "## Trend" in report.markdown
    assert "bot/bump-express-4.19.2" in report.markdown
    assert "#101" in report.markdown  # oldest stale issue
    assert len(report.pr_links) == 6
    assert set(report.closed_issues) == {101, 102, 103}
    storage.close()


def test_report_preview_with_no_actions():
    storage = _ingested_store()
    score, _ = _detection(storage)
    report = build_report(score, score_history(storage, REPO))
    assert "_None this cycle._" in report.markdown
    storage.close()


# ── Orchestration ──────────────────────────────────────────────────────────--

def test_run_cycle_acts_when_escalated():
    storage = SQLiteStorage(":memory:")
    result = run_cycle(REPO, Config(), storage=storage, now=DEFAULT_NOW)
    assert result.escalated
    assert result.acted
    assert len(result.actions.pr_results) == 6
    assert len(result.actions.issue_results) == 3
    assert result.actions.report_result.ok
    assert result.actions.report is not None
    storage.close()


def test_run_cycle_dry_run_skips_actions():
    storage = SQLiteStorage(":memory:")
    result = run_cycle(REPO, Config(), storage=storage, act=False, now=DEFAULT_NOW)
    assert result.escalated      # analysis still produced
    assert not result.acted      # but no actions executed
    assert result.actions is None
    storage.close()


def test_run_cycle_healthy_repo_does_not_act():
    storage = SQLiteStorage(":memory:")
    cfg = Config(score_threshold=50)  # score 55 >= 50 -> healthy
    result = run_cycle(REPO, cfg, storage=storage, now=DEFAULT_NOW)
    assert not result.escalated
    assert not result.acted
    storage.close()


def test_run_cycle_records_score_history():
    storage = SQLiteStorage(":memory:")
    run_cycle(REPO, Config(), storage=storage, act=False, now=DEFAULT_NOW)
    run_cycle(REPO, Config(), storage=storage, act=False, now=DEFAULT_NOW)
    history = score_history(storage, REPO)
    assert len(history) == 2          # one sample per cycle, even on dry runs
    assert all(score == 55 for score, _ in history)
    storage.close()
