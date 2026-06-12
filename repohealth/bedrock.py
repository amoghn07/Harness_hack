"""Phase 2: remediation analysis via AWS Bedrock (Claude).

This is the *decide* step's expensive half — it only runs when the score gate
trips (see `orchestrator`), so inference cost stays near zero on healthy repos.

Given a `Detection` (the score + the specific offenders), the analyzer returns a
prioritized, human-readable remediation plan that Phase 3 turns into real
actions (PRs, issue closes, the weekly report).

Two backends, mock-by-default like the rest of the project:
  * MockBedrockAnalyzer  — deterministic, offline, no creds. Default.
  * BedrockAnalyzer      — real inference via Bedrock's provider-agnostic
                           Converse API (needs AWS creds + a model-enabled
                           region). The default model is Amazon Nova, which is
                           generally available with no use-case form; any
                           Converse-capable model works by setting
                           BEDROCK_MODEL_ID.

Flip with REPOHEALTH_INFERENCE=bedrock once AWS credentials exist.
"""

from __future__ import annotations

import abc
import json
import os
from dataclasses import dataclass, field

from .config import Config
from .detect import Detection

_SYSTEM_PROMPT = (
    "You are a repository-health remediation planner. You receive a repo's "
    "health score and the specific issues behind it. Produce a concise, "
    "prioritized action plan. Respond ONLY with JSON matching this shape: "
    '{"summary": str, "actions": [{"kind": "bump_dep"|"close_issue"|"other", '
    '"target": str, "rationale": str, "priority": "high"|"medium"|"low"}]}. '
    "The `target` field MUST be an exact identifier with no extra text: for "
    'bump_dep use "ecosystem:name" exactly as listed (e.g. "npm:express"), '
    "NOT the version or any description; for close_issue use only the bare "
    'numeric issue id (e.g. "101"). Put versions and reasoning in `rationale`.'
)


@dataclass
class Action:
    kind: str        # "bump_dep" | "close_issue" | "other"
    target: str      # dep name or issue id (as string)
    rationale: str
    priority: str    # "high" | "medium" | "low"


@dataclass
class Analysis:
    summary: str
    actions: list[Action] = field(default_factory=list)
    model: str = ""          # which backend/model produced this
    raw: str = ""            # raw model text, for debugging


def _detection_brief(detection: Detection) -> str:
    """Compact, deterministic description of the offenders for the prompt."""
    deps = "\n".join(
        f"  - {d.ecosystem}:{d.name} {d.current_ver} -> {d.latest_ver} "
        f"(branch {d.branch})"
        for d in detection.outdated_deps
    ) or "  (none)"
    issues = "\n".join(
        f"  - #{i.id} \"{i.title}\" open {i.age_days}d labels={i.labels}"
        for i in detection.stale_issues
    ) or "  (none)"
    return (
        f"Repo: {detection.repo}\n"
        f"Health score: {detection.score}/100 (threshold {detection.threshold})\n"
        f"Outdated dependencies:\n{deps}\n"
        f"Stale issues (>90d open):\n{issues}\n"
    )


class BedrockAnalyzerBase(abc.ABC):
    @abc.abstractmethod
    def analyze(self, detection: Detection) -> Analysis:
        raise NotImplementedError


class MockBedrockAnalyzer(BedrockAnalyzerBase):
    """Deterministic plan built straight from the detection — no model call.

    Mirrors what the real model is asked to produce, so the whole fetch -> score
    -> decide -> act loop is exercisable offline. The priorities are a simple
    heuristic (security-ish ecosystems and oldest issues first).
    """

    def analyze(self, detection: Detection) -> Analysis:
        actions: list[Action] = []
        for d in detection.outdated_deps:
            actions.append(
                Action(
                    kind="bump_dep",
                    target=f"{d.ecosystem}:{d.name}",
                    rationale=f"{d.current_ver} -> {d.latest_ver}; draft PR on {d.branch}",
                    priority="high" if d.ecosystem == "npm" else "medium",
                )
            )
        for i in detection.stale_issues:
            actions.append(
                Action(
                    kind="close_issue",
                    target=str(i.id),
                    rationale=f"Open {i.age_days}d with no resolution; close as stale.",
                    priority="medium" if i.age_days > 120 else "low",
                )
            )
        summary = (
            f"{detection.repo} scored {detection.score}/100. "
            f"{len(detection.outdated_deps)} dep bump(s) and "
            f"{len(detection.stale_issues)} stale issue(s) to address."
        )
        return Analysis(summary=summary, actions=actions, model="mock")


