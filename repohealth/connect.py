"""Composio GitHub connection helpers.

The real connector needs a GitHub account authorized on the Composio project.
This module creates (or reuses) a Composio-managed OAuth auth config, mints the
end-user authorization link, and checks whether a usable connection exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass
class ConnectionStatus:
    connected: bool
    account_id: str | None = None
    status: str | None = None


def _client(cfg: Config):
    from composio import Composio  # lazy import

    if not cfg.composio_api_key:
        raise ValueError("COMPOSIO_API_KEY is not set (.env).")
    return Composio(api_key=cfg.composio_api_key)


def ensure_auth_config(cfg: Config) -> str:
    """Return a GitHub auth_config_id, reusing an existing one or creating a
    Composio-managed-OAuth config."""
    client = _client(cfg)
    if cfg.composio_auth_config_id:
        return cfg.composio_auth_config_id
    existing = getattr(client.auth_configs.list(), "items", []) or []
    for a in existing:
        tk = getattr(a, "toolkit", None)
        slug = getattr(tk, "slug", None) if tk else None
        if (slug or "").upper() == "GITHUB":
            return a.id
    ac = client.auth_configs.create(
        toolkit="github", options={"type": "use_composio_managed_auth"}
    )
    return ac.id


def create_link(cfg: Config) -> str:
    """Mint the OAuth authorization URL for the configured user."""
    client = _client(cfg)
    ac_id = ensure_auth_config(cfg)
    req = client.connected_accounts.link(
        user_id=cfg.composio_user_id, auth_config_id=ac_id
    )
    return getattr(req, "redirect_url", "")


def check_connection(cfg: Config) -> ConnectionStatus:
    """Is there an ACTIVE GitHub connection for the configured user?"""
    client = _client(cfg)
    try:
        accts = client.connected_accounts.list(user_ids=[cfg.composio_user_id])
    except TypeError:
        accts = client.connected_accounts.list()
    for a in (getattr(accts, "items", accts) or []):
        tk = getattr(a, "toolkit", None)
        slug = getattr(tk, "slug", None) if tk else None
        status = (getattr(a, "status", "") or "").upper()
        if (slug or "").upper() == "GITHUB":
            return ConnectionStatus(
                connected=status in ("ACTIVE", "INITIATED") and status == "ACTIVE",
                account_id=getattr(a, "id", None),
                status=status,
            )
    return ConnectionStatus(connected=False)
