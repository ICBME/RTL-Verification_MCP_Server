from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from common_config import CommonConfig, common_config_path
from gitea import GiteaClient
from git_workspace import get_local_source_revision, sync_directory_to_repo
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
        "last_source_revision": meta.last_source_revision,
        "last_sync_at": meta.last_sync_at,
    }


async def bind_workspace_with_repo(
    *,
    root_path: str,
    remote_server: str,
    gitea_base_url: str,
    gitea_token: str,
    repo_owner: Optional[str],
    repo_default_branch: str,
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
        gitea_base_url=gitea_base_url,
        repo_owner=repo_owner,
        repo_default_branch=repo_default_branch,
        on_existing=on_existing,
    )
    if bind_result["status"] in {"needs_confirmation", "invalid_metadata"}:
        return bind_result

    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError("Workspace metadata was not created.")

    repo_name = meta.repo_name or _repo_name_for(meta)
    async with GiteaClient(gitea_base_url, gitea_token) as client:
        repo_result = await client.ensure_repo(
            owner=repo_owner,
            repo_name=repo_name,
            description=f"Workspace mirror for {meta.workspace_name} ({meta.topic_id})",
            private=True,
            auto_init=False,
            default_branch=repo_default_branch,
        )

    repo = repo_result["repo"]
    meta.gitea_base_url = gitea_base_url
    meta.repo_owner = repo.owner
    meta.repo_name = repo.name
    meta.repo_clone_url = repo.clone_url
    meta.repo_default_branch = repo.default_branch or repo_default_branch
    if remote_auth_token:
        meta.auth_token = remote_auth_token
    save_metadata(root, meta)

    source_revision = get_local_source_revision(str(root))
    remote_workspace = await ensure_remote_workspace(
        remote_server_url=meta.remote_server,
        topic_id=meta.topic_id,
        workspace_name=meta.workspace_name,
        repo_owner=repo.owner,
        repo_name=repo.name,
        repo_clone_url=repo.clone_url,
        repo_default_branch=meta.repo_default_branch or repo_default_branch,
        source_revision=source_revision,
        auth_token=remote_auth_token,
    )

    should_push = (
        bind_result["status"] == "created"
        or repo_result["status"] == "created"
        or meta.last_source_revision != source_revision
    )
    push_result = None
    if should_push:
        push_result = sync_directory_to_repo(
            root_path=str(root),
            remote_url=repo.clone_url,
            branch=meta.repo_default_branch or repo_default_branch,
            auth_token=gitea_token,
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
        "repo_status": repo_result["status"],
        "workspace": _workspace_summary(meta),
        "remote_workspace": remote_workspace,
        "initial_push": push_result,
    }


async def ensure_workspace_synced(
    *,
    root_path: str,
    gitea_token: str,
    remote_auth_token: Optional[str] = None,
) -> dict:
    root = Path(root_path).resolve()
    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError("Workspace is not bound. Call bind_workspace first.")
    if not meta.repo_clone_url or not meta.repo_default_branch:
        raise RuntimeError("Workspace metadata is missing repository information.")

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
        auth_token=gitea_token,
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
