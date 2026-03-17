from __future__ import annotations

import time

from http_client import build_async_client


_PROBE_TTL_SECONDS = 10.0
_last_probe_ok_at: dict[str, float] = {}


def _api_base_url(remote_server_url: str) -> str:
    base = remote_server_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]
    return base


def _cache_key(remote_server_url: str, auth_token: str | None) -> str:
    return f"{remote_server_url}|{auth_token or ''}"


def _should_skip_probe(remote_server_url: str, auth_token: str | None) -> bool:
    key = _cache_key(remote_server_url, auth_token)
    last_ok = _last_probe_ok_at.get(key)
    if last_ok is None:
        return False
    return (time.monotonic() - last_ok) < _PROBE_TTL_SECONDS


def _mark_probe_ok(remote_server_url: str, auth_token: str | None) -> None:
    _last_probe_ok_at[_cache_key(remote_server_url, auth_token)] = time.monotonic()


async def probe_remote_server(
    remote_server_url: str,
    auth_token: str | None = None,
) -> None:
    if _should_skip_probe(remote_server_url, auth_token):
        return

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        async with build_async_client(
            headers=headers or None,
        ) as client:
            response = await client.get(f"{_api_base_url(remote_server_url)}/api/health")
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "ok":
                raise RuntimeError(f"Unexpected remote health response: {payload}")
    except Exception as exc:
        raise RuntimeError(
            f"Remote API server unreachable: {remote_server_url}"
        ) from exc

    _mark_probe_ok(remote_server_url, auth_token)


async def _post_remote_api(
    remote_server_url: str,
    path: str,
    arguments: dict,
    auth_token: str | None = None,
) -> dict:
    await probe_remote_server(remote_server_url, auth_token=auth_token)

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with build_async_client(
        headers=headers or None,
    ) as client:
        response = await client.post(
            f"{_api_base_url(remote_server_url)}{path}",
            json=arguments,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected remote API response: {result!r}")
        _mark_probe_ok(remote_server_url, auth_token)
        return result


async def execute_remote_command(
    *,
    remote_server_url: str,
    command: str,
    work_dir: str = ".",
    use_ssh: bool | None = None,
    timeout: int = 3600,
    auth_token: str | None = None,
) -> dict:
    return await _post_remote_api(
        remote_server_url,
        "/api/commands/execute",
        {
            "command": command,
            "work_dir": work_dir,
            "use_ssh": use_ssh,
            "timeout": timeout,
        },
        auth_token=auth_token,
    )


async def ensure_remote_workspace(
    *,
    remote_server_url: str,
    topic_id: str,
    workspace_name: str,
    source_revision: str | None,
    auth_token: str | None = None,
) -> dict:
    return await _post_remote_api(
        remote_server_url,
        "/api/workspaces/register",
        {
            "topic_id": topic_id,
            "workspace_name": workspace_name,
            "source_revision": source_revision,
        },
        auth_token=auth_token,
    )


async def get_remote_workspace(
    *,
    remote_server_url: str,
    topic_id: str,
    auth_token: str | None = None,
) -> dict:
    await probe_remote_server(remote_server_url, auth_token=auth_token)

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with build_async_client(
        headers=headers or None,
    ) as client:
        response = await client.get(f"{_api_base_url(remote_server_url)}/api/workspaces/{topic_id}")
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected remote API response: {result!r}")
        _mark_probe_ok(remote_server_url, auth_token)
        return result


async def finalize_remote_sync(
    *,
    remote_server_url: str,
    topic_id: str,
    source_revision: str | None,
    auth_token: str | None = None,
) -> dict:
    return await _post_remote_api(
        remote_server_url,
        f"/api/workspaces/{topic_id}/sync",
        {
            "source_revision": source_revision,
        },
        auth_token=auth_token,
    )
