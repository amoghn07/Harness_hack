"""Phase 3: act & publish — the three real actions, behind one interface.

When the score gate trips and Bedrock has a plan, these execute against GitHub
(via Composio) — or against the deterministic mock, the default:

    1. draft_dep_pr     -> open a bump PR on branch bot/bump-{pkg}-{ver},
                           body carries a changelog summary.
    2. close_stale_issue-> comment the stale notice, apply the `stale` label,
                           close the issue.
    3. publish_report   -> post the weekly Markdown report to a GitHub
                           Discussion (or a Notion page).

Same mock/real split as every other backend: MockActuator records what *would*
happen and returns deterministic URLs (offline, no creds); ComposioActuator is
the guided stub that calls real Composio tool actions. Flip with
REPOHEALTH_ACTIONS=composio.
"""

from __future__ import annotations

import abc
import base64
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Config
from .detect import OutdatedDep, StaleIssue

if TYPE_CHECKING:  # avoid an import cycle (report imports ActionResult from here)
    from .report import WeeklyReport

# The exact stale notice from the spec.
STALE_COMMENT = "Closing as stale after 90 days; reopen if still relevant"
STALE_LABEL = "stale"


@dataclass
class ActionResult:
    kind: str          # "pull_request" | "close_issue" | "report"
    target: str        # dep "eco:name" | issue id | report destination
    ok: bool
    url: str = ""      # link to the created PR / comment / discussion / page
    branch: str = ""   # PR branch (pull_request only)
    detail: str = ""


def changelog_url(dep: OutdatedDep) -> str:
    """Best-effort link to the dependency's release/version history."""
    if dep.ecosystem == "npm":
        return f"https://www.npmjs.com/package/{dep.name}?activeTab=versions"
    if dep.ecosystem == "pypi":
        return f"https://pypi.org/project/{dep.name}/{dep.latest_ver}/#history"
    return ""


def changelog_summary(dep: OutdatedDep) -> str:
    """One-paragraph changelog diff summary for the PR body (Phase 3 spec).

    Deterministic and offline. The real connector can enrich this with release
    notes pulled from the registry; the shape stays the same."""
    return (
        f"Bumps **{dep.name}** ({dep.ecosystem}) from `{dep.current_ver}` to "
        f"`{dep.latest_ver}`.\n\n"
        f"Review the changes between these versions: {changelog_url(dep)}"
    )


def pr_body(dep: OutdatedDep) -> str:
    return (
        f"## Dependency bump\n\n"
        f"{changelog_summary(dep)}\n\n"
        f"Edited file: `{dep.source_file}`\n\n"
        f"_Opened automatically by RepoHealth._"
    )


def bump_manifest(content: str, dep: OutdatedDep) -> str:
    """Return `content` with `dep`'s version line bumped current_ver -> latest_ver.

    Targets just this package's pin so unrelated versions are untouched; any npm
    range operator (^ ~ >=) is preserved. Returns the input unchanged if no
    matching pin is found (caller treats that as "nothing to commit")."""
    if not dep.current_ver or not dep.latest_ver:
        return content
    if dep.ecosystem == "npm":
        # "<name>": "<spec>" — replace the version inside the spec, keep operator.
        pattern = re.compile(
            r'("' + re.escape(dep.name) + r'"\s*:\s*")([^"]*)(")'
        )

        def _repl(m: re.Match) -> str:
            return m.group(1) + m.group(2).replace(dep.current_ver, dep.latest_ver) + m.group(3)

        new, n = pattern.subn(_repl, content)
        return new if n else content
    # pypi / requirements.txt — bump the pinned `name==ver` line.
    pattern = re.compile(
        r'(^' + re.escape(dep.name) + r'\s*==\s*)' + re.escape(dep.current_ver),
        re.MULTILINE,
    )
    new, n = pattern.subn(lambda m: m.group(1) + dep.latest_ver, content)
    return new if n else content


def _file_sha_and_text(data: Any) -> tuple[str | None, str | None]:
    """Pull (blob sha, decoded text) out of a GITHUB_GET_REPOSITORY_CONTENT payload.

    Mirrors the connector's tolerance for the toolkit nesting content under a
    `content` wrapper key."""
    node = data
    if isinstance(data, dict) and isinstance(data.get("content"), dict):
        node = data["content"]
    if not isinstance(node, dict):
        return None, None
    sha = node.get("sha")
    raw, enc = node.get("content"), node.get("encoding")
    if enc == "base64" and isinstance(raw, str):
        text = base64.b64decode(raw).decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        text = None
    return sha, text


