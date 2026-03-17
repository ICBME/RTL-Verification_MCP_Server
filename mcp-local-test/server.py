from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from common_config import CommonConfig, common_config_path
from metadata import load_metadata
from remote import execute_remote_command
from workspace_service import bind_workspace_with_repo, ensure_workspace_synced, require_value

PARENT_DIR = Path(__file__).resolve().parents[1]
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from config import ServerConfig, SimulatorDef, load_config
from executor import extract_template_params, render_template
from skills import SkillsManager

mcp = FastMCP("LocalWorkspaceBridge")

_CFG_PATH = PARENT_DIR / "tools.toml"
_SKILLS_DIR = PARENT_DIR / "skills"
_CFG = load_config(_CFG_PATH)
_SKILLS = SkillsManager(_SKILLS_DIR)


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


def _simulators_markdown(cfg: ServerConfig) -> str:
    lines = ["# Configured Simulators\n"]
    for sim in cfg.simulators.values():
        label = "SSH → host" if sim.use_ssh else "local"
        lines += [
            f"## {sim.name}  [{label}]",
            sim.description,
            f"\nDefault work dir: `{sim.work_dir}`\n",
            "| command | description | template params |",
            "|---------|-------------|-----------------|",
        ]
        for cmd in sim.commands:
            params = ", ".join(f"`{p}`" for p in extract_template_params(cmd.template))
            lines.append(f"| `{cmd.name}` | {cmd.description} | {params or '—'} |")
        lines.append("")
    return "\n".join(lines)


def _lookup(cfg: ServerConfig, simulator: str, command: str) -> tuple[str, SimulatorDef]:
    sim = cfg.simulators.get(simulator)
    if sim is None:
        raise ValueError(
            f"Unknown simulator '{simulator}'. "
            f"Available: {', '.join(cfg.simulators)}. "
            "Call get_skill(name='simulators') to see options."
        )
    cmd_def = sim.get_command(command)
    if cmd_def is None:
        raise ValueError(
            f"Unknown command '{command}' for '{simulator}'. "
            f"Available: {', '.join(c.name for c in sim.commands)}."
        )
    return cmd_def.template, sim


def _resolve_remote_server(common: CommonConfig) -> str:
    if common.root_path:
        meta = load_metadata(Path(common.root_path).resolve())
        if meta is not None and meta.remote_server:
            return meta.remote_server
    return require_value("remote_server", common.remote_server)


async def _maybe_sync_bound_workspace(common: CommonConfig, auth_token: Optional[str]) -> dict | None:
    if not common.root_path:
        return None

    root = Path(common.root_path).resolve()
    meta = load_metadata(root)
    if meta is None:
        return None

    return await ensure_workspace_synced(
        root_path=str(root),
        remote_auth_token=auth_token or common.auth_token or meta.auth_token,
    )


@mcp.tool()
def get_skill(
    name: Annotated[
        Optional[str],
        Field(description=(
            "Skill name to load. Omit to return the skill index. "
            "Use 'simulators' to return the simulator catalog."
        )),
    ] = None,
) -> str:
    if not name:
        return _SKILLS.skills_index()
    if name.strip().lower() == "simulators":
        return _simulators_markdown(_CFG)
    return _SKILLS.load_skill(name)


