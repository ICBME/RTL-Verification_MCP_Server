from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gitea_workspace import GiteaWorkspaceAdmin, GiteaWorkspaceConfig
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


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


async def api_health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def api_register_workspace(request: Request) -> JSONResponse:
    payload = await request.json()
    topic_id = str(payload["topic_id"])
    workspace_name = str(payload["workspace_name"])
    previous = _load_record(topic_id) or {}
    cfg = GiteaWorkspaceConfig.from_env()

    async with GiteaWorkspaceAdmin(cfg) as admin:
        repo_result = await admin.ensure_repo(topic=workspace_name, topic_id=topic_id)
        repo = repo_result["repo"]
        access_token = previous.get("repo_access_token")
        if not access_token:
            token_info = await admin.create_access_token(
                token_name=f"workspace-{topic_id}"
            )
            access_token = token_info.get("sha1")

    created_at = str(previous.get("created_at") or _utc_now_iso())
    record = {
        "topic_id": topic_id,
        "workspace_name": workspace_name,
        "repo_owner": str((repo.get("owner") or {}).get("login") or cfg.owner),
        "repo_name": str(repo["name"]),
        "repo_clone_url": str(repo.get("clone_url") or ""),
        "repo_default_branch": str(repo.get("default_branch") or cfg.default_branch),
        "repo_access_token": access_token,
        "source_revision": payload.get("source_revision"),
        "created_at": created_at,
        "updated_at": _utc_now_iso(),
        "last_sync_at": previous.get("last_sync_at"),
    }
    _save_record(topic_id, record)
    return JSONResponse(
        {
            "status": "ok",
            "repo_status": repo_result["status"],
            "workspace": record,
        }
    )


async def api_get_workspace(request: Request) -> JSONResponse:
    topic_id = str(request.path_params["topic_id"])
    record = _load_record(topic_id)
    if record is None:
        return JSONResponse(
            {"status": "error", "error": f"Workspace '{topic_id}' not found."},
            status_code=404,
        )
    return JSONResponse({"status": "ok", "workspace": record})


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


def build_workspace_api_routes() -> list[Route]:
    return [
        Route("/api/health", api_health, methods=["GET"]),
        Route("/api/workspaces/register", api_register_workspace, methods=["POST"]),
        Route("/api/workspaces/{topic_id:str}", api_get_workspace, methods=["GET"]),
        Route("/api/workspaces/{topic_id:str}/sync", api_mark_workspace_synced, methods=["POST"]),
    ]
