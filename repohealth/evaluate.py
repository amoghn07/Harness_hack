"""Evaluate a remediation Analysis against the Detection it was derived from.

The analyzer's job is narrow: take the offenders `detect()` found
(deterministically) and prioritize + explain them. That means the Detection is
*ground truth* — we can grade every generation, online, with zero labeling cost
and no LLM:

  * groundedness   — does every action reference an offender that actually
                     exists? A bump for a dep we never flagged, or a close for
                     an issue we never detected, is a hallucination.
  * coverage       — did the plan silently drop offenders it should address?
  * schema_validity — are all kinds/priorities well-formed?

`groundedness` and `schema_validity` are *safety* signals: because Phase 3 acts
with no human in the loop, a hallucinated or malformed action would open a bogus
PR or close the wrong issue. `safe_to_act` gates that. `coverage` is a *quality*
signal — low coverage is incomplete, not dangerous, so it warns but never blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bedrock import Analysis
from .detect import Detection

VALID_KINDS = {"bump_dep", "close_issue", "other"}
VALID_PRIORITIES = {"high", "medium", "low"}


@dataclass
class EvalResult:
    groundedness: float          # 0..1 — fraction of offender-referencing actions that resolve
    coverage: float              # 0..1 — fraction of detected offenders the plan addresses
    schema_validity: float       # 0..1 — fraction of actions with valid kind + priority
    ungrounded: list[str] = field(default_factory=list)   # targets that match no offender
    uncovered: list[str] = field(default_factory=list)    # offenders no action addresses
    invalid_actions: list[str] = field(default_factory=list)

    @property
    def safe_to_act(self) -> bool:
        """Block Phase 3 unless every action is grounded and well-formed."""
        return self.groundedness >= 1.0 and self.schema_validity >= 1.0

    def as_scores(self) -> dict[str, float]:
        """The numeric scores, for Langfuse / dashboards."""
        return {
            "groundedness": self.groundedness,
            "coverage": self.coverage,
            "schema_validity": self.schema_validity,
        }


def _dep_keys(detection: Detection) -> set[str]:
    """Accept either "ecosystem:name" (what the mock emits) or a bare name."""
    keys: set[str] = set()
    for d in detection.outdated_deps:
        keys.add(f"{d.ecosystem}:{d.name}")
        keys.add(d.name)
    return keys


def evaluate(detection: Detection, analysis: Analysis) -> EvalResult:
    dep_keys = _dep_keys(detection)
    issue_ids = {str(i.id) for i in detection.stale_issues}

    offender_actions = 0      # actions that claim to address a specific offender
    grounded = 0
    valid = 0
    ungrounded: list[str] = []
    invalid: list[str] = []

    covered_deps: set[str] = set()
    covered_issues: set[str] = set()

    for a in analysis.actions:
        if a.kind in VALID_KINDS and a.priority in VALID_PRIORITIES:
            valid += 1
        else:
            invalid.append(f"{a.kind}/{a.priority} -> {a.target}")

        if a.kind == "bump_dep":
            offender_actions += 1
            if a.target in dep_keys or a.target.split(":")[-1] in dep_keys:
                grounded += 1
                covered_deps.add(a.target.split(":")[-1])
            else:
                ungrounded.append(a.target)
        elif a.kind == "close_issue":
            offender_actions += 1
            if a.target in issue_ids:
                grounded += 1
                covered_issues.add(a.target)
            else:
                ungrounded.append(a.target)
        # "other" makes no claim about a specific offender → not graded for groundedness

    total_actions = len(analysis.actions)
    total_offenders = len(detection.outdated_deps) + len(detection.stale_issues)
    covered = len(covered_deps) + len(covered_issues)

    uncovered: list[str] = []
    for d in detection.outdated_deps:
        if d.name not in covered_deps:
            uncovered.append(f"{d.ecosystem}:{d.name}")
    for i in detection.stale_issues:
        if str(i.id) not in covered_issues:
            uncovered.append(f"#{i.id}")

    return EvalResult(
        # No offender-referencing actions → nothing to be wrong about → grounded.
        groundedness=grounded / offender_actions if offender_actions else 1.0,
        coverage=covered / total_offenders if total_offenders else 1.0,
        schema_validity=valid / total_actions if total_actions else 1.0,
        ungrounded=ungrounded,
        uncovered=uncovered,
        invalid_actions=invalid,
    )