class BedrockAnalyzer(BedrockAnalyzerBase):  # pragma: no cover - needs AWS creds
    """Real Bedrock analyzer over the provider-agnostic Converse API.

    Uses boto3's `bedrock-runtime` `converse`, so the same request shape works
    for Amazon Nova (the default), Anthropic, Meta, Mistral, etc. — switching
    providers is just a BEDROCK_MODEL_ID change, no body rewrite. Credentials
    resolve via the standard boto3 chain (env vars, shared profile, instance
    role); the explicit keys in Config take precedence when set.
    """

    def __init__(self, cfg: Config) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "boto3 is not installed. `pip install boto3` or use "
                "REPOHEALTH_INFERENCE=mock."
            ) from exc
        # A Bedrock API key (ABSK… bearer token) is consumed by botocore via this
        # env var — it takes the place of an access-key/secret pair.
        if cfg.aws_bearer_token_bedrock:
            os.environ.setdefault(
                "AWS_BEARER_TOKEN_BEDROCK", cfg.aws_bearer_token_bedrock
            )
        kwargs: dict = {"region_name": cfg.aws_region}
        if cfg.aws_access_key_id and cfg.aws_secret_access_key:
            kwargs["aws_access_key_id"] = cfg.aws_access_key_id
            kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
            if cfg.aws_session_token:
                kwargs["aws_session_token"] = cfg.aws_session_token
        self._client = boto3.client("bedrock-runtime", **kwargs)
        self._model_id = cfg.bedrock_model_id

    def analyze(self, detection: Detection) -> Analysis:
        resp = self._client.converse(
            modelId=self._model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[
                {"role": "user",
                 "content": [{"text": _detection_brief(detection)}]}
            ],
            inferenceConfig={"maxTokens": 1024, "temperature": 0},
        )
        # Converse: output.message.content is a list of blocks; take first text.
        text = ""
        for block in resp.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                text = block["text"]
                break
        return _parse_analysis(text, model=self._model_id)


def _parse_analysis(text: str, model: str) -> Analysis:
    """Parse the model's JSON reply; degrade gracefully if it isn't clean JSON."""
    raw = text
    # Models sometimes wrap JSON in prose or fences; grab the outermost object.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return Analysis(summary=raw.strip()[:280] or "(no analysis)", model=model,
                        raw=raw)
    actions = [
        Action(
            kind=a.get("kind", "other"),
            target=str(a.get("target", "")),
            rationale=a.get("rationale", ""),
            priority=a.get("priority", "medium"),
        )
        for a in data.get("actions", [])
        if isinstance(a, dict)
    ]
    return Analysis(summary=data.get("summary", ""), actions=actions, model=model,
                    raw=raw)


def build_analyzer(cfg: Config) -> BedrockAnalyzerBase:
    if cfg.inference_backend == "mock":
        base: BedrockAnalyzerBase = MockBedrockAnalyzer()
    elif cfg.inference_backend == "bedrock":
        base = BedrockAnalyzer(cfg)
    else:
        raise ValueError(f"Unknown inference backend: {cfg.inference_backend!r}")

    if cfg.tracing_backend == "langfuse":
        # Lazy import: tracing.py imports this module, so importing it at module
        # load would be circular.
        from .tracing import LangfuseAnalyzer

        return LangfuseAnalyzer(base, cfg)
    if cfg.tracing_backend != "none":
        raise ValueError(f"Unknown tracing backend: {cfg.tracing_backend!r}")
    return base
