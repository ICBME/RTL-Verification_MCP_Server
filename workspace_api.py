from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_api_root() -> Path:
    return Path(__file__).resolve().parent / ".mcp" / "workspace_api"


def _workspace_record_path(topic_id: str) -> Path:
    return _workspace_api_root() / f"{topic_id}.json"


def _load_record(topic_id: str) -> dict[str, Any] | None:
    path = _workspace_record_path(topic_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_record(topic_id: str, payload: dict[str, Any]) -> None:
    root = _workspace_api_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _workspace_record_path(topic_id)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def register_workspace_api_routes(mcp: FastMCP) -> None:
    @mcp.custom_route("/api/health", methods=["GET"], include_in_schema=False)
    async def api_health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/api/workspaces/register", methods=["POST"], include_in_schema=False)
    async def api_register_workspace(request: Request) -> JSONResponse:
        payload = await request.json()
        topic_id = str(payload["topic_id"])
        previous = _load_record(topic_id) or {}

        created_at = str(previous.get("created_at") or _utc_now_iso())
        record = {
            "topic_id": topic_id,
            "workspace_name": str(payload["workspace_name"]),
            "repo_owner": str(payload["repo_owner"]),
            "repo_name": str(payload["repo_name"]),
            "repo_clone_url": str(payload["repo_clone_url"]),
            "repo_default_branch": str(payload.get("repo_default_branch") or "main"),
            "source_revision": payload.get("source_revision"),
            "created_at": created_at,
            "updated_at": _utc_now_iso(),
            "last_sync_at": previous.get("last_sync_at"),
        }
        _save_record(topic_id, record)
        return JSONResponse({"status": "ok", "workspace": record})

    @mcp.custom_route("/api/workspaces/{topic_id}", methods=["GET"], include_in_schema=False)
    async def api_get_workspace(request: Request) -> JSONResponse:
        topic_id = str(request.path_params["topic_id"])
        record = _load_record(topic_id)
        if record is None:
            return JSONResponse(
                {"status": "error", "error": f"Workspace '{topic_id}' not found."},
                status_code=404,
            )
        return JSONResponse({"status": "ok", "workspace": record})

    @mcp.custom_route("/api/workspaces/{topic_id}/sync", methods=["POST"], include_in_schema=False)
    async def api_mark_workspace_synced(request: Request) -> JSONResponse:
        topic_id = str(request.path_params["topic_id"])
        record = _load_record(topic_id)
        if record is None:
            return JSONResponse(
                {"status": "error", "error": f"Workspace '{topic_id}' not found."},
                status_code=404,
            )

        payload = await request.json()
        record["source_revision"] = payload.get("source_revision")
        record["last_sync_at"] = _utc_now_iso()
        record["updated_at"] = record["last_sync_at"]
        _save_record(topic_id, record)
        return JSONResponse({"status": "ok", "workspace": record})
