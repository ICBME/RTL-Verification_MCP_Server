"""RTL simulation MCP server."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Optional
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).parent))
from config import ServerConfig, SimulatorDef, load_config
from executor import CommandExecutor, extract_template_params, render_template
from skills import SkillsManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            "Call get_skill(name='simulators') to see options."
        )
    cmd_def = sim.get_command(command)
    if cmd_def is None:
        raise ValueError(
            f"Unknown command '{command}' for '{simulator}'. "
            f"Available: {', '.join(c.name for c in sim.commands)}."
        )
    return cmd_def.template, sim


def _remote_workspace_dir(remote_base_dir: str, topic_id: str) -> Path:
    base_dir = Path(remote_base_dir).expanduser().resolve()
    return (base_dir / topic_id).resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Server factory ─────────────────────────────────────────────────────────────

def build_server(
    config_path: str = "tools.toml",
    skills_dir:  str = "skills",
) -> FastMCP:
    cfg_path, skl_path = _resolve(config_path, skills_dir)
    cfg        = load_config(cfg_path)
    executor   = CommandExecutor(cfg.ssh)
    skills_mgr = SkillsManager(skl_path)

    #mcp = FastMCP("rtl-sim-mcp",host="127.0.0.1", port=8999)
    mcp = FastMCP("rtl-sim-mcp")

    # ── Skills / discovery ────────────────────────────────────────────────────

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
        """Return skill guidance for the agent.
        - Omit `name` to get the index and loading hints.
        - Pass `name='simulators'` to get simulator and command catalog.
        - Pass a skill name such as `vcs` to load that skill markdown."""
        if not name:
            return skills_mgr.skills_index()
        if name.strip().lower() == "simulators":
            return _simulators_markdown(cfg)
        return skills_mgr.load_skill(name)

    # ── Unified execution ─────────────────────────────────────────────────────

    @mcp.tool()
    async def run_predefined_command(
        simulator: Annotated[
            str,
            Field(description=(
                "Simulator name from tools.toml, e.g. 'vcs', 'iverilog', 'xcelium', 'questa'."
                " Call get_skill(name='simulators') to enumerate valid names."
            )),
        ],
        command: Annotated[
            str,
            Field(description=(
                "Command name within the simulator, e.g. 'compile', 'simulate'."
                " Call get_skill(name='simulators') to see available commands and params."
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

    # ── Local server API surface ──────────────────────────────────────────────

    @mcp.custom_route("/api/health", methods=["GET"], include_in_schema=False)
    async def api_health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/api/workspaces/ensure", methods=["POST"], include_in_schema=False)
    async def api_ensure_workspace(request: Request) -> JSONResponse:
        payload = await request.json()
        topic_id = str(payload["topic_id"])
        workspace_name = str(payload["workspace_name"])
        remote_base_dir = str(payload["remote_base_dir"])

        workspace_dir = _remote_workspace_dir(remote_base_dir, topic_id)
        src_dir = workspace_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        meta_path = workspace_dir / ".workspace_meta.json"
        previous: dict[str, object] = {}
        if meta_path.exists():
            try:
                previous = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                previous = {}

        created_at = previous.get("created_at") or _utc_now_iso()
        metadata = {
            "topic_id": topic_id,
            "workspace_name": workspace_name,
            "remote_base_dir": str(Path(remote_base_dir).expanduser().resolve()),
            "workspace_dir": str(workspace_dir),
            "src_dir": str(src_dir),
            "created_at": created_at,
            "updated_at": _utc_now_iso(),
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        return JSONResponse(
            {
                "status": "ok",
                "topic_id": topic_id,
                "workspace_dir": str(workspace_dir),
                "src_dir": str(src_dir),
                "metadata_path": str(meta_path),
            }
        )

    @mcp.custom_route("/api/workspaces/finalize", methods=["POST"], include_in_schema=False)
    async def api_finalize_sync(request: Request) -> JSONResponse:
        payload = await request.json()
        topic_id = str(payload["topic_id"])
        remote_base_dir = str(payload.get("remote_base_dir") or "/tmp/remote-workspaces")

        workspace_dir = _remote_workspace_dir(remote_base_dir, topic_id)
        meta_path = workspace_dir / ".workspace_meta.json"
        if not meta_path.exists():
            return JSONResponse(
                {
                    "status": "error",
                    "error": (
                        f"Remote workspace metadata is missing for topic_id={topic_id}. "
                        "Call ensure workspace API first."
                    ),
                },
                status_code=404,
            )

        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        last_sync_at = _utc_now_iso()
        metadata["last_sync_at"] = last_sync_at
        metadata["updated_at"] = last_sync_at
        meta_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        return JSONResponse(
            {
                "status": "ok",
                "topic_id": topic_id,
                "workspace_dir": str(workspace_dir),
                "metadata_path": str(meta_path),
                "last_sync_at": last_sync_at,
            }
        )

    return mcp

# ── Entry point ────────────────────────────────────────────────────────────────

    # Middleware stack (innermost first – add_middleware prepends):
     #   request → CORS（跨域处理) → FastMCP app
def main() -> None:
    parser = argparse.ArgumentParser(description="RTL Simulation MCP Server")
    parser.add_argument("--config",    default="tools.toml")
    parser.add_argument("--skills",    default="skills")
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

    mcp = build_server(config_path=args.config, skills_dir=args.skills)

    if args.transport == "streamable-http":
        app = mcp.streamable_http_app()

        # 跨域请求处理
        from starlette.middleware.cors import CORSMiddleware
        # 允许域名列表
        allowed_origins = [
        ]
        # 允许域名正则表达式
        allow_origin_regex = "http://localhost:.*"

        app.add_middleware(
            CORSMiddleware,
            #allow_origins=allowed_origins,
            allow_origin_regex = allow_origin_regex,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )

        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
