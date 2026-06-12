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
from .ingest import build_connector, build_storage, ingest_repo


def _cmd_ingest(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    connector = build_connector(cfg)
    storage = build_storage(cfg)
    try:
        result = ingest_repo(args.repo, connector, storage)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repohealth")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch a repo snapshot and store it")
    p_ingest.add_argument("--repo", required=True, help="owner/name")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_show = sub.add_parser("show", help="Read back stored health signals")
    p_show.add_argument("--repo", required=True, help="owner/name")
    p_show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
