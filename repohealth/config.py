"""Configuration, read from environment with safe defaults.

The whole point of Phase 1 is that it runs with nothing set — the mock backends
need no credentials. Setting REPOHEALTH_GITHUB=composio or REPOHEALTH_STORE=
clickhouse flips individual pieces to their real implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # "mock" | "composio"
    github_backend: str = "mock"
    # "sqlite" | "clickhouse"
    store_backend: str = "sqlite"

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

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            github_backend=os.getenv("REPOHEALTH_GITHUB", "mock"),
            store_backend=os.getenv("REPOHEALTH_STORE", "sqlite"),
            sqlite_path=os.getenv("REPOHEALTH_SQLITE_PATH", "data/repohealth.db"),
            clickhouse_host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            clickhouse_user=os.getenv("CLICKHOUSE_USER", "default"),
            clickhouse_password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            clickhouse_database=os.getenv("CLICKHOUSE_DATABASE", "repohealth"),
            composio_api_key=os.getenv("COMPOSIO_API_KEY", ""),
        )
