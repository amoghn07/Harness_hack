"""Phase 2 tests: scoring, detection, and the orchestration cycle.

All run against the deterministic mock connector + in-memory SQLite, so the
exact health score is asserted (no network, no wall-clock dependence — `now` is
pinned to the mock dataset's reference date)."""

from __future__ import annotations

from repohealth.bedrock import MockBedrockAnalyzer
from repohealth.config import Config
from repohealth.connectors.mock_github import DEFAULT_NOW, MockGitHubConnector
from repohealth.detect import detect
from repohealth.ingest import ingest_repo
from repohealth.orchestrator import run_cycle
from repohealth.scoring import WEIGHTS, score_repo
from repohealth.storage.sqlite_store import SQLiteStorage

REPO = "acme/widget"


def _ingested_store() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    ingest_repo(REPO, MockGitHubConnector(), storage)
    return storage


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_signal_badness_matches_mock_dataset():
    storage = _ingested_store()
    score = score_repo(storage, REPO, now=DEFAULT_NOW)

    by_key = {s.key: s for s in score.signals}
    # 3 of 5 open issues are stale (>90d): ids 101, 102, 103.
    assert abs(by_key["stale_issues"].badness - 3 / 5) < 1e-9
    # 6 of 9 deps are outdated.
    assert abs(by_key["outdated_deps"].badness - 6 / 9) < 1e-9
    # 9 of 30 CI runs failed.
    assert abs(by_key["ci_red_rate"].badness - 9 / 30) < 1e-9
    # Newest stored activity is "now" → no inactivity penalty.
    assert by_key["commit_inactivity"].badness == 0.0
    storage.close()


def test_health_score_is_deterministic():
    storage = _ingested_store()
    score = score_repo(storage, REPO, now=DEFAULT_NOW)
    # penalty = .30*.6 + .30*(6/9) + .25*.3 + .15*0 ≈ 0.455 -> round(100*0.545) = 55
    assert score.score == 55
    storage.close()


def test_empty_repo_scores_100():
    """No data at all = nothing wrong yet (except inactivity, which has no
    activity to measure)."""
    storage = SQLiteStorage(":memory:")
    storage.init_schema()
    score = score_repo(storage, "nobody/nothing", now=DEFAULT_NOW)
    by_key = {s.key: s for s in score.signals}
    assert by_key["stale_issues"].badness == 0.0
    assert by_key["outdated_deps"].badness == 0.0
    # No events -> treated as maximally inactive.
    assert by_key["commit_inactivity"].badness == 1.0
    storage.close()


def test_detect_collects_offenders():
    storage = _ingested_store()
    score = score_repo(storage, REPO, now=DEFAULT_NOW)
    det = detect(storage, score, threshold=60)
    assert det.needs_attention  # 54 < 60
    assert len(det.outdated_deps) == 6
    assert len(det.stale_issues) == 3
    # Branch name follows the Phase 3 convention.
    dep = next(d for d in det.outdated_deps if d.name == "express")
    assert dep.branch == "bot/bump-express-4.19.2"
    storage.close()


def test_detect_healthy_repo_does_not_escalate():
    storage = _ingested_store()
    score = score_repo(storage, REPO, now=DEFAULT_NOW)
    det = detect(storage, score, threshold=50)  # 54 >= 50
    assert not det.needs_attention
    storage.close()


def test_mock_analyzer_plans_every_offender():
    storage = _ingested_store()
    score = score_repo(storage, REPO, now=DEFAULT_NOW)
    det = detect(storage, score, threshold=60)
    analysis = MockBedrockAnalyzer().analyze(det)
    assert analysis.model == "mock"
    assert len(analysis.actions) == 6 + 3
    assert sum(1 for a in analysis.actions if a.kind == "bump_dep") == 6
    assert sum(1 for a in analysis.actions if a.kind == "close_issue") == 3
    storage.close()


def test_run_cycle_escalates_on_mock_backends():
    storage = SQLiteStorage(":memory:")
    result = run_cycle(REPO, Config(), storage=storage, now=DEFAULT_NOW)
    assert result.score.score == 55
    assert result.escalated  # below default threshold of 60
    assert result.analysis is not None
    assert result.analysis.model == "mock"
    storage.close()


def test_run_cycle_skips_inference_when_healthy():
    storage = SQLiteStorage(":memory:")
    # Threshold below the score -> gate stays shut, no analyzer call.
    cfg = Config(score_threshold=50)
    result = run_cycle(REPO, cfg, storage=storage, now=DEFAULT_NOW)
    assert not result.escalated
    assert result.analysis is None
    storage.close()