class Actuator(abc.ABC):
    @abc.abstractmethod
    def draft_dep_pr(self, repo: str, dep: OutdatedDep) -> ActionResult: ...

    @abc.abstractmethod
    def close_stale_issue(self, repo: str, issue: StaleIssue) -> ActionResult: ...

    @abc.abstractmethod
    def publish_report(self, repo: str, report: "WeeklyReport") -> ActionResult: ...


class MockActuator(Actuator):
    """Deterministic, offline. Records intended actions and returns fake URLs so
    the full act-and-publish path is exercisable without GitHub or any creds."""

    def __init__(self) -> None:
        self.performed: list[ActionResult] = []
        self._pr_seq = 1000

    def draft_dep_pr(self, repo: str, dep: OutdatedDep) -> ActionResult:
        self._pr_seq += 1
        res = ActionResult(
            kind="pull_request",
            target=f"{dep.ecosystem}:{dep.name}",
            ok=True,
            url=f"https://github.com/{repo}/pull/{self._pr_seq}",
            branch=dep.branch,
            detail=f"{dep.current_ver} -> {dep.latest_ver}",
        )
        self.performed.append(res)
        return res

    def close_stale_issue(self, repo: str, issue: StaleIssue) -> ActionResult:
        res = ActionResult(
            kind="close_issue",
            target=str(issue.id),
            ok=True,
            url=f"https://github.com/{repo}/issues/{issue.id}",
            detail=f"commented + labeled '{STALE_LABEL}' + closed",
        )
        self.performed.append(res)
        return res

    def publish_report(self, repo: str, report: "WeeklyReport") -> ActionResult:
        res = ActionResult(
            kind="report",
            target=report.target,
            ok=True,
            url=f"mock://report/{repo}/{report.target}",
            detail=f"{len(report.markdown)} chars",
        )
        self.performed.append(res)
        return res


