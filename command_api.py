from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from executor import CommandExecutor


def build_command_api_routes(executor: CommandExecutor) -> list[Route]:
    async def api_execute_command(request: Request) -> JSONResponse:
        payload = await request.json()
        command = str(payload["command"])
        work_dir = str(payload.get("work_dir") or ".")
        timeout = int(payload.get("timeout") or 3600)
        use_ssh_raw = payload.get("use_ssh")
        use_ssh = None if use_ssh_raw is None else bool(use_ssh_raw)

        result = await executor.run(
            command,
            work_dir=work_dir,
            use_ssh=use_ssh,
            timeout=timeout,
        )
        return JSONResponse(
            {
                "status": "ok",
                "result": {
                    "command": result.command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "success": result.success,
                    "formatted": result.formatted(),
                },
            }
        )

    return [
        Route("/api/commands/execute", api_execute_command, methods=["POST"]),
    ]
