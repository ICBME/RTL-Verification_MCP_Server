"""
server.py – RTL simulation MCP server, built with FastMCP.

FastMCP derives tool names, JSON schemas, and descriptions directly from
function signatures and docstrings – no hand-written schema dicts needed.

Tool surface (5 tools, fixed):
    list_skills            – return skill index markdown
    load_skill             – load one simulator skill file
    list_simulators        – enumerate simulators + commands from config
    run_predefined_command – config-driven execution (template → shell → executor)
    execute_command        – arbitrary shell command          (shell → executor)

Execution flow:
    run_predefined_command(simulator, command, params, ...)
        → config lookup + render_template()  →  shell_cmd
        → executor.run()                                  ┐
                                                          ├─ ExecResult
    execute_command(command, ...)                         │
        → executor.run() ─────────────────────────────────┘
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Annotated, Optional
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from pydantic import Field

sys.path.insert(0, str(Path(__file__).parent))
from config import ServerConfig, SimulatorDef, load_config
from executor import CommandExecutor, extract_template_params, render_template
from skills import SkillsManager
from workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# ── Session ID ───────────────────────────────────────────────────
# 在上下文中传递mcp-session-id
_current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")


def _session() -> str:
    """Return the MCP session ID for the current request."""
    ssid = _current_session_id.get()
    if not ssid:
        raise RuntimeError(
            "No MCP session ID available. "
            "This server requires streamable-http transport."
        )
    return ssid

# ── Session context middleware ─────────────────────────────────────────────────

class SessionIDMiddleware:
    """
    Reads ``mcp-session-id`` from the incoming request headers and stores it
    in ``_current_session_id`` for the duration of that request.

    The header is set by FastMCP on the initialize response and echoed back
    by the client on every subsequent request.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            ssid = headers.get(b"mcp-session-id", b"").decode().strip()
            token = _current_session_id.set(ssid)
            try:
                await self.app(scope, receive, send)
            finally:
                _current_session_id.reset(token)
        else:
            await self.app(scope, receive, send)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve(config_path: str, skills_dir: str) -> tuple[Path, Path]:
    base = Path(__file__).parent
    cfg = Path(config_path) if Path(config_path).is_absolute() else base / config_path
    skl = Path(skills_dir)  if Path(skills_dir).is_absolute()  else base / skills_dir
    return cfg, skl


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
    """Config lookup + template render.  Raises ValueError on bad input."""
    sim = cfg.simulators.get(simulator)
    if sim is None:
        raise ValueError(
            f"Unknown simulator '{simulator}'. "
            f"Available: {', '.join(cfg.simulators)}. "
            "Call list_simulators to see options."
        )
    cmd_def = sim.get_command(command)
    if cmd_def is None:
        raise ValueError(
            f"Unknown command '{command}' for '{simulator}'. "
            f"Available: {', '.join(c.name for c in sim.commands)}."
        )
    return cmd_def.template, sim


# ── Server factory ─────────────────────────────────────────────────────────────

