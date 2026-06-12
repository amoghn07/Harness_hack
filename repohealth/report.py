"""Phase 3: the weekly health report.

Assembles a self-contained Markdown summary that Composio posts to a GitHub
Discussion or a Notion page. Per the spec it includes:
  * the current health score,
  * a trend chart pulled from the stored score history (ClickHouse/SQLite),
  * links to the PRs drafted and the issues closed this cycle.

The "chart" is a unicode sparkline + a small table — dependency-free and renders
in both GitHub Discussions and Notion. A real image chart could replace the
sparkline without changing this module's contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .actions import ActionResult
from .scoring import HealthScore
from .storage import Storage

_SPARK = "▁▂▃▄▅▆▇█"


@dataclass
class WeeklyReport:
    repo: str
    score: int
    title: str
    markdown: str
    target: str                      # "github" | "notion"
    pr_links: list[str] = field(default_factory=list)
    closed_issues: list[int] = field(default_factory=list)


def _sparkline(values: list[int]) -> str:
    """Map scores (0–100) onto an 8-level unicode sparkline."""
    if not values:
        return "(no history yet)"
    return "".join(_SPARK[min(7, max(0, v * 8 // 100))] for v in values)


def score_history(storage: Storage, repo: str, limit: int = 30) -> list[tuple[int, str]]:
    """Most recent score samples, oldest-first. (score, timestamp-iso)."""
    rows = storage.query(
        "SELECT score, timestamp FROM scores WHERE repo=? "
        "ORDER BY timestamp DESC LIMIT ?",
        [repo, limit],
    )
    return [(int(s), str(t)) for s, t in reversed(rows)]


def _trend_section(history: list[tuple[int, str]]) -> str:
    scores = [s for s, _ in history]
    spark = _sparkline(scores)
    if not scores:
        return f"## Trend\n\n{spark}\n"
    lo, hi = min(scores), max(scores)
    arrow = "▲" if len(scores) > 1 and scores[-1] > scores[0] else (
        "▼" if len(scores) > 1 and scores[-1] < scores[0] else "▬")
    return (
        f"## Trend ({len(scores)} samples)\n\n"
        f"`{spark}`  {arrow}  range {lo}–{hi}, latest {scores[-1]}\n"
    )


def build_report(
    health: HealthScore,
    history: list[tuple[int, str]],
    *,
    target: str = "github",
    pr_results: list[ActionResult] | None = None,
    issue_results: list[ActionResult] | None = None,
    generated_at: datetime | None = None,
) -> WeeklyReport:
    """Render the weekly report. `pr_results`/`issue_results` are this cycle's
    actions; pass empty/None for a preview that lists what *would* be done."""
    pr_results = pr_results or []
    issue_results = issue_results or []
    when = (generated_at or health.computed_at).date().isoformat()
    repo = health.repo

    lines: list[str] = [
        f"# Repo health report — {repo}",
        f"_Generated {when}_",
        "",
        f"## Score: {health.score}/100",
        "",
        "| Signal | Detail | Penalty |",
        "| --- | --- | --- |",
    ]
    for s in health.signals:
        lines.append(f"| {s.label} | {s.detail} | -{s.penalty:.1f} |")
    lines += ["", _trend_section(history)]

    lines.append("## Drafted PRs")
    if pr_results:
        for r in pr_results:
            link = f"[{r.url}]({r.url})" if r.url else "(no link)"
            lines.append(f"- `{r.branch}` — {r.target} {r.detail} → {link}")
    else:
        lines.append("_None this cycle._")
    lines.append("")

    lines.append("## Closed stale issues")
    if issue_results:
        for r in issue_results:
            link = f"[#{r.target}]({r.url})" if r.url else f"#{r.target}"
            lines.append(f"- {link} — {r.detail}")
    else:
        lines.append("_None this cycle._")

    markdown = "\n".join(lines)
    return WeeklyReport(
        repo=repo,
        score=health.score,
        title=f"Repo health report — {repo} ({when})",
        markdown=markdown,
        target=target,
        pr_links=[r.url for r in pr_results if r.url],
        closed_issues=[int(r.target) for r in issue_results if r.target.isdigit()],
    )