@mcp.tool()
async def run_predefined_command(
    simulator: Annotated[
        str,
        Field(description=(
            "Simulator name from tools.toml. "
            "Call get_skill(name='simulators') to enumerate valid names."
        )),
    ],
    command: Annotated[
        str,
        Field(description=(
            "Command name within the simulator. "
            "Call get_skill(name='simulators') to see available commands and params."
        )),
    ],
    params: Annotated[
        dict[str, str],
        Field(description="Template placeholder values for the simulator command."),
    ] = {},
    work_dir: Annotated[
        Optional[str],
        Field(description="Override the simulator's default working directory."),
    ] = None,
    timeout: Annotated[
        int,
        Field(description="Execution timeout in seconds.", ge=1),
    ] = 3600,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for the remote HTTP API."),
    ] = None,
) -> str:
    common = CommonConfig.load()
    try:
        template, sim = _lookup(_CFG, simulator, command)
        sync_info = await _maybe_sync_bound_workspace(common, auth_token)
        shell_cmd = render_template(template, params)
        remote_server = _resolve_remote_server(common)
        response = await execute_remote_command(
            remote_server_url=remote_server,
            command=shell_cmd,
            work_dir=work_dir or sim.work_dir,
            use_ssh=sim.use_ssh or None,
            timeout=timeout,
            auth_token=auth_token or common.auth_token,
        )
    except Exception as exc:
        return f"[error] {_flatten_exception_messages(exc)}"

    result = response.get("result") or {}
    formatted = str(result.get("formatted") or "")
    if sync_info and sync_info.get("pushed"):
        return f"[workspace synced]\n{formatted}"
    return formatted


@mcp.tool()
async def execute_command(
    command: Annotated[str, Field(description="Full shell command to execute on the remote server.")],
    work_dir: Annotated[
        str,
        Field(description="Working directory on the remote host."),
    ] = ".",
    use_ssh: Annotated[
        Optional[bool],
        Field(description="true = run on host via SSH; false = run locally on remote server."),
    ] = None,
    timeout: Annotated[
        int,
        Field(description="Timeout in seconds.", ge=1),
    ] = 3600,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for the remote HTTP API."),
    ] = None,
) -> str:
    common = CommonConfig.load()
    try:
        sync_info = await _maybe_sync_bound_workspace(common, auth_token)
        remote_server = _resolve_remote_server(common)
        response = await execute_remote_command(
            remote_server_url=remote_server,
            command=command,
            work_dir=work_dir,
            use_ssh=use_ssh,
            timeout=timeout,
            auth_token=auth_token or common.auth_token,
        )
    except Exception as exc:
        return f"[error] {_flatten_exception_messages(exc)}"

    result = response.get("result") or {}
    formatted = str(result.get("formatted") or "")
    if sync_info and sync_info.get("pushed"):
        return f"[workspace synced]\n{formatted}"
    return formatted


@mcp.tool()
async def bind_workspace(
    root_path: Annotated[
        Optional[str],
        Field(description="Absolute path of local source root. If omitted, use common config."),
    ] = None,
    remote_server: Annotated[
        Optional[str],
        Field(description="Remote HTTP API base URL. If omitted, use common config."),
    ] = None,
    auth_token: Annotated[
        Optional[str],
        Field(description="Optional auth token for remote HTTP API. Stored in metadata."),
    ] = None,
    on_existing: Annotated[
        Literal["ask", "reuse", "overwrite", "fail"],
        Field(description="Policy when metadata already exists: ask, reuse, overwrite, or fail."),
    ] = "ask",
) -> dict:
    common = CommonConfig.load()
    resolved_root = require_value("root_path", root_path or common.root_path)
    resolved_remote_server = require_value("remote_server", remote_server or common.remote_server)
    resolved_auth_token = auth_token or common.auth_token

    result = await bind_workspace_with_repo(
        root_path=resolved_root,
        remote_server=resolved_remote_server,
        remote_auth_token=resolved_auth_token,
        on_existing=on_existing,
    )

    common.merge_updates(
        root_path=resolved_root,
        remote_server=resolved_remote_server,
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
        Field(description="Optional auth token for remote HTTP API; overrides metadata."),
    ] = None,
) -> dict:
    common = CommonConfig.load()
    resolved_root = require_value("root_path", root_path or common.root_path)
    resolved_auth_token = auth_token or common.auth_token

    try:
        result = await ensure_workspace_synced(
            root_path=resolved_root,
            remote_auth_token=resolved_auth_token,
        )
    except Exception as exc:
        raise RuntimeError(
            "Remote workspace sync failed. "
            f"detail={_flatten_exception_messages(exc)}"
        ) from exc

    common.merge_updates(
        root_path=resolved_root,
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
