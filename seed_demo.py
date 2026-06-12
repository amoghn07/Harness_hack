"""Seed the demo repo with deliberately outdated deps + stale issues.

Uses the SAME Composio GitHub connection the agent uses (no separate token).
Idempotent: re-running updates the existing files rather than failing.

    python3 seed_demo.py vasmolasi-arch/buggyprogram
"""

from __future__ import annotations

import base64
import sys

from composio import Composio

from repohealth.config import Config

PACKAGE_JSON = """\
{
  "name": "buggyprogram",
  "version": "1.0.0",
  "description": "Demo repo for RepoHealth — intentionally outdated deps.",
  "dependencies": {
    "express": "^4.17.1",
    "lodash": "4.17.20"
  }
}
"""

REQUIREMENTS_TXT = """\
flask==2.2.0
requests==2.28.0
"""

README = """\
# buggyprogram

A throwaway repo for the **RepoHealth** agent demo. Its dependencies are pinned
to old versions and it has a few neglected issues, so the agent has real work to
do: open bump PRs and close stale issues.
"""

ISSUES = [
    ("Login button unresponsive on mobile", "Reported a while ago, never triaged."),
    ("Docs are out of date", "The README examples no longer match the API."),
    ("Flaky test in CI", "test_checkout intermittently fails; needs investigation."),
]


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else "vasmolasi-arch/buggyprogram"
    owner, name = repo.split("/", 1)
    cfg = Config.from_env()
    client = Composio(api_key=cfg.composio_api_key)
    uid = cfg.composio_user_id

    # Manual execution requires an explicit toolkit version ("latest" is rejected).
    try:
        version = client.toolkits.get("github").meta.version
    except Exception:
        version = None

    def execute(slug: str, **arguments):
        schemas = client.tools._tool_schemas
        if slug not in schemas:
            schemas[slug] = client.tools.get_raw_composio_tool_by_slug(slug)
        kwargs = {"arguments": arguments, "user_id": uid}
        if version:
            kwargs["version"] = version
        else:
            kwargs["dangerously_skip_version_check"] = True
        resp = client.tools.execute(slug, **kwargs)
        if isinstance(resp, dict):
            if not resp.get("successful", True):
                raise RuntimeError(f"{slug} failed: {resp.get('error')}")
            return resp.get("data")
        if not getattr(resp, "successful", True):
            raise RuntimeError(f"{slug} failed: {getattr(resp, 'error', resp)}")
        return getattr(resp, "data", resp)

    def put_file(path: str, text: str) -> None:
        # Pass the existing blob sha if the file is already there (update path).
        sha = None
        try:
            data = execute("GITHUB_GET_REPOSITORY_CONTENT", owner=owner, repo=name, path=path)
            node = data.get("content") if isinstance(data, dict) and isinstance(data.get("content"), dict) else data
            sha = node.get("sha") if isinstance(node, dict) else None
        except Exception:
            pass  # file (or repo) doesn't exist yet — first write creates main
        args = dict(
            owner=owner, repo=name, path=path,
            message=f"seed: add {path}",
            content=base64.b64encode(text.encode()).decode("ascii"),
        )
        if sha:
            args["sha"] = sha
        execute("GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS", **args)
        print(f"  file  {path}")

    print(f"Seeding {repo} via Composio (user {uid})...")
    put_file("README.md", README)            # creates the initial commit + main
    put_file("package.json", PACKAGE_JSON)
    put_file("requirements.txt", REQUIREMENTS_TXT)

    for title, body in ISSUES:
        execute("GITHUB_CREATE_AN_ISSUE", owner=owner, repo=name, title=title, body=body)
        print(f"  issue {title!r}")

    print("Done. Now run:  python3 -m repohealth run --repo", repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
