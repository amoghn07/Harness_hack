"""Phase 2 orchestration: fetch -> score -> decide -> act.

This is the sequence Guild AI drives on a schedule (cron on Render, every 24h).
Guild AI is the workflow runner; this module is the workflow it runs — kept as
plain, testable Python so the steps are exercisable without the scheduler.

    fetch   -> ingest the latest snapshot into the store (Phase 1)
    score   -> four SQL aggregations -> 0-100 health score (scoring.py)
    decide  -> below threshold? collect offenders + ask Bedrock for a plan
    act      -> Phase 3 (PRs, issue closes, report) — not wired yet; the
                CycleResult carries everything Phase 3 needs.

The cost gate lives in `decide`: Bedrock only fires when score < threshold, so
healthy repos cost nothing beyond the SQL reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bedrock import Analysis, build_analyzer
from .config import Config
from .detect import Detection, detect
from .evaluate import EvalResult, evaluate
from .ingest import build_connector, build_registry, build_storage, ingest_repo
from .scoring import HealthScore, score_repo
from .storage import Storage


@dataclass
class CycleResult:
    repo: str
    score: HealthScore
    detection: Detection
    analysis: Analysis | None   # None when the repo was healthy (gate not tripped)
    evaluation: EvalResult | None = None  # set when analysis ran — the safety check
    action_blocked: bool = False  # True if the plan failed the gate (don't let Phase 3 act)
    acted: bool = False         # Phase 3 will flip this once actions execute

    @property
    def escalated(self) -> bool:
        return self.analysis is not None


def run_cycle(
    repo: str,
    cfg: Config | None = None,
    *,
    storage: Storage | None = None,
    skip_fetch: bool = False,
    now: datetime | None = None,
) -> CycleResult:
    """Run one fetch -> score -> decide cycle for a repo.

    `storage` lets callers (and tests) inject a store; otherwise one is built
    from config and closed at the end. `skip_fetch=True` scores whatever is
    already in the store (the scheduled agent re-fetches; an ad-hoc score may
    not want to).
    """
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

        # 2. score — pure SQL aggregation over the stored snapshot.
        score = score_repo(storage, repo, now=now)

        # 3. decide — who are the offenders, and is escalation warranted?
        detection = detect(storage, score, cfg.score_threshold)
        analysis: Analysis | None = None
        evaluation: EvalResult | None = None
        action_blocked = False
        if detection.needs_attention and not detection.is_empty:
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
    finally:
        if own_storage:
            storage.close()
