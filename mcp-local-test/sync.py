from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from metadata import load_metadata


DEFAULT_EXCLUDES = [
    ".mcp/",
    ".git/",
    "__pycache__/",
    "node_modules/",
    ".DS_Store",
]


class RsyncError(RuntimeError):
    pass


def build_remote_workspace_path(remote_base_dir: str, topic_id: str) -> str:
    remote_base_dir = remote_base_dir.rstrip("/")
    return f"{remote_base_dir}/{topic_id}"


def sync_directory_with_rsync(
    *,
    root_path: str,
    ssh_user: str,
    ssh_host: str,
    remote_base_dir: str,
    extra_excludes: Optional[Iterable[str]] = None,
    ssh_port: int = 22,
    delete: bool = False,
    dry_run: bool = False,
) -> dict:
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError(f"不是目录：{root}")

    meta = load_metadata(root)
    if meta is None:
        raise RuntimeError("目录尚未绑定工作区，找不到 .mcp/workspace.json。")

    remote_workspace = build_remote_workspace_path(remote_base_dir, meta.topic_id)

    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    # 注意：源目录末尾加 / 表示同步目录内容，不是目录本身
    src = str(root) + "/"
    dst = f"{ssh_user}@{ssh_host}:{remote_workspace}/src/"

    cmd = [
        "rsync",
        "-az",
        "--stats",
        "--human-readable",
        "--itemize-changes",
        "--partial",
    ]

    if delete:
        cmd.append("--delete")
    if dry_run:
        cmd.append("--dry-run")

    for pattern in excludes:
        cmd.extend(["--exclude", pattern])

    cmd.extend([
        "-e",
        f"ssh -p {ssh_port}",
        src,
        dst,
    ])

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RsyncError(
            f"rsync 同步失败，exit_code={proc.returncode}\n"
            f"CMD: {' '.join(shlex.quote(x) for x in cmd)}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    return {
        "status": "ok",
        "topic_id": meta.topic_id,
        "remote_workspace": remote_workspace,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "dry_run": dry_run,
    }