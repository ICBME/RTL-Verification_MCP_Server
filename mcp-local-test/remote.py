from __future__ import annotations

import time
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


_PROBE_TTL_SECONDS = 10.0
_last_probe_ok_at: dict[str, float] = {}


def _extract_tool_result(result: Any) -> dict:
    """
    兼容 MCP Python SDK 常见的 tool result 结构。
    优先返回结构化结果，其次回退到 data/content。
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data

    content = getattr(result, "content", None)
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        if text_parts:
            return {"content": "\n".join(text_parts)}

    if isinstance(result, dict):
        return result

    return {"result": str(result)}


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
        async with httpx.AsyncClient(
            headers=headers or None,
            trust_env=False,
        ) as client:
            async with streamable_http_client(
                remote_server_url,
                http_client=client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
    except Exception as exc:
        raise RuntimeError(
            f"Remote MCP server unreachable: {remote_server_url}"
        ) from exc

    _mark_probe_ok(remote_server_url, auth_token)


async def _call_remote_tool(
    remote_server_url: str,
    tool_name: str,
    arguments: dict,
    auth_token: str | None = None,
) -> dict:
    await probe_remote_server(remote_server_url, auth_token=auth_token)

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(
        headers=headers or None,
        trust_env=False,
    ) as client:
        async with streamable_http_client(
            remote_server_url,
            http_client=client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                _mark_probe_ok(remote_server_url, auth_token)
                return _extract_tool_result(result)


async def ensure_remote_workspace(
    *,
    remote_server_url: str,
    topic_id: str,
    workspace_name: str,
    remote_base_dir: str,
    auth_token: str | None = None,
) -> dict:
    """
    调用远程 MCP tool：ensure_workspace
    """
    return await _call_remote_tool(
        remote_server_url,
        "ensure_workspace",
        {
            "topic_id": topic_id,
            "workspace_name": workspace_name,
            "remote_base_dir": remote_base_dir,
        },
        auth_token=auth_token,
    )


async def finalize_remote_sync(
    *,
    remote_server_url: str,
    topic_id: str,
    auth_token: str | None = None,
) -> dict:
    return await _call_remote_tool(
        remote_server_url,
        "finalize_sync",
        {"topic_id": topic_id},
        auth_token=auth_token,
    )
