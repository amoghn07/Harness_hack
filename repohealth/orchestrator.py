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
from .ingest import build_connector, build_storage, ingest_repo
from .scoring import HealthScore, score_repo
from .storage import Storage


@dataclass
class CycleResult:
    repo: str
    score: HealthScore
    detection: Detection
    analysis: Analysis | None   # None when the repo was healthy (gate not tripped)
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
            ingest_repo(repo, connector, storage)
        else:
            storage.init_schema()

        # 2. score — pure SQL aggregation over the stored snapshot.
        score = score_repo(storage, repo, now=now)

        # 3. decide — who are the offenders, and is escalation warranted?
        detection = detect(storage, score, cfg.score_threshold)
        analysis: Analysis | None = None
        if detection.needs_attention and not detection.is_empty:
            analyzer = build_analyzer(cfg)
            analysis = analyzer.analyze(detection)

        # 4. act — Phase 3 consumes `analysis` + `detection`. Left unwired here.
        return CycleResult(repo=repo, score=score, detection=detection,
                           analysis=analysis)
    finally:
        if own_storage:
            storage.close()