def build_server(
    config_path: str = "tools.toml",
    skills_dir:  str = "skills",
    workspaces_root: str = "~/rtl_workspaces",
) -> FastMCP:
    cfg_path, skl_path = _resolve(config_path, skills_dir)
    cfg        = load_config(cfg_path)
    executor   = CommandExecutor(cfg.ssh)
    skills_mgr = SkillsManager(skl_path)
    ws_mgr     = WorkspaceManager(workspaces_root)

    #mcp = FastMCP("rtl-sim-mcp",host="127.0.0.1", port=8999)
    mcp = FastMCP("rtl-sim-mcp")

    # ── Skills / discovery ────────────────────────────────────────────────────

    @mcp.tool()
    def list_skills() -> str:
        """Return the skill index (INDEX.md).
        Call first to discover available simulators before loading details."""
        return skills_mgr.list_skills()

    @mcp.tool()
    def load_skill(
        skill: Annotated[str, Field(description="Skill name without .md, e.g. 'vcs'")],
    ) -> str:
        """Load the detailed usage guide for one simulator skill.
        Read the returned markdown to learn which (command, params) to pass
        to run_predefined_command."""
        return skills_mgr.load_skill(skill)

    @mcp.tool()
    def list_simulators() -> str:
        """List every simulator defined in tools.toml with its command names,
        descriptions, and template parameters.
        Use to discover valid (simulator, command) pairs without loading a full skill."""
        return _simulators_markdown(cfg)

    # ── Workspace + sync ──────────────────────────────────────────────────────

    @mcp.tool()
    def init_workspace(
        topic: Annotated[
            str,
            Field(description=(
                "Short description of this task, e.g. 'uart-rx-verification'. "
                "Used to name the workspace directory."
            )),
        ],
        local_source: Annotated[
            str,
            Field(description=(
                "Local path to show in the rsync command hint, e.g. './my_project/'. "
                "Defaults to './' (current directory). "
                "The user substitutes their actual path when running the command."
            )),
        ] = "./",
    ) -> str:
        """Create (or reuse) the session workspace and return rsync/scp commands
        for the user to push source files directly from their local machine.

        File content is NEVER transmitted through MCP – the agent only forwards
        the sync command to the user, who runs it locally.

        Call this once per session before any file operations or simulation.
        If the workspace already exists for this session, the existing path is
        returned with a fresh sync command."""
        try:
            ws = ws_mgr.get_or_create(_session(), topic)
            # Update topic label if a new one is provided
            if topic and topic != ws.topic:
                ws = ws_mgr.set_topic(_session(), topic)
            sync_info = ws_mgr.build_sync_info(ws, cfg.sync, local_source)
            return "\n".join([
                ws.summary(),
                "",
                sync_info.summary(),
            ])
        except Exception as exc:
            return f"[error] {exc}"
        
    # ── Unified execution ─────────────────────────────────────────────────────

    @mcp.tool()
    async def run_predefined_command(
        simulator: Annotated[
            str,
            Field(description=(
                "Simulator name from tools.toml, e.g. 'vcs', 'iverilog', 'xcelium', 'questa'."
                " Call list_simulators to enumerate valid names."
            )),
        ],
        command: Annotated[
            str,
            Field(description=(
                "Command name within the simulator, e.g. 'compile', 'simulate'."
                " Call list_simulators to see available commands and their params."
            )),
        ],
        params: Annotated[
            dict[str, str],
            Field(
                description=(
                    "Template placeholder values, e.g. {\"files\": \"rtl/top.sv\", \"top\": \"tb\"}."
                    " Missing keys are silently removed from the rendered command."
                ),
            ),
        ] = {},
        work_dir: Annotated[
            Optional[str],
            Field(description="Override the simulator's default working directory."),
        ] = None,
        timeout: Annotated[
            int,
            Field(description="Execution timeout in seconds.", ge=1),
        ] = 3600,
    ) -> str:
        """Execute a pre-configured simulator command defined in tools.toml.
        Looks up the command template, substitutes params, then runs the resulting
        shell command through the same executor as execute_command.
        SSH routing is handled automatically based on the simulator's config.
        Prefer this over execute_command when the operation is covered by a template."""
        try:
            template, sim = _lookup(cfg, simulator, command)
        except ValueError as exc:
            return f"[error] {exc}"

        shell_cmd = render_template(template, params)
        result = await executor.run(
            shell_cmd,
            work_dir=work_dir or sim.work_dir,
            use_ssh=sim.use_ssh or None,   # True → SSH; False falls back to ssh.enabled
            timeout=timeout,
        )
        return result.formatted()

    @mcp.tool()
    async def execute_command(
        command: Annotated[str, Field(description="Full shell command to execute.")],
        work_dir: Annotated[
            str,
            Field(description="Working directory (default: '.')"),
        ] = ".",
        use_ssh: Annotated[
            Optional[bool],
            Field(description=(
                "true = run on host via SSH; false = run locally."
                " Omit to follow the global ssh.enabled setting in config."
            )),
        ] = None,
        timeout: Annotated[
            int,
            Field(description="Timeout in seconds.", ge=1),
        ] = 3600,
    ) -> str:
        """Execute an arbitrary shell command.
        Use for operations not covered by predefined templates: file management,
        log inspection, environment checks, one-off scripts, etc.
        Routes through the same executor as run_predefined_command."""
        result = await executor.run(
            command,
            work_dir=work_dir,
            use_ssh=use_ssh,
            timeout=timeout,
        )
        return result.formatted()

    return mcp

# ── Entry point ────────────────────────────────────────────────────────────────

    # Middleware stack (innermost first – add_middleware prepends):
     #   request → CORS（跨域处理）→ SessionID（ID获取） → FastMCP app
def main() -> None:
    parser = argparse.ArgumentParser(description="RTL Simulation MCP Server")
    parser.add_argument("--config",    default="tools.toml")
    parser.add_argument("--skills",    default="skills")
    parser.add_argument("--workspaces-root", dest="workspaces_root",
                    default="~/rtl_workspaces")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--transport", default="streamable-http",
                        choices=["stdio", "streamable-http"],
                        help="MCP transport")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host for streamable-http")
    parser.add_argument("--port", type=int, default=8999,
                        help="Bind port for streamable-http")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    mcp = build_server(config_path=args.config, 
                       skills_dir=args.skills, 
                       workspaces_root = args.workspaces_root
                       )

    if args.transport == "streamable-http":
        app = mcp.streamable_http_app()

        # 获取话题ID
        app.add_middleware(SessionIDMiddleware)

        # 跨域请求处理
        from starlette.middleware.cors import CORSMiddleware
        # 允许域名列表
        allowed_origins = [
        ]
        # 允许域名正则表达式
        allow_origin_regex = "http://localhost:.*"

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_origin_regex = allow_origin_regex,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"]
        )

        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")

if __name__ == "__main__":
    main()