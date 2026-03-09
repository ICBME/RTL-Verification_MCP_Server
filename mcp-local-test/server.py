from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from remote import ensure_remote_workspace, finalize_remote_sync
from sync import sync_directory_with_rsync
from metadata import init_or_bind_workspace, load_metadata, save_metadata, utc_now_iso

mcp = FastMCP("LocalWorkspaceBridge")


@mcp.tool()
def bind_workspace(
    root_path: Annotated[
        str,
        Field(description="Absolute path of local source root. Metadata is stored in .mcp/workspace.json."),
    ],
    remote_server: Annotated[
        str,
        Field(description="Remote MCP server URL, for example http://127.0.0.1:8000/mcp."),
    ],
    remote_host: Annotated[
        str,
        Field(description="Remote host name or IP used by rsync/ssh."),
    ],
    remote_base_dir: Annotated[
        str,
        Field(description="Remote workspace root directory, for example /data/workspaces."),
    ],
    on_existing: Annotated[
        Literal["ask", "reuse", "overwrite", "fail"],
        Field(description="Policy when metadata already exists: ask, reuse, overwrite, or fail."),
    ] = "ask",
) -> dict:
    """
    Bind a local source directory to a topic_id and write .mcp/workspace.json.
    """
    result = init_or_bind_workspace(
        root_path=root_path,
        remote_server=remote_server,
        remote_host=remote_host,
        remote_base_dir=remote_base_dir,
        on_existing=on_existing,
    )
    return result


@mcp.tool()
async def sync_workspace(
    root_path: Annotated[
        str,
        Field(description="Absolute path of the local source directory that is already bound."),
    ],
    ssh_user: Annotated[
        str,
        Field(description="SSH user name for rsync."),
    ],
    ssh_port: Annotated[
        int,
        Field(description="SSH port number.", ge=1, le=65535),
    ] = 22,
    delete: Annotated[
        bool,
        Field(description="Delete remote files that do not exist locally (rsync --delete)."),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(description="Run sync as preview only without writing remote files."),
    ] = True,
    remote_base_dir_override: Annotated[
        Optional[str],
        Field(description="Optional override for remote_base_dir from local metadata."),
    ] = None,
) -> dict:
    """
    Sync flow:
    1. Read local metadata to get topic_id.
    2. Call remote MCP ensure_workspace.
    3. Push local files by rsync.
    4. Call remote MCP finalize_sync (when dry_run is false).
    """
    root = Path(root_path).resolve()
    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError(
            "Workspace is not bound. Call bind_workspace first."
        )

    remote_base_dir = remote_base_dir_override or meta.remote_base_dir

    ensure_ret = await ensure_remote_workspace(
        remote_server_url=meta.remote_server,
        topic_id=meta.topic_id,
        workspace_name=meta.workspace_name,
        remote_base_dir=remote_base_dir,
    )

    rsync_ret = sync_directory_with_rsync(
        root_path=str(root),
        ssh_user=ssh_user,
        ssh_host=meta.remote_host,
        remote_base_dir=remote_base_dir,
        ssh_port=ssh_port,
        delete=delete,
        dry_run=dry_run,
    )

    finalize_ret = None
    if not dry_run:
        finalize_ret = await finalize_remote_sync(
            remote_server_url=meta.remote_server,
            topic_id=meta.topic_id,
        )
        meta.last_sync_at = utc_now_iso()
        save_metadata(root, meta)

    return {
        "status": "ok",
        "topic_id": meta.topic_id,
        "ensure_workspace": ensure_ret,
        "rsync": rsync_ret,
        "finalize": finalize_ret,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
