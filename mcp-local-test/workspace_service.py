from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from common_config import CommonConfig, common_config_path
from git_workspace import (
    get_local_source_revision,
    sync_directory_to_repo,
    write_workspace_git_config,
)
from metadata import (
    WorkspaceMetadata,
    init_or_bind_workspace,
    load_metadata,
    save_metadata,
    utc_now_iso,
)
from remote import ensure_remote_workspace, finalize_remote_sync, get_remote_workspace


OnExisting = Literal["ask", "reuse", "overwrite", "fail"]


def require_value(name: str, value: Optional[str]) -> str:
    if value:
        return value
    raise ValueError(
        f"Missing required parameter '{name}'. "
        f"Provide it explicitly or save it in {common_config_path()}."
    )


def _repo_name_for(meta: WorkspaceMetadata) -> str:
    return f"workspace-{meta.topic_id}"


def _workspace_summary(meta: WorkspaceMetadata) -> dict:
    return {
        "topic_id": meta.topic_id,
        "workspace_name": meta.workspace_name,
        "repo_owner": meta.repo_owner,
        "repo_name": meta.repo_name,
        "repo_clone_url": meta.repo_clone_url,
        "repo_default_branch": meta.repo_default_branch,
        "git_config_path": meta.git_config_path,
        "last_source_revision": meta.last_source_revision,
        "last_sync_at": meta.last_sync_at,
    }


async def bind_workspace_with_repo(
    *,
    root_path: str,
    remote_server: str,
    remote_auth_token: Optional[str],
    on_existing: OnExisting,
) -> dict:
    root = Path(root_path).resolve()
    bind_result = init_or_bind_workspace(
        root_path=str(root),
        remote_server=remote_server,
        remote_host=None,
        remote_base_dir=None,
        auth_token=remote_auth_token,
        on_existing=on_existing,
    )
    if bind_result["status"] in {"needs_confirmation", "invalid_metadata"}:
        return bind_result

    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError("Workspace metadata was not created.")

    source_revision = get_local_source_revision(str(root))
    remote_workspace = await ensure_remote_workspace(
        remote_server_url=meta.remote_server,
        topic_id=meta.topic_id,
        workspace_name=meta.workspace_name,
        source_revision=source_revision,
        auth_token=remote_auth_token,
    )
    remote_info = remote_workspace.get("workspace") or {}
    repo_status = str(remote_workspace.get("repo_status") or "exists")
    repo_clone_url = str(remote_info.get("repo_clone_url") or "")
    repo_access_token = str(remote_info.get("repo_access_token") or "")
    repo_default_branch = str(remote_info.get("repo_default_branch") or "main")
    if not repo_clone_url or not repo_access_token:
        raise RuntimeError("Remote workspace registration did not return repo credentials.")

    meta.repo_owner = str(remote_info.get("repo_owner") or "")
    meta.repo_name = str(remote_info.get("repo_name") or _repo_name_for(meta))
    meta.repo_clone_url = repo_clone_url
    meta.repo_default_branch = repo_default_branch
    meta.repo_access_token = repo_access_token
    meta.git_config_path = write_workspace_git_config(
        root_path=str(root),
        access_token=repo_access_token,
    )
    if remote_auth_token:
        meta.auth_token = remote_auth_token
    save_metadata(root, meta)

    should_push = (
        bind_result["status"] == "created"
        or repo_status == "created"
        or meta.last_source_revision != source_revision
    )
    push_result = None
    if should_push:
        push_result = sync_directory_to_repo(
            root_path=str(root),
            remote_url=meta.repo_clone_url,
            branch=meta.repo_default_branch or "main",
            git_config_path=meta.git_config_path,
            commit_message=f"Sync workspace {meta.topic_id} from {source_revision}",
            dry_run=False,
        )
        await finalize_remote_sync(
            remote_server_url=meta.remote_server,
            topic_id=meta.topic_id,
            source_revision=source_revision,
            auth_token=remote_auth_token,
        )
        meta.last_source_revision = source_revision
        meta.last_sync_at = utc_now_iso()
        save_metadata(root, meta)

    return {
        "status": "ok",
        "bind": bind_result,
        "repo_status": repo_status,
        "workspace": _workspace_summary(meta),
        "remote_workspace": remote_workspace,
        "initial_push": push_result,
    }


async def ensure_workspace_synced(
    *,
    root_path: str,
    remote_auth_token: Optional[str] = None,
) -> dict:
    root = Path(root_path).resolve()
    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError("Workspace is not bound. Call bind_workspace first.")
    if not meta.repo_clone_url or not meta.repo_default_branch:
        raise RuntimeError("Workspace metadata is missing repository information.")
    if not meta.git_config_path:
        if not meta.repo_access_token:
            raise RuntimeError("Workspace metadata is missing git credentials.")
        meta.git_config_path = write_workspace_git_config(
            root_path=str(root),
            access_token=meta.repo_access_token,
        )
        save_metadata(root, meta)

    source_revision = get_local_source_revision(str(root))
    remote_workspace = await get_remote_workspace(
        remote_server_url=meta.remote_server,
        topic_id=meta.topic_id,
        auth_token=remote_auth_token or meta.auth_token,
    )
    remote_info = remote_workspace.get("workspace") or {}
    remote_revision = str(remote_info.get("source_revision") or "")
    local_revision = source_revision

    if meta.last_source_revision == local_revision and remote_revision == local_revision:
        return {
            "status": "ok",
            "synced": True,
            "pushed": False,
            "workspace": _workspace_summary(meta),
            "remote_workspace": remote_workspace,
            "source_revision": local_revision,
        }

    push_result = sync_directory_to_repo(
        root_path=str(root),
        remote_url=meta.repo_clone_url,
        branch=meta.repo_default_branch,
        git_config_path=meta.git_config_path,
        commit_message=f"Sync workspace {meta.topic_id} from {local_revision}",
        dry_run=False,
    )
    finalized = await finalize_remote_sync(
        remote_server_url=meta.remote_server,
        topic_id=meta.topic_id,
        source_revision=local_revision,
        auth_token=remote_auth_token or meta.auth_token,
    )
    meta.last_source_revision = local_revision
    meta.last_sync_at = utc_now_iso()
    if remote_auth_token:
        meta.auth_token = remote_auth_token
    save_metadata(root, meta)

    return {
        "status": "ok",
        "synced": True,
        "pushed": True,
        "workspace": _workspace_summary(meta),
        "source_revision": local_revision,
        "push": push_result,
        "remote_finalize": finalized,
    }
