from __future__ import annotations

from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from common_config import CommonConfig, common_config_path
from workspace_service import bind_workspace_with_repo, ensure_workspace_synced, require_value

mcp = FastMCP("LocalWorkspaceBridge")


def _flatten_exception_messages(exc: BaseException) -> str:
    parts: list[str] = []

    def _walk(err: BaseException) -> None:
        children = getattr(err, "exceptions", None)
        if isinstance(children, tuple) and children:
            for child in children:
                if isinstance(child, BaseException):
                    _walk(child)
            return

        msg = str(err).strip()
        if msg:
            parts.append(f"{err.__class__.__name__}: {msg}")
        else:
            parts.append(err.__class__.__name__)

    _walk(exc)
    return " | ".join(parts) if parts else exc.__class__.__name__


@mcp.tool()
async def bind_workspace(
    root_path: Annotated[
        Optional[str],
        Field(description="Absolute path of local source root. If omitted, use common config."),
    ] = None,
    remote_server: Annotated[
        Optional[str],
        Field(description="Remote MCP server URL. If omitted, use common config."),
    ] = None,
    gitea_base_url: Annotated[
        Optional[str],
        Field(description="Gitea base URL, for example http://127.0.0.1:3000."),
    ] = None,
    repo_owner: Annotated[
        Optional[str],
        Field(description="Target Gitea user or organization that owns workspace repos."),
    ] = None,
    repo_default_branch: Annotated[
        Optional[str],
        Field(description="Default branch for workspace repositories. Defaults to 'main'."),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for remote MCP server. Stored in metadata."),
    ] = None,
    gitea_token: Annotated[
        Optional[str],
        Field(description="Gitea access token used for repo management and git push."),
    ] = None,
    on_existing: Annotated[
        Literal["ask", "reuse", "overwrite", "fail"],
        Field(description="Policy when metadata already exists: ask, reuse, overwrite, or fail."),
    ] = "ask",
) -> dict:
    """
    Bind a local source directory to a workspace repository and push the initial snapshot.
    """
    common = CommonConfig.load()
    resolved_root = require_value("root_path", root_path or common.root_path)
    resolved_remote_server = require_value("remote_server", remote_server or common.remote_server)
    resolved_gitea_base_url = require_value("gitea_base_url", gitea_base_url or common.gitea_base_url)
    resolved_gitea_token = require_value("gitea_token", gitea_token or common.gitea_token)
    resolved_repo_owner = repo_owner or common.repo_owner
    resolved_repo_default_branch = repo_default_branch or common.repo_default_branch or "main"
    resolved_auth_token = auth_token or common.auth_token

    result = await bind_workspace_with_repo(
        root_path=resolved_root,
        remote_server=resolved_remote_server,
        gitea_base_url=resolved_gitea_base_url,
        gitea_token=resolved_gitea_token,
        repo_owner=resolved_repo_owner,
        repo_default_branch=resolved_repo_default_branch,
        remote_auth_token=resolved_auth_token,
        on_existing=on_existing,
    )

    common.merge_updates(
        root_path=resolved_root,
        remote_server=resolved_remote_server,
        gitea_base_url=resolved_gitea_base_url,
        repo_owner=resolved_repo_owner,
        repo_default_branch=resolved_repo_default_branch,
        gitea_token=resolved_gitea_token,
        auth_token=resolved_auth_token,
    ).save()
    return result


@mcp.tool()
async def sync_workspace(
    root_path: Annotated[
        Optional[str],
        Field(description="Absolute path of local source directory. If omitted, use common config."),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for remote MCP server; overrides metadata."),
    ] = None,
    gitea_token: Annotated[
        Optional[str],
        Field(description="Gitea access token used for git push. If omitted, use common config."),
    ] = None,
) -> dict:
    """
    Compare local source revision with the remote workspace state and push when needed.
    """
    common = CommonConfig.load()
    resolved_root = require_value("root_path", root_path or common.root_path)
    resolved_auth_token = auth_token or common.auth_token
    resolved_gitea_token = require_value("gitea_token", gitea_token or common.gitea_token)

    try:
        result = await ensure_workspace_synced(
            root_path=resolved_root,
            gitea_token=resolved_gitea_token,
            remote_auth_token=resolved_auth_token,
        )
    except Exception as exc:
        raise RuntimeError(
            "Remote workspace sync failed. "
            f"detail={_flatten_exception_messages(exc)}"
        ) from exc

    common.merge_updates(
        root_path=resolved_root,
        gitea_token=resolved_gitea_token,
        auth_token=resolved_auth_token,
    ).save()
    return {
        "status": "ok",
        "common_config_path": str(common_config_path()),
        "workspace": result.get("workspace"),
        "pushed": result.get("pushed"),
        "source_revision": result.get("source_revision"),
        "detail": result,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
