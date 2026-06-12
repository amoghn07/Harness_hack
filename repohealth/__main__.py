"""CLI for Phase 1 ingest.

    python -m repohealth ingest --repo owner/name
    python -m repohealth show   --repo owner/name   # peek at what was stored

Backends come from env (REPOHEALTH_GITHUB, REPOHEALTH_STORE); defaults are the
zero-dependency mock + SQLite, so this runs out of the box.
"""

from __future__ import annotations

import argparse
import sys

from .config import Config
from .ingest import build_connector, build_registry, build_storage, ingest_repo


def _cmd_ingest(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    connector = build_connector(cfg)
    storage = build_storage(cfg)
    registry = build_registry(cfg)
    try:
        result = ingest_repo(args.repo, connector, storage, registry)
    finally:
        storage.close()

    print(f"Ingested {result.repo} via {cfg.github_backend} -> {cfg.store_backend}")
    print(f"  issues     : {result.issues}")
    print(f"  deps       : {result.deps} ({result.outdated_deps} outdated)")
    print(f"  ci_runs    : {result.ci_runs}")
    print(f"  events     : {result.events}")
    if cfg.store_backend == "sqlite":
        print(f"  stored at  : {cfg.sqlite_path}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Quick read-back so you can eyeball the agent's memory."""
    cfg = Config.from_env()
    storage = build_storage(cfg)
    try:
        repo = args.repo
        stale = storage.query(
            "SELECT COUNT(*) FROM issues WHERE repo=? AND state='open' "
            "AND age_days > 90", [repo],
        )[0][0]
        outdated = storage.query(
            "SELECT COUNT(*) FROM deps WHERE repo=? AND outdated=1", [repo],
        )[0][0]
        red = storage.query(
            "SELECT AVG(CASE WHEN status='failure' THEN 1.0 ELSE 0.0 END) "
            "FROM ci_runs WHERE repo=?", [repo],
        )[0][0]
        print(f"Stored snapshot for {repo}:")
        print(f"  stale open issues (>90d) : {stale}")
        print(f"  outdated dependencies    : {outdated}")
        print(f"  CI red rate              : {red:.0%}" if red is not None
              else "  CI red rate              : n/a")
        print("\nOutdated deps:")
        for name, cur, latest, eco in storage.query(
            "SELECT name, current_ver, latest_ver, ecosystem FROM deps "
            "WHERE repo=? AND outdated=1 ORDER BY ecosystem, name", [repo],
        ):
            print(f"  [{eco}] {name}: {cur} -> {latest}")
    finally:
        storage.close()
    return 0


def _print_score(score) -> None:
    """Shared rendering for the score breakdown."""
    print(f"Health score for {score.repo}: {score.score}/100")
    print("  signals (weight x badness = penalty):")
    for s in score.signals:
        print(f"    {s.label:<32} {s.detail:<34} "
              f"{s.weight:.0%} x {s.badness:.2f} = -{s.penalty:.1f}")


def _cmd_score(args: argparse.Namespace) -> int:
    """Score a repo from the stored snapshot (Phase 2 'score' step)."""
    from .scoring import score_repo

    cfg = Config.from_env()
    storage = build_storage(cfg)
    try:
        storage.init_schema()
        score = score_repo(storage, args.repo)
    finally:
        storage.close()
    _print_score(score)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Run one fetch -> score -> decide -> act cycle (the scheduled agent)."""
    from .orchestrator import run_cycle

    cfg = Config.from_env()
    result = run_cycle(args.repo, cfg, skip_fetch=args.no_fetch, act=not args.no_act)
    _print_score(result.score)
    det = result.detection
    print(f"\nThreshold {det.threshold} -> "
          f"{'ESCALATE' if det.needs_attention else 'healthy, no action'}")
    print(f"  outdated deps : {len(det.outdated_deps)}")
    print(f"  stale issues  : {len(det.stale_issues)}")
    if result.analysis is not None:
        print(f"\nBedrock analysis ({result.analysis.model}):")
        print(f"  {result.analysis.summary}")
        for a in result.analysis.actions:
            print(f"   [{a.priority}] {a.kind} {a.target}: {a.rationale}")
    if result.evaluation is not None:
        e = result.evaluation
        print("\nPlan evaluation (graded against the detection):")
        print(f"  groundedness    : {e.groundedness:.0%}")
        print(f"  coverage        : {e.coverage:.0%}")
        print(f"  schema validity : {e.schema_validity:.0%}")
        if e.ungrounded:
            print(f"  ungrounded targets : {e.ungrounded}")
        if e.uncovered:
            print(f"  uncovered offenders: {e.uncovered}")
        verdict = "BLOCKED — plan not safe to act on" if result.action_blocked \
            else "PASS — safe for Phase 3 to act"
        print(f"  gate            : {verdict}")
    if result.actions is not None:
        acts = result.actions
        print(f"\nActions executed via {cfg.actions_backend}:")
        for r in acts.pr_results:
            print(f"   PR  {r.branch}: {r.detail} -> {r.url}")
        for r in acts.issue_results:
            print(f"   issue #{r.target}: {r.detail} -> {r.url}")
        if acts.report_result is not None:
            print(f"   report -> {acts.report_result.url}")
    elif result.escalated and result.action_blocked:
        print("\n(act skipped — plan blocked by the safety gate)")
    elif result.escalated:
        print("\n(act skipped — dry run)")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Render the weekly Markdown report from the stored snapshot + history."""
    from .detect import detect
    from .report import build_report, score_history
    from .scoring import score_repo

    cfg = Config.from_env()
    storage = build_storage(cfg)
    try:
        storage.init_schema()
        score = score_repo(storage, args.repo)
        detect(storage, score, cfg.score_threshold)  # validates the snapshot exists
        report = build_report(score, score_history(storage, args.repo),
                              target=cfg.report_target)
    finally:
        storage.close()
    print(report.markdown)
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    """Generate the GitHub OAuth link, or check connection status with --check."""
    from .connect import check_connection, create_link

    cfg = Config.from_env()
    if args.check:
        status = check_connection(cfg)
        print(f"GitHub connection for user '{cfg.composio_user_id}': "
              f"{'CONNECTED' if status.connected else 'NOT CONNECTED'} "
              f"(status={status.status}, account={status.account_id})")
        return 0 if status.connected else 1
    url = create_link(cfg)
    print("Authorize GitHub by opening this URL in your browser:\n")
    print(f"  {url}\n")
    print("Then run:  python -m repohealth connect --check")
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the read-only health dashboard over the stored snapshot."""
    from .dashboard import serve

    serve(Config.from_env(), port=args.port, host=args.host)
    return 0


def main(argv: list[str] | None = None) -> int:
    # The report markdown carries a unicode trend sparkline; the published copy
    # is UTF-8 (GitHub/Notion handle it), but a legacy Windows console (cp1252)
    # can't print it. Make stdout tolerant rather than crash on display.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(prog="repohealth")
    sub = parser.add_subparsers(dest="command", required=True)

    p_connect = sub.add_parser("connect", help="Authorize GitHub via Composio OAuth")
    p_connect.add_argument("--check", action="store_true",
                           help="Check connection status instead of creating a link")
    p_connect.set_defaults(func=_cmd_connect)

    p_ingest = sub.add_parser("ingest", help="Fetch a repo snapshot and store it")
    p_ingest.add_argument("--repo", required=True, help="owner/name")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_show = sub.add_parser("show", help="Read back stored health signals")
    p_show.add_argument("--repo", required=True, help="owner/name")
    p_show.set_defaults(func=_cmd_show)

    p_score = sub.add_parser("score", help="Compute the 0-100 health score")
    p_score.add_argument("--repo", required=True, help="owner/name")
    p_score.set_defaults(func=_cmd_score)

    p_run = sub.add_parser(
        "run", help="Run one fetch->score->decide->act cycle (the scheduled agent)")
    p_run.add_argument("--repo", required=True, help="owner/name")
    p_run.add_argument("--no-fetch", action="store_true",
                       help="Score the existing snapshot without re-ingesting")
    p_run.add_argument("--no-act", action="store_true",
                       help="Analyze but execute no actions (dry run)")
    p_run.set_defaults(func=_cmd_run)

    p_report = sub.add_parser("report", help="Render the weekly Markdown report")
    p_report.add_argument("--repo", required=True, help="owner/name")
    p_report.set_defaults(func=_cmd_report)

    p_dash = sub.add_parser("dashboard", help="Serve the read-only health dashboard")
    p_dash.add_argument("--host", default="127.0.0.1",
                        help="bind address; use 0.0.0.0 to expose when hosting")
    p_dash.add_argument("--port", type=int, default=8000)
    p_dash.set_defaults(func=_cmd_dashboard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
