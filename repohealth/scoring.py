"""Phase 2: score & detect.

Turns the stored memory (Phase 1's tables) into a single 0–100 health score by
running four SQL aggregations — never a live GitHub call. This is the "score"
step the orchestrator runs every 24h before deciding whether to escalate.

Signal weights (from the spec):

    | Signal                         | Weight | Aggregation                          |
    |--------------------------------|--------|--------------------------------------|
    | Stale issues (>90 days open)   |  30%   | COUNT(open issues age_days > 90)     |
    | Outdated deps                  |  30%   | COUNT(current_ver != latest_ver)     |
    | CI red rate (last 30 runs)     |  25%   | AVG(status = 'failure')              |
    | Commit inactivity              |  15%   | MAX(timestamp) < now() - 30d         |

Each raw aggregation is mapped to a 0..1 *badness* fraction, then:

    score = round(100 * (1 - Σ weightᵢ · badnessᵢ))

so a perfectly healthy repo scores 100 and a maximally unhealthy one scores 0.

Normalization choices (kept simple and bounded):
  * stale / outdated  → fraction of the relevant population (stale ÷ open issues,
    outdated ÷ total deps). A raw COUNT isn't comparable across repos of
    different sizes; a fraction is.
  * ci red rate       → already a 0..1 average, used directly.
  * inactivity        → days since the latest stored activity, ramped linearly
    to full penalty at 30 days (the spec's threshold), clamped to [0, 1].

The SQL is written to run unchanged on both the SQLite mock and ClickHouse:
only COUNT / SUM / AVG / MAX appear, and the "now() - 30d" comparison is done in
Python from the MAX(timestamp) string rather than with a dialect-specific date
function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .storage import Storage

# Spec weights. Kept as a module constant so the orchestrator/report can show
# the exact breakdown, and so a test can assert they sum to 1.0.
WEIGHTS: dict[str, float] = {
    "stale_issues": 0.30,
    "outdated_deps": 0.30,
    "ci_red_rate": 0.25,
    "commit_inactivity": 0.15,
}

# Days of no activity that count as fully inactive (spec: 30d).
INACTIVITY_FULL_PENALTY_DAYS = 30


@dataclass
class Signal:
    """One scored dimension, with enough detail to explain the number."""

    key: str
    label: str
    weight: float
    raw: float          # the underlying aggregation (count, rate, days, …)
    detail: str         # human-readable raw value, e.g. "3 of 5 open issues"
    badness: float      # normalized 0..1 (1 == worst)

    @property
    def penalty(self) -> float:
        """Points this signal subtracts from a perfect 100."""
        return self.weight * self.badness * 100.0


@dataclass
class HealthScore:
    repo: str
    score: int                      # 0..100
    computed_at: datetime
    signals: list[Signal] = field(default_factory=list)

    def signal(self, key: str) -> Signal:
        for s in self.signals:
            if s.key == key:
                return s
        raise KeyError(key)


def _scalar(storage: Storage, sql: str, repo: str, default: float = 0.0) -> float:
    rows = storage.query(sql, [repo])
    if not rows or rows[0][0] is None:
        return default
    return float(rows[0][0])


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _stale_signal(storage: Storage, repo: str) -> Signal:
    stale = _scalar(
        storage,
        "SELECT COUNT(*) FROM issues WHERE repo=? AND state='open' "
        "AND age_days > 90",
        repo,
    )
    open_total = _scalar(
        storage, "SELECT COUNT(*) FROM issues WHERE repo=? AND state='open'", repo
    )
    badness = (stale / open_total) if open_total else 0.0
    return Signal(
        key="stale_issues",
        label="Stale issues (>90d open)",
        weight=WEIGHTS["stale_issues"],
        raw=stale,
        detail=f"{int(stale)} of {int(open_total)} open issues stale",
        badness=_clamp01(badness),
    )


def _outdated_signal(storage: Storage, repo: str) -> Signal:
    # `outdated` is stored as 1/0 (it already encodes current_ver != latest_ver).
    outdated = _scalar(
        storage, "SELECT SUM(outdated) FROM deps WHERE repo=?", repo
    )
    total = _scalar(storage, "SELECT COUNT(*) FROM deps WHERE repo=?", repo)
    badness = (outdated / total) if total else 0.0
    return Signal(
        key="outdated_deps",
        label="Outdated dependencies",
        weight=WEIGHTS["outdated_deps"],
        raw=outdated,
        detail=f"{int(outdated)} of {int(total)} deps outdated",
        badness=_clamp01(badness),
    )


def _ci_red_signal(storage: Storage, repo: str) -> Signal:
    # Last 30 runs by recency; AVG over the failure indicator. The subquery +
    # LIMIT works in both SQLite and ClickHouse.
    red = _scalar(
        storage,
        "SELECT AVG(CASE WHEN status='failure' THEN 1.0 ELSE 0.0 END) FROM "
        "(SELECT status FROM ci_runs WHERE repo=? ORDER BY timestamp DESC "
        "LIMIT 30)",
        repo,
    )
    return Signal(
        key="ci_red_rate",
        label="CI red rate (last 30 runs)",
        weight=WEIGHTS["ci_red_rate"],
        raw=red,
        detail=f"{red:.0%} of recent runs failing",
        badness=_clamp01(red),
    )


def _inactivity_signal(storage: Storage, repo: str, now: datetime) -> Signal:
    rows = storage.query("SELECT MAX(timestamp) FROM events WHERE repo=?", [repo])
    latest = _parse_dt(rows[0][0]) if rows else None
    if latest is None:
        days = float(INACTIVITY_FULL_PENALTY_DAYS)  # no activity at all → worst
    else:
        # Compare naive/aware safely by normalizing both to naive UTC.
        if latest.tzinfo is not None:
            latest = latest.astimezone(timezone.utc).replace(tzinfo=None)
        ref = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        days = max(0.0, (ref - latest).total_seconds() / 86400.0)
    badness = days / INACTIVITY_FULL_PENALTY_DAYS
    return Signal(
        key="commit_inactivity",
        label="Commit / activity inactivity",
        weight=WEIGHTS["commit_inactivity"],
        raw=days,
        detail=f"{days:.0f} days since last activity",
        badness=_clamp01(badness),
    )


def score_repo(
    storage: Storage, repo: str, now: datetime | None = None
) -> HealthScore:
    """Run the four aggregations and fold them into a 0–100 health score."""
    now = now or datetime.now(timezone.utc)
    signals = [
        _stale_signal(storage, repo),
        _outdated_signal(storage, repo),
        _ci_red_signal(storage, repo),
        _inactivity_signal(storage, repo, now),
    ]
    penalty = sum(s.weight * s.badness for s in signals)
    score = int(round(100 * (1 - penalty)))
    score = max(0, min(100, score))
    return HealthScore(repo=repo, score=score, computed_at=now, signals=signals)
