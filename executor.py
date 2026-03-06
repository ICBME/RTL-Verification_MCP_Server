"""
executor.py – Unified command execution layer.

Both run_predefined_command and execute_command converge to a single
executor.run() call, ensuring consistent behaviour across SSH / local
execution, timeout handling, and result formatting.

Execution flow
──────────────
run_predefined_command tool
    → config lookup → render_template() → shell_cmd (str)
    → executor.run()                                    ┐
                                                        ├─ ExecResult
execute_command tool                                    │
    → shell_cmd (str, provided directly by agent)       │
    → executor.run() ───────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import asyncssh

from config import SSHConfig


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class ExecResult:
    command: str       # the final shell command that was executed
    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def formatted(self) -> str:
        """Human / agent-readable output block."""
        lines = [f"$ {self.command}"]
        if self.stdout.strip():
            lines.append(self.stdout.strip())
        if self.stderr.strip():
            lines.append(f"[stderr]\n{self.stderr.strip()}")
        lines.append(f"[exit {self.returncode}]")
        return "\n".join(lines)


# ── Template rendering (module-level, used by server.py too) ───────────────────

def render_template(template: str, params: dict[str, str]) -> str:
    """
    Substitute ``{param}`` placeholders in *template* with values from *params*.

    Rules:
    - Present, non-empty params are substituted verbatim.
    - Missing or empty params: the placeholder token (plus any preceding
      whitespace) is removed so the command stays well-formed.
    """
    result = template
    for key, value in params.items():
        if value:
            result = result.replace(f"{{{key}}}", value)
    # Remove unfilled / empty placeholders
    result = re.sub(r"\s*\{[^}]+\}", "", result)
    return result.strip()


def extract_template_params(template: str) -> list[str]:
    """Return list of placeholder names found in *template*."""
    return re.findall(r"\{(\w+)\}", template)


# ── Executor ───────────────────────────────────────────────────────────────────

class CommandExecutor:
    def __init__(self, ssh_cfg: SSHConfig):
        self._ssh_cfg = ssh_cfg

    # ── Unified entry point (called by BOTH tools) ────────────────────────────

    async def run(
        self,
        command: str,
        *,
        work_dir: str = ".",
        use_ssh: Optional[bool] = None,
        timeout: int = 3600,
    ) -> ExecResult:
        """
        Execute *command* and return an :class:`ExecResult`.

        ``use_ssh`` three-way logic:
          None  → use ``ssh.enabled`` value from config (default)
          True  → always SSH regardless of config
          False → always local regardless of config
        """
        via_ssh = use_ssh if use_ssh is not None else self._ssh_cfg.enabled
        if via_ssh:
            return await self._run_ssh(command, work_dir, timeout)
        return await self._run_local(command, work_dir, timeout)

    # ── Local ─────────────────────────────────────────────────────────────────

    async def _run_local(self, command: str, work_dir: str, timeout: int) -> ExecResult:
        cwd = work_dir if work_dir not in (".", "") else None
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecResult(command=command, stdout="",
                              stderr=f"Timed out after {timeout}s", returncode=-1)
        return ExecResult(
            command=command,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            returncode=proc.returncode or 0,
        )

    # ── SSH ───────────────────────────────────────────────────────────────────

    async def _run_ssh(self, command: str, work_dir: str, timeout: int) -> ExecResult:
        cfg = self._ssh_cfg
        key_path = str(Path(cfg.key_file).expanduser()) if cfg.key_file else None

        connect_kwargs: dict = {
            "host": cfg.host,
            "port": cfg.port,
            "username": cfg.user,
            "known_hosts": None,  # loosen for dev; set to known_hosts file in prod
        }
        if cfg.password:
            connect_kwargs["password"] = cfg.password
        elif key_path and Path(key_path).exists():
            connect_kwargs["client_keys"] = [key_path]

        full_cmd = (
            f"cd {shlex.quote(work_dir)} && {command}"
            if work_dir and work_dir not in (".", "")
            else command
        )

        try:
            async with asyncssh.connect(**connect_kwargs) as ssh_device:
                result = await asyncio.wait_for(
                    ssh_device.run(full_cmd, check=False), timeout=timeout
                )
            return ExecResult(
                command=command,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                returncode=result.exit_status or 0,
            )
        except asyncio.TimeoutError:
            return ExecResult(command=command, stdout="",
                              stderr=f"SSH timed out after {timeout}s", returncode=-1)
        except Exception as exc:  # noqa: BLE001
            return ExecResult(command=command, stdout="",
                              stderr=f"SSH connection error: {exc}", returncode=-1)