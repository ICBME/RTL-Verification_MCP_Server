from __future__ import annotations

from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


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


async def _call_remote_tool(
    remote_server_url: str,
    tool_name: str,
    arguments: dict,
) -> dict:
    async with streamable_http_client(remote_server_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return _extract_tool_result(result)


async def ensure_remote_workspace(
    *,
    remote_server_url: str,
    topic_id: str,
    workspace_name: str,
    remote_base_dir: str,
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
    )


async def finalize_remote_sync(
    *,
    remote_server_url: str,
    topic_id: str,
) -> dict:
    return await _call_remote_tool(
        remote_server_url,
        "finalize_sync",
        {"topic_id": topic_id},
    )