class ComposioActuator(Actuator):  # pragma: no cover - needs creds + connection
    """Real actions via Composio's GitHub toolkit (and Notion for the report).

    Guided stub — the action slugs and argument shapes are written inline; they
    mirror the read-side connector in `connectors/composio_github.py`. Wiring is
    mechanical once a GitHub account is connected (see `repohealth.connect`).

    PR flow (GitHub has no one-shot "open PR from a change"):
      1. GITHUB_GET_A_REFERENCE        -> base branch head SHA
      2. GITHUB_CREATE_A_REFERENCE     -> create refs/heads/bot/bump-{pkg}-{ver}
      3. GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS -> commit the bumped manifest
      4. GITHUB_CREATE_A_PULL_REQUEST  -> open the PR (body = changelog summary)
    """

    def __init__(self, cfg: Config) -> None:
        if not cfg.composio_api_key:
            raise ValueError("COMPOSIO_API_KEY is required for ComposioActuator.")
        try:
            from composio import Composio  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "composio is not installed. `pip install composio` or use "
                "REPOHEALTH_ACTIONS=mock."
            ) from exc
        self._cfg = cfg
        self._client = Composio(api_key=cfg.composio_api_key)
        self._user_id = cfg.composio_user_id
        # Manual execution requires an explicit toolkit version ("latest" is
        # rejected); resolve it once, allow an env pin (mirrors the connector).
        import os
        self._version = os.getenv("COMPOSIO_GITHUB_VERSION") or self._resolve_version()

    def _resolve_version(self) -> str | None:
        try:
            return self._client.toolkits.get("github").meta.version
        except Exception:
            return None

    def _exec(self, slug: str, **arguments: Any) -> Any:
        # SDK 1.x: tools.execute reads its schema cache and raises KeyError on the
        # custom-tools fallback unless the slug was fetched first. Prime it.
        schemas = self._client.tools._tool_schemas
        if slug not in schemas:
            schemas[slug] = self._client.tools.get_raw_composio_tool_by_slug(slug)
        kwargs: dict[str, Any] = {"arguments": arguments, "user_id": self._user_id}
        if self._version:
            kwargs["version"] = self._version
        else:
            kwargs["dangerously_skip_version_check"] = True
        resp = self._client.tools.execute(slug, **kwargs)
        if isinstance(resp, dict):
            if not resp.get("successful", True):
                raise RuntimeError(f"{slug} failed: {resp.get('error')}")
            return resp.get("data")
        if not getattr(resp, "successful", True):
            raise RuntimeError(f"{slug} failed: {getattr(resp, 'error', resp)}")
        return getattr(resp, "data", resp)

    def draft_dep_pr(self, repo: str, dep: OutdatedDep) -> ActionResult:
        owner, name = repo.split("/", 1)
        base = "main"
        head_sha = self._exec(
            "GITHUB_GET_A_REFERENCE", owner=owner, repo=name, ref=f"heads/{base}"
        )["object"]["sha"]
        self._exec(
            "GITHUB_CREATE_A_REFERENCE", owner=owner, repo=name,
            ref=f"refs/heads/{dep.branch}", sha=head_sha,
        )
        # Commit the bumped manifest onto the branch so the PR has a real diff
        # (GitHub rejects a PR whose head == base with "no commits between").
        file_data = self._exec(
            "GITHUB_GET_REPOSITORY_CONTENT", owner=owner, repo=name,
            path=dep.source_file, ref=dep.branch,
        )
        file_sha, original = _file_sha_and_text(file_data)
        bumped = bump_manifest(original or "", dep)
        if not original or bumped == original or not file_sha:
            raise RuntimeError(
                f"could not bump {dep.name} {dep.current_ver}->{dep.latest_ver} "
                f"in {dep.source_file} (no matching pin found)"
            )
        self._exec(
            "GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS", owner=owner, repo=name,
            path=dep.source_file, branch=dep.branch, sha=file_sha,
            message=f"bot: bump {dep.name} to {dep.latest_ver}",
            content=base64.b64encode(bumped.encode("utf-8")).decode("ascii"),
        )
        pr = self._exec(
            "GITHUB_CREATE_A_PULL_REQUEST", owner=owner, repo=name,
            title=f"bot: bump {dep.name} to {dep.latest_ver}",
            head=dep.branch, base=base, body=pr_body(dep),
        )
        return ActionResult(
            kind="pull_request", target=f"{dep.ecosystem}:{dep.name}", ok=True,
            url=pr.get("html_url", ""), branch=dep.branch,
            detail=f"{dep.current_ver} -> {dep.latest_ver}",
        )

    def close_stale_issue(self, repo: str, issue: StaleIssue) -> ActionResult:
        owner, name = repo.split("/", 1)
        self._exec(
            "GITHUB_CREATE_AN_ISSUE_COMMENT", owner=owner, repo=name,
            issue_number=issue.id, body=STALE_COMMENT,
        )
        self._exec(
            "GITHUB_ADD_LABELS_TO_AN_ISSUE", owner=owner, repo=name,
            issue_number=issue.id, labels=[STALE_LABEL],
        )
        self._exec(
            "GITHUB_UPDATE_AN_ISSUE", owner=owner, repo=name,
            issue_number=issue.id, state="closed",
        )
        return ActionResult(
            kind="close_issue", target=str(issue.id), ok=True,
            url=f"https://github.com/{repo}/issues/{issue.id}",
            detail=f"commented + labeled '{STALE_LABEL}' + closed",
        )

    def publish_report(self, repo: str, report: "WeeklyReport") -> ActionResult:
        if self._cfg.report_target == "notion":
            return self._publish_notion(repo, report)
        return self._publish_discussion(repo, report)

    def _publish_discussion(self, repo: str, report: "WeeklyReport") -> ActionResult:
        owner, name = (self._cfg.report_repo or repo).split("/", 1)
        # GitHub Discussions are GraphQL-only; Composio exposes it as an action.
        data = self._exec(
            "GITHUB_CREATE_A_DISCUSSION", owner=owner, repo=name,
            category_id=self._cfg.github_discussion_category,
            title=report.title, body=report.markdown,
        )
        return ActionResult(kind="report", target="github", ok=True,
                            url=data.get("html_url", ""))

    def _publish_notion(self, repo: str, report: "WeeklyReport") -> ActionResult:
        data = self._exec(
            "NOTION_CREATE_PAGE",
            parent_id=self._cfg.notion_parent_page_id,
            title=report.title, content=report.markdown,
        )
        return ActionResult(kind="report", target="notion", ok=True,
                            url=data.get("url", ""))


def build_actuator(cfg: Config) -> Actuator:
    if cfg.actions_backend == "mock":
        return MockActuator()
    if cfg.actions_backend == "composio":
        return ComposioActuator(cfg)
    raise ValueError(f"Unknown actions backend: {cfg.actions_backend!r}")
