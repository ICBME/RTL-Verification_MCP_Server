"""RTL simulation HTTP API server."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).parent))
from config import load_config
from executor import CommandExecutor
from command_api import build_command_api_routes
from workspace_api import build_workspace_api_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve(config_path: str) -> Path:
    base = Path(__file__).parent
    return Path(config_path) if Path(config_path).is_absolute() else base / config_path


def build_app(config_path: str = "tools.toml") -> Starlette:
    cfg_path = _resolve(config_path)
    cfg = load_config(cfg_path)
    executor = CommandExecutor(cfg.ssh)
    routes = build_workspace_api_routes() + build_command_api_routes(executor)
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origin_regex="http://localhost:.*",
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
    return Starlette(debug=False, routes=routes, middleware=middleware)


def main() -> None:
    parser = argparse.ArgumentParser(description="RTL Simulation HTTP API Server")
    parser.add_argument("--config",    default="tools.toml")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host for HTTP API")
    parser.add_argument("--port", type=int, default=8999,
                        help="Bind port for HTTP API")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    app = build_app(config_path=args.config)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
