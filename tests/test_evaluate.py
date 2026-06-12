"""Tests for the remediation-plan evaluator + safety gate."""

from __future__ import annotations

from repohealth.bedrock import Action, Analysis, MockBedrockAnalyzer
from repohealth.detect import Detection, OutdatedDep, StaleIssue
from repohealth.evaluate import evaluate


def _detection() -> Detection:
    return Detection(
        repo="acme/widget",
        score=54,
        threshold=60,
        needs_attention=True,
        stale_issues=[StaleIssue(id=101, title="bug", age_days=142, labels=["bug"])],
        outdated_deps=[
            OutdatedDep("express", "4.17.1", "4.19.2", "npm", "package.json"),
            OutdatedDep("flask", "2.2.0", "3.0.3", "pypi", "requirements.txt"),
        ],
    )


def test_perfect_plan_passes_gate():
    det = _detection()
    analysis = Analysis(summary="ok", actions=[
        Action("bump_dep", "npm:express", "...", "high"),
        Action("bump_dep", "pypi:flask", "...", "medium"),
        Action("close_issue", "101", "...", "medium"),
    ])
    r = evaluate(det, analysis)
    assert r.groundedness == 1.0
    assert r.coverage == 1.0
    assert r.schema_validity == 1.0
    assert r.safe_to_act is True


def test_hallucinated_target_blocks():
    det = _detection()
    analysis = Analysis(summary="x", actions=[
        Action("bump_dep", "npm:left-pad", "not a real offender", "high"),
    ])
    r = evaluate(det, analysis)
    assert r.groundedness < 1.0
    assert "npm:left-pad" in r.ungrounded
    assert r.safe_to_act is False          # the gate trips


def test_dropped_offender_is_incomplete_but_safe():
    det = _detection()
    analysis = Analysis(summary="x", actions=[
        Action("bump_dep", "npm:express", "...", "high"),  # flask + issue dropped
    ])
    r = evaluate(det, analysis)
    assert r.groundedness == 1.0
    assert r.coverage < 1.0
    assert "pypi:flask" in r.uncovered and "#101" in r.uncovered
    assert r.safe_to_act is True           # incomplete ≠ unsafe


def test_invalid_schema_blocks():
    det = _detection()
    analysis = Analysis(summary="x", actions=[
        Action("bump_dep", "npm:express", "...", "URGENT"),  # bad priority
    ])
    r = evaluate(det, analysis)
    assert r.schema_validity < 1.0
    assert r.safe_to_act is False


def test_empty_plan_is_safe_but_uncovered():
    det = _detection()
    r = evaluate(det, Analysis(summary="nothing", actions=[]))
    assert r.groundedness == 1.0           # nothing to be wrong about
    assert r.coverage == 0.0
    assert r.safe_to_act is True


def test_mock_analyzer_output_is_grounded():
    det = _detection()
    analysis = MockBedrockAnalyzer().analyze(det)
    r = evaluate(det, analysis)
    assert r.safe_to_act is True
    assert r.groundedness == 1.0
    assert r.coverage == 1.0
