from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Literal, Optional

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


class ScpError(RuntimeError):
    pass


TransferMethod = Literal["rsync", "scp", "auto"]


def build_remote_workspace_path(remote_base_dir: str, topic_id: str) -> str:
    remote_base_dir = remote_base_dir.rstrip("/")
    return f"{remote_base_dir}/{topic_id}"


def _prepare_auth(
    ssh_port: int,
    ssh_key_path: Optional[str],
) -> tuple[list[str], list[str], Optional[str]]:
    ssh_parts = ["ssh", "-p", str(ssh_port)]
    scp_parts = ["scp", "-P", str(ssh_port)]
    key_path: Optional[str] = None

    if ssh_key_path:
        key_path = str(Path(ssh_key_path).expanduser().resolve())
        if not Path(key_path).is_file():
            raise ValueError(f"SSH 私钥文件不存在：{key_path}")

        common = [
            "-i",
            key_path,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PubkeyAuthentication=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
        ]
        ssh_parts.extend(common)
        scp_parts.extend(common)

    return ssh_parts, scp_parts, key_path


def _build_run_env(ssh_key_passphrase: Optional[str]) -> tuple[Optional[dict[str, str]], Optional[str]]:
    if not ssh_key_passphrase:
        return None, None

    with tempfile.NamedTemporaryFile("w", delete=False, prefix="mcp-askpass-", suffix=".sh") as f:
        f.write("#!/bin/sh\n")
        f.write('printf "%s\\n" "$SSH_KEY_PASSPHRASE"\n')
        askpass_script_path = f.name
    os.chmod(askpass_script_path, 0o700)

    proc_env = os.environ.copy()
    proc_env["SSH_ASKPASS"] = askpass_script_path
    proc_env["SSH_ASKPASS_REQUIRE"] = "force"
    proc_env["SSH_KEY_PASSPHRASE"] = ssh_key_passphrase
    proc_env["DISPLAY"] = proc_env.get("DISPLAY", ":0")
    return proc_env, askpass_script_path


def _run_command(cmd: list[str], env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )


def _is_excluded(rel_posix_path: str, is_dir: bool, patterns: list[str]) -> bool:
    rel = rel_posix_path.strip("/")
    if not rel:
        return False

    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        p = pattern.strip()
        if not p:
            continue

        if p.endswith("/"):
            p = p.rstrip("/")
            if rel == p or rel.startswith(p + "/") or name == p:
                return True
            continue

        if name == p or rel == p:
            return True

        from fnmatch import fnmatch

        if fnmatch(name, p) or fnmatch(rel, p):
            return True

    return False


