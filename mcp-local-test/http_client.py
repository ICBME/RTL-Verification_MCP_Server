from __future__ import annotations

import os
from typing import Optional

import httpx


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def http2_enabled(explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return explicit
    return _env_flag("MCP_HTTP2", default=False)


def build_async_client(
    *,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    http2: Optional[bool] = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url or "",
        headers=headers,
        timeout=timeout,
        trust_env=False,
        http2=http2_enabled(http2),
    )
