"""Orchestration: fetch -> score -> decide -> act.

This is the sequence Guild AI drives on a schedule (cron on Render, every 24h).
Guild AI is the workflow runner; this module is the workflow it runs — kept as
plain, testable Python so the steps are exercisable without the scheduler.

    fetch   -> ingest the latest snapshot into the store (Phase 1)
    score   -> four SQL aggregations -> 0-100 health score (scoring.py); the
               score is recorded to history every cycle for the trend chart.
    decide  -> below threshold? collect offenders + ask Bedrock for a plan
    act      -> Phase 3: Composio drafts bump PRs, closes stale issues, and
               publishes the weekly report (actions.py + report.py).

Two gates keep cost and side effects in check:
  * Bedrock only runs when score < threshold (decide).
  * Actions only run when escalated AND act=True (act). The score is still
    recorded when healthy, so the trend keeps filling in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .actions import ActionResult, build_actuator
from .bedrock import Analysis, build_analyzer
from .config import Config
from .detect import Detection, detect
<<<<<<< HEAD
from .evaluate import EvalResult, evaluate
from .ingest import build_connector, build_registry, build_storage, ingest_repo
=======
from .ingest import build_connector, build_registry, build_storage, ingest_repo
from .report import WeeklyReport, build_report, score_history
>>>>>>> 3efa6b8c3ff59acf1a87a2c8f746ba5a7f9c0311
from .scoring import HealthScore, score_repo
from .storage import Storage


@dataclass
class ActResult:
    pr_results: list[ActionResult] = field(default_factory=list)
    issue_results: list[ActionResult] = field(default_factory=list)
    report_result: ActionResult | None = None
    report: WeeklyReport | None = None


@dataclass
class CycleResult:
    repo: str
    score: HealthScore
    detection: Detection
    analysis: Analysis | None   # None when the repo was healthy (gate not tripped)
<<<<<<< HEAD
    evaluation: EvalResult | None = None  # set when analysis ran — the safety check
    action_blocked: bool = False  # True if the plan failed the gate (don't let Phase 3 act)
    acted: bool = False         # Phase 3 will flip this once actions execute
=======
    actions: ActResult | None = None   # None when not escalated or act=False
>>>>>>> 3efa6b8c3ff59acf1a87a2c8f746ba5a7f9c0311

    @property
    def escalated(self) -> bool:
        return self.analysis is not None

    @property
    def acted(self) -> bool:
        return self.actions is not None


def _act(
    repo: str, cfg: Config, storage: Storage, score: HealthScore,
    detection: Detection,
) -> ActResult:
    """Phase 3: execute the three real actions and publish the report."""
    actuator = build_actuator(cfg)
    pr_results = [actuator.draft_dep_pr(repo, d) for d in detection.outdated_deps]
    issue_results = [
        actuator.close_stale_issue(repo, i) for i in detection.stale_issues
    ]
    report = build_report(
        score, score_history(storage, repo),
        target=cfg.report_target,
        pr_results=pr_results, issue_results=issue_results,
    )
    report_result = actuator.publish_report(repo, report)
    return ActResult(pr_results=pr_results, issue_results=issue_results,
                     report_result=report_result, report=report)


def run_cycle(
    repo: str,
    cfg: Config | None = None,
    *,
    storage: Storage | None = None,
    skip_fetch: bool = False,
    act: bool = True,
    now: datetime | None = None,
) -> CycleResult:
    """Run one fetch -> score -> decide -> act cycle for a repo.

    `storage` lets callers (and tests) inject a store; otherwise one is built
    from config and closed at the end. `skip_fetch=True` scores whatever is
    already stored. `act=False` runs the analysis but executes no real actions
    (dry run)."""
    cfg = cfg or Config.from_env()
    own_storage = storage is None
    storage = storage or build_storage(cfg)
    try:
        # 1. fetch — refresh the agent's memory from the source of truth.
        if not skip_fetch:
            connector = build_connector(cfg)
            ingest_repo(repo, connector, storage, build_registry(cfg))
        else:
            storage.init_schema()

        # 2. score — pure SQL aggregation; persist to history for the trend.
        score = score_repo(storage, repo, now=now)
        storage.record_score(repo, score.score, score.computed_at)

        # 3. decide — who are the offenders, and is escalation warranted?
        detection = detect(storage, score, cfg.score_threshold)
        analysis: Analysis | None = None
        evaluation: EvalResult | None = None
        action_blocked = False
        if detection.needs_attention and not detection.is_empty:
<<<<<<< HEAD
            analyzer = build_analyzer(cfg)
            analysis = analyzer.analyze(detection)
            # Grade the plan against ground truth (the Detection). This is the
            # safety gate: a hallucinated or malformed action must not reach
            # Phase 3, which acts on real GitHub with no human in the loop.
            evaluation = evaluate(detection, analysis)
            action_blocked = not evaluation.safe_to_act

        # 4. act — Phase 3 consumes `analysis` + `detection` only when the plan
        # passed the gate. Left unwired here; `action_blocked` is the interlock.
        return CycleResult(repo=repo, score=score, detection=detection,
                           analysis=analysis, evaluation=evaluation,
                           action_blocked=action_blocked)
=======
            analysis = build_analyzer(cfg).analyze(detection)

        # 4. act — only when escalated and not a dry run.
        actions: ActResult | None = None
        if analysis is not None and act:
            actions = _act(repo, cfg, storage, score, detection)

        return CycleResult(repo=repo, score=score, detection=detection,
                           analysis=analysis, actions=actions)
>>>>>>> 3efa6b8c3ff59acf1a87a2c8f746ba5a7f9c0311
    finally:
        if own_storage:
            storage.close()