def _copy_tree_with_excludes(src_root: Path, dst_root: Path, patterns: list[str]) -> None:
    for current, dirs, files in os.walk(src_root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(src_root)
        rel_dir_posix = rel_dir.as_posix()

        dirs[:] = [
            d for d in dirs
            if not _is_excluded(
                (f"{rel_dir_posix}/{d}" if rel_dir_posix != "." else d),
                True,
                patterns,
            )
        ]

        target_dir = dst_root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for filename in files:
            rel_file = f"{rel_dir_posix}/{filename}" if rel_dir_posix != "." else filename
            if _is_excluded(rel_file, False, patterns):
                continue
            src_file = current_path / filename
            dst_file = target_dir / filename
            shutil.copy2(src_file, dst_file)


def _rsync_upload(
    *,
    root: Path,
    ssh_user: str,
    ssh_host: str,
    remote_workspace: str,
    excludes: list[str],
    ssh_parts: list[str],
    env: Optional[dict[str, str]],
    delete: bool,
    dry_run: bool,
) -> dict:
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

    cmd.extend(["-e", " ".join(shlex.quote(p) for p in ssh_parts), src, dst])
    proc = _run_command(cmd, env=env)
    if proc.returncode != 0:
        raise RsyncError(
            f"rsync 同步失败，exit_code={proc.returncode}\n"
            f"CMD: {' '.join(shlex.quote(x) for x in cmd)}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    return {
        "method": "rsync",
        "command": " ".join(shlex.quote(x) for x in cmd),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "dry_run": dry_run,
    }


def _scp_upload(
    *,
    root: Path,
    ssh_user: str,
    ssh_host: str,
    remote_workspace: str,
    excludes: list[str],
    ssh_parts: list[str],
    scp_parts: list[str],
    env: Optional[dict[str, str]],
    dry_run: bool,
) -> dict:
    remote_src_dir = f"{remote_workspace}/src"
    if dry_run:
        check_cmd = ssh_parts + [f"{ssh_user}@{ssh_host}", "test", "-d", remote_workspace]
        check_proc = _run_command(check_cmd, env=env)
        if check_proc.returncode != 0:
            raise ScpError(
                f"scp dry-run 远程路径检查失败，exit_code={check_proc.returncode}\n"
                f"CMD: {' '.join(shlex.quote(x) for x in check_cmd)}\n"
                f"STDOUT:\n{check_proc.stdout}\n"
                f"STDERR:\n{check_proc.stderr}"
            )
        preview_cmd = list(scp_parts) + ["-r", "<LOCAL_STAGE>/.", f"{ssh_user}@{ssh_host}:{remote_src_dir}/"]
        return {
            "method": "scp",
            "command": " ".join(shlex.quote(x) for x in preview_cmd),
            "stdout": check_proc.stdout,
            "stderr": check_proc.stderr,
            "dry_run": True,
            "note": "scp dry-run: only validated auth/connectivity and remote workspace path; no files uploaded",
        }

    mkdir_cmd = ssh_parts + [f"{ssh_user}@{ssh_host}", "mkdir", "-p", remote_src_dir]
    mkdir_proc = _run_command(mkdir_cmd, env=env)
    if mkdir_proc.returncode != 0:
        raise ScpError(
            f"scp 预创建远程目录失败，exit_code={mkdir_proc.returncode}\n"
            f"CMD: {' '.join(shlex.quote(x) for x in mkdir_cmd)}\n"
            f"STDOUT:\n{mkdir_proc.stdout}\n"
            f"STDERR:\n{mkdir_proc.stderr}"
        )

    # scp 不支持 --exclude；先做本地过滤再上传。
    with tempfile.TemporaryDirectory(prefix="mcp-scp-stage-") as td:
        stage_root = Path(td) / "src"
        _copy_tree_with_excludes(root, stage_root, excludes)

        dst = f"{ssh_user}@{ssh_host}:{remote_src_dir}/"
        entries = sorted(stage_root.iterdir(), key=lambda p: p.name)
        if not entries:
            return {
                "method": "scp",
                "command": " ".join(shlex.quote(x) for x in (list(scp_parts) + ["-r", "<EMPTY>", dst])),
                "stdout": "",
                "stderr": "",
                "dry_run": dry_run,
                "note": "scp fallback used; nothing to upload after exclude filtering",
            }

        cmd = list(scp_parts)
        cmd.append("-r")
        cmd.extend(str(p) for p in entries)
        cmd.append(dst)

        proc = _run_command(cmd, env=env)
        if proc.returncode != 0:
            raise ScpError(
                f"scp 同步失败，exit_code={proc.returncode}\n"
                f"CMD: {' '.join(shlex.quote(x) for x in cmd)}\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )

        return {
            "method": "scp",
            "command": " ".join(shlex.quote(x) for x in cmd),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "dry_run": dry_run,
            "note": "scp fallback used",
        }


def sync_directory_with_rsync(
    *,
    root_path: str,
    ssh_user: str,
    ssh_host: str,
    remote_base_dir: str,
    extra_excludes: Optional[Iterable[str]] = None,
    ssh_port: int = 22,
    ssh_key_path: Optional[str] = None,
    ssh_key_passphrase: Optional[str] = None,
    transfer_method: TransferMethod = "auto",
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

    ssh_parts, scp_parts, _ = _prepare_auth(ssh_port=ssh_port, ssh_key_path=ssh_key_path)
    proc_env, askpass_script_path = _build_run_env(ssh_key_passphrase)

    result: dict
    warnings: list[str] = []
    try:
        if transfer_method == "rsync":
            result = _rsync_upload(
                root=root,
                ssh_user=ssh_user,
                ssh_host=ssh_host,
                remote_workspace=remote_workspace,
                excludes=excludes,
                ssh_parts=ssh_parts,
                env=proc_env,
                delete=delete,
                dry_run=dry_run,
            )
        elif transfer_method == "scp":
            if delete:
                warnings.append("transfer_method=scp 时不支持 delete，已忽略 delete 参数。")
            result = _scp_upload(
                root=root,
                ssh_user=ssh_user,
                ssh_host=ssh_host,
                remote_workspace=remote_workspace,
                excludes=excludes,
                ssh_parts=ssh_parts,
                scp_parts=scp_parts,
                env=proc_env,
                dry_run=dry_run,
            )
        else:
            try:
                result = _rsync_upload(
                    root=root,
                    ssh_user=ssh_user,
                    ssh_host=ssh_host,
                    remote_workspace=remote_workspace,
                    excludes=excludes,
                    ssh_parts=ssh_parts,
                    env=proc_env,
                    delete=delete,
                    dry_run=dry_run,
                )
            except (RsyncError, FileNotFoundError) as exc:
                warnings.append(
                    f"rsync 不可用或失败，已回退到 scp。detail={exc}"
                )
                if delete:
                    warnings.append("fallback 到 scp 时不支持 delete，已忽略 delete 参数。")
                result = _scp_upload(
                    root=root,
                    ssh_user=ssh_user,
                    ssh_host=ssh_host,
                    remote_workspace=remote_workspace,
                    excludes=excludes,
                    ssh_parts=ssh_parts,
                    scp_parts=scp_parts,
                    env=proc_env,
                    dry_run=dry_run,
                )
    finally:
        if askpass_script_path:
            try:
                os.remove(askpass_script_path)
            except OSError:
                pass

    return {
        "status": "ok",
        "topic_id": meta.topic_id,
        "remote_workspace": remote_workspace,
        "transfer_method": result["method"],
        "command": result["command"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "dry_run": result["dry_run"],
        "warnings": warnings,
        "detail": result,
    }
