from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from common_config import CommonConfig, common_config_path
from remote import ensure_remote_workspace, finalize_remote_sync
from sync import TransferMethod, sync_directory_with_rsync
from metadata import init_or_bind_workspace, load_metadata, save_metadata, utc_now_iso

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


def _require_value(name: str, value: Optional[str]) -> str:
    if value:
        return value
    raise ValueError(
        f"缺少必需参数 `{name}`，请在调用参数中提供，"
        f"或先写入常用配置文件：{common_config_path()}"
    )


@mcp.tool()
def bind_workspace(
    root_path: Annotated[
        Optional[str],
        Field(description="Absolute path of local source root. If omitted, use common config."),
    ] = None,
    remote_server: Annotated[
        Optional[str],
        Field(description="Remote MCP server URL. If omitted, use common config."),
    ] = None,
    remote_host: Annotated[
        Optional[str],
        Field(description="Remote host name or IP used by rsync/ssh. If omitted, use common config."),
    ] = None,
    remote_base_dir: Annotated[
        Optional[str],
        Field(description="Remote workspace root directory. If omitted, use common config."),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for remote MCP server. Stored in metadata."),
    ] = None,
    on_existing: Annotated[
        Literal["ask", "reuse", "overwrite", "fail"],
        Field(description="Policy when metadata already exists: ask, reuse, overwrite, or fail."),
    ] = "ask",
) -> dict:
    """
    Bind a local source directory to a topic_id and write .mcp/workspace.json.
    """
    common = CommonConfig.load()
    resolved_root = _require_value("root_path", root_path or common.root_path)
    resolved_remote_server = _require_value("remote_server", remote_server or common.remote_server)
    resolved_remote_host = _require_value("remote_host", remote_host or common.remote_host)
    resolved_remote_base_dir = _require_value("remote_base_dir", remote_base_dir or common.remote_base_dir)
    resolved_auth_token = auth_token or common.auth_token

    result = init_or_bind_workspace(
        root_path=resolved_root,
        remote_server=resolved_remote_server,
        remote_host=resolved_remote_host,
        remote_base_dir=resolved_remote_base_dir,
        auth_token=resolved_auth_token,
        on_existing=on_existing,
    )

    root = Path(resolved_root).resolve()
    if resolved_auth_token:
        meta = load_metadata(root)
        if meta is not None and meta.auth_token != resolved_auth_token:
            meta.auth_token = resolved_auth_token
            save_metadata(root, meta)

    common.merge_updates(
        root_path=str(root),
        remote_server=resolved_remote_server,
        remote_host=resolved_remote_host,
        remote_base_dir=resolved_remote_base_dir,
        auth_token=resolved_auth_token,
    ).save()
    return result


@mcp.tool()
async def sync_workspace(
    root_path: Annotated[
        Optional[str],
        Field(description="Absolute path of local source directory. If omitted, use common config."),
    ] = None,
    ssh_user: Annotated[
        Optional[str],
        Field(description="SSH user name for rsync. If omitted, use common config."),
    ] = None,
    ssh_port: Annotated[
        Optional[int],
        Field(description="SSH port number. If omitted, use common config, fallback 22.", ge=1, le=65535),
    ] = None,
    ssh_key_path: Annotated[
        Optional[str],
        Field(description="Optional SSH private key path for rsync/ssh."),
    ] = None,
    ssh_key_passphrase: Annotated[
        Optional[str],
        Field(description="Optional passphrase for SSH private key."),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for remote MCP server; overrides metadata."),
    ] = None,
    transfer_method: Annotated[
        TransferMethod,
        Field(description="File transfer method: rsync, scp, or auto(fallback from rsync to scp)."),
    ] = "auto",
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
    common = CommonConfig.load()
    resolved_root = _require_value("root_path", root_path or common.root_path)
    resolved_ssh_user = _require_value("ssh_user", ssh_user or common.ssh_user)
    resolved_ssh_port = ssh_port if ssh_port is not None else (common.ssh_port if common.ssh_port is not None else 22)
    resolved_ssh_key_path = ssh_key_path or common.ssh_key_path

    root = Path(resolved_root).resolve()
    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError(
            "Workspace is not bound. Call bind_workspace first."
        )

    remote_base_dir = remote_base_dir_override or meta.remote_base_dir
    resolved_auth_token = auth_token or meta.auth_token or common.auth_token

    try:
        ensure_ret = await ensure_remote_workspace(
            remote_server_url=meta.remote_server,
            topic_id=meta.topic_id,
            workspace_name=meta.workspace_name,
            remote_base_dir=remote_base_dir,
            auth_token=resolved_auth_token,
        )
    except Exception as exc:
        raise RuntimeError(
            "远程 MCP 服务不可用或连接失败（ensure_workspace）。"
            f" server={meta.remote_server}; detail={_flatten_exception_messages(exc)}"
        ) from exc

    rsync_ret = sync_directory_with_rsync(
        root_path=str(root),
        ssh_user=resolved_ssh_user,
        ssh_host=meta.remote_host,
        remote_base_dir=remote_base_dir,
        ssh_port=resolved_ssh_port,
        ssh_key_path=resolved_ssh_key_path,
        ssh_key_passphrase=ssh_key_passphrase,
        transfer_method=transfer_method,
        delete=delete,
        dry_run=dry_run,
    )

    finalize_ret = None
    if not dry_run:
        try:
            finalize_ret = await finalize_remote_sync(
                remote_server_url=meta.remote_server,
                topic_id=meta.topic_id,
                auth_token=resolved_auth_token,
            )
        except Exception as exc:
            raise RuntimeError(
                "远程 MCP 服务不可用或连接失败（finalize_sync）。"
                f" server={meta.remote_server}; detail={_flatten_exception_messages(exc)}"
            ) from exc
        meta.last_sync_at = utc_now_iso()
        if resolved_auth_token:
            meta.auth_token = resolved_auth_token
        save_metadata(root, meta)
    elif resolved_auth_token and meta.auth_token != resolved_auth_token:
        meta.auth_token = resolved_auth_token
        save_metadata(root, meta)

    common.merge_updates(
        root_path=str(root),
        ssh_user=resolved_ssh_user,
        ssh_port=resolved_ssh_port,
        ssh_key_path=resolved_ssh_key_path,
        auth_token=resolved_auth_token,
    ).save()

    return {
        "status": "ok",
        "topic_id": meta.topic_id,
        "common_config_path": str(common_config_path()),
        "ensure_workspace": ensure_ret,
        "file_sync": rsync_ret,
        "finalize": finalize_ret,
        "transfer_method": rsync_ret.get("transfer_method"),
    }


if __name__ == "__main__":
    app = mcp.streamable_http_app()
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
