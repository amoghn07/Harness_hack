"""Configuration, read from environment with safe defaults.

The whole point of Phase 1 is that it runs with nothing set — the mock backends
need no credentials. Setting REPOHEALTH_GITHUB=composio or REPOHEALTH_STORE=
clickhouse flips individual pieces to their real implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (stdlib only). Lines are KEY=VALUE; surrounding
    quotes are stripped; existing environment variables are NOT overridden.
    Silently does nothing if the file is absent."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


@dataclass
class Config:
    # "mock" | "composio"
    github_backend: str = "mock"
    # "sqlite" | "clickhouse"
    store_backend: str = "sqlite"
    # "mock" | "http" — where to resolve latest dependency versions from.
    registry_backend: str = "mock"

    # SQLite (mock store) location — the agent's persistent memory.
    sqlite_path: str = "data/repohealth.db"

    # ClickHouse (real store) — only read when store_backend == "clickhouse".
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_database: str = "repohealth"

    # Composio (real GitHub) — only read when github_backend == "composio".
    composio_api_key: str = ""
    # Identifies the end-user whose connected GitHub account Composio uses, and
    # the OAuth auth config backing it.
    composio_user_id: str = "repohealth"
    composio_auth_config_id: str = ""

    # ---- Phase 2: score & detect -------------------------------------------
    # Below this 0–100 health score, the cycle escalates to Bedrock for
    # remediation analysis. At or above it, inference is skipped (cost ≈ 0 on
    # healthy repos — the whole point of the gate).
    score_threshold: int = 60

    # Reasoning backend: "mock" (deterministic, offline, no creds) | "bedrock".
    inference_backend: str = "mock"

    # AWS Bedrock (real inference) — only read when inference_backend=="bedrock".
    # boto3 also honors a shared ~/.aws/credentials profile; these env vars take
    # precedence when set.
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    # Bedrock Claude model ID. Defaults to the latest Opus; many accounts must
    # use a region-prefixed inference-profile ID (e.g. us.anthropic.claude-opus-4-8).
    bedrock_model_id: str = "anthropic.claude-opus-4-8"
    # Bedrock long-term API key (the `ABSK...` bearer token). When set, boto3
    # signs bedrock-runtime calls with it via AWS_BEARER_TOKEN_BEDROCK — no
    # AWS_ACCESS_KEY_ID/SECRET pair required.
    aws_bearer_token_bedrock: str = ""

    # ---- Phase 2: tracing / evaluation observability -----------------------
    # "none" | "langfuse". When "langfuse", the analyzer is wrapped to log each
    # generation + the deterministic eval scores. Purely observability — the
    # safety gate runs regardless.
    tracing_backend: str = "none"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ---- Phase 3: act & publish --------------------------------------------
    # How the three real actions (bump PRs, stale-issue closes, weekly report)
    # are executed. "mock" records intended actions offline; "composio" hits
    # GitHub for real (reuses the same connected account as the connector).
    actions_backend: str = "mock"

    # Where the weekly report is published: a GitHub Discussion or a Notion page.
    report_target: str = "github"          # "github" | "notion"
    report_repo: str = ""                  # owner/name to post to; "" = monitored repo
    github_discussion_category: str = ""   # category id for GITHUB_CREATE_A_DISCUSSION

    # Notion (only when report_target == "notion").
    notion_api_key: str = ""
    notion_parent_page_id: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        return cls(
            github_backend=os.getenv("REPOHEALTH_GITHUB", "mock"),
            store_backend=os.getenv("REPOHEALTH_STORE", "sqlite"),
            registry_backend=os.getenv("REPOHEALTH_REGISTRY", "mock"),
            sqlite_path=os.getenv("REPOHEALTH_SQLITE_PATH", "data/repohealth.db"),
            clickhouse_host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            clickhouse_user=os.getenv("CLICKHOUSE_USER", "default"),
            clickhouse_password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            clickhouse_database=os.getenv("CLICKHOUSE_DATABASE", "repohealth"),
            composio_api_key=os.getenv("COMPOSIO_API_KEY", ""),
            composio_user_id=os.getenv("COMPOSIO_USER_ID", "repohealth"),
            composio_auth_config_id=os.getenv("COMPOSIO_AUTH_CONFIG_ID", ""),
            score_threshold=int(os.getenv("REPOHEALTH_SCORE_THRESHOLD", "60")),
            inference_backend=os.getenv("REPOHEALTH_INFERENCE", "mock"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            aws_session_token=os.getenv("AWS_SESSION_TOKEN", ""),
            bedrock_model_id=os.getenv(
                "BEDROCK_MODEL_ID", "anthropic.claude-opus-4-8"
            ),
<<<<<<< HEAD
            aws_bearer_token_bedrock=os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""),
            tracing_backend=os.getenv("REPOHEALTH_TRACING", "none"),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
            langfuse_host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
=======
            actions_backend=os.getenv("REPOHEALTH_ACTIONS", "mock"),
            report_target=os.getenv("REPOHEALTH_REPORT_TARGET", "github"),
            report_repo=os.getenv("REPOHEALTH_REPORT_REPO", ""),
            github_discussion_category=os.getenv("GITHUB_DISCUSSION_CATEGORY", ""),
            notion_api_key=os.getenv("NOTION_API_KEY", ""),
            notion_parent_page_id=os.getenv("NOTION_PARENT_PAGE_ID", ""),
>>>>>>> 3efa6b8c3ff59acf1a87a2c8f746ba5a7f9c0311
        )
