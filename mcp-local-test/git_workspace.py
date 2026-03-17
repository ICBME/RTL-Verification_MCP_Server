from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_EXCLUDES = [
    ".git/",
    ".mcp/",
    "__pycache__/",
    "node_modules/",
    ".DS_Store",
]


class GitSyncError(RuntimeError):
    pass


def _run_command(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )


def _git_base_cmd(auth_token: Optional[str]) -> list[str]:
    cmd = ["git"]
    if auth_token:
        cmd.extend(["-c", f"http.extraHeader=Authorization: token {auth_token}"])
    return cmd


def _is_excluded(rel_posix_path: str, patterns: list[str]) -> bool:
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


def _clear_worktree(root: Path) -> None:
    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_tree_with_excludes(src_root: Path, dst_root: Path, patterns: list[str]) -> None:
    for current, dirs, files in os.walk(src_root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(src_root)
        rel_dir_posix = rel_dir.as_posix()

        dirs[:] = [
            d for d in dirs
            if not _is_excluded(
                f"{rel_dir_posix}/{d}" if rel_dir_posix != "." else d,
                patterns,
            )
        ]

        target_dir = dst_root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for filename in files:
            rel_file = f"{rel_dir_posix}/{filename}" if rel_dir_posix != "." else filename
            if _is_excluded(rel_file, patterns):
                continue
            shutil.copy2(current_path / filename, target_dir / filename)


def _git_has_changes(repo_dir: Path, auth_token: Optional[str]) -> bool:
    proc = _run_command(_git_base_cmd(auth_token) + ["status", "--porcelain"], cwd=repo_dir)
    if proc.returncode != 0:
        raise GitSyncError(f"git status failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return bool(proc.stdout.strip())


def _compute_directory_fingerprint(root: Path, patterns: list[str]) -> str:
    hasher = hashlib.sha256()
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        rel_dir_posix = rel_dir.as_posix()

        dirs[:] = [
            d for d in dirs
            if not _is_excluded(
                f"{rel_dir_posix}/{d}" if rel_dir_posix != "." else d,
                patterns,
            )
        ]

        for filename in sorted(files):
            rel_file = f"{rel_dir_posix}/{filename}" if rel_dir_posix != "." else filename
            if _is_excluded(rel_file, patterns):
                continue
            file_path = current_path / filename
            stat = file_path.stat()
            hasher.update(rel_file.encode("utf-8"))
            hasher.update(str(stat.st_size).encode("ascii"))
            hasher.update(str(stat.st_mtime_ns).encode("ascii"))
    return f"tree:{hasher.hexdigest()}"


def get_local_source_revision(
    root_path: str,
    *,
    extra_excludes: Optional[Iterable[str]] = None,
) -> str:
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    rev_proc = _run_command(["git", "rev-parse", "HEAD"], cwd=root)
    if rev_proc.returncode == 0:
        head = rev_proc.stdout.strip()
        status_proc = _run_command(["git", "status", "--porcelain"], cwd=root)
        if status_proc.returncode == 0 and status_proc.stdout.strip():
            return f"git:{head}+dirty"
        return f"git:{head}"

    return _compute_directory_fingerprint(root, excludes)


def sync_directory_to_repo(
    *,
    root_path: str,
    remote_url: str,
    branch: str = "main",
    auth_token: Optional[str] = None,
    author_name: str = "MCP Workspace Bot",
    author_email: str = "mcp-workspace@example.com",
    commit_message: str = "Sync workspace",
    extra_excludes: Optional[Iterable[str]] = None,
    dry_run: bool = False,
) -> dict:
    """
    Sync a local directory into a remote Git repository without touching the source tree.

    Flow:
    1. Clone the remote repository into a temporary directory.
    2. Replace the temporary worktree content with filtered local files.
    3. Commit changes when needed.
    4. Push the target branch back to the remote repository.
    """
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    excludes = list(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    commands: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mcp-git-sync-") as td:
        repo_dir = Path(td) / "repo"
        clone_cmd = _git_base_cmd(auth_token) + ["clone", remote_url, str(repo_dir)]
        commands.append(" ".join(shlex.quote(x) for x in clone_cmd))
        clone_proc = _run_command(clone_cmd)
        if clone_proc.returncode != 0:
            raise GitSyncError(
                "git clone failed\n"
                f"STDOUT:\n{clone_proc.stdout}\nSTDERR:\n{clone_proc.stderr}"
            )

        checkout_cmd = _git_base_cmd(auth_token) + ["checkout", "-B", branch]
        commands.append(" ".join(shlex.quote(x) for x in checkout_cmd))
        checkout_proc = _run_command(checkout_cmd, cwd=repo_dir)
        if checkout_proc.returncode != 0:
            raise GitSyncError(
                "git checkout failed\n"
                f"STDOUT:\n{checkout_proc.stdout}\nSTDERR:\n{checkout_proc.stderr}"
            )

        _clear_worktree(repo_dir)
        _copy_tree_with_excludes(root, repo_dir, excludes)

        add_cmd = _git_base_cmd(auth_token) + ["add", "-A"]
        commands.append(" ".join(shlex.quote(x) for x in add_cmd))
        add_proc = _run_command(add_cmd, cwd=repo_dir)
        if add_proc.returncode != 0:
            raise GitSyncError(
                "git add failed\n"
                f"STDOUT:\n{add_proc.stdout}\nSTDERR:\n{add_proc.stderr}"
            )

        has_changes = _git_has_changes(repo_dir, auth_token)
        if not has_changes:
            return {
                "status": "ok",
                "changed": False,
                "branch": branch,
                "remote_url": remote_url,
                "commands": commands,
                "dry_run": dry_run,
                "message": "No changes to commit.",
            }

        commit_cmd = _git_base_cmd(auth_token) + [
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            commit_message,
        ]
        commands.append(" ".join(shlex.quote(x) for x in commit_cmd))
        if dry_run:
            return {
                "status": "ok",
                "changed": True,
                "branch": branch,
                "remote_url": remote_url,
                "commands": commands,
                "dry_run": True,
                "message": "Dry run: commit and push skipped.",
            }

        commit_proc = _run_command(commit_cmd, cwd=repo_dir)
        if commit_proc.returncode != 0:
            raise GitSyncError(
                "git commit failed\n"
                f"STDOUT:\n{commit_proc.stdout}\nSTDERR:\n{commit_proc.stderr}"
            )

        push_cmd = _git_base_cmd(auth_token) + ["push", "origin", f"HEAD:refs/heads/{branch}"]
        commands.append(" ".join(shlex.quote(x) for x in push_cmd))
        push_proc = _run_command(push_cmd, cwd=repo_dir)
        if push_proc.returncode != 0:
            raise GitSyncError(
                "git push failed\n"
                f"STDOUT:\n{push_proc.stdout}\nSTDERR:\n{push_proc.stderr}"
            )

        return {
            "status": "ok",
            "changed": True,
            "branch": branch,
            "remote_url": remote_url,
            "commands": commands,
            "dry_run": False,
            "commit_stdout": commit_proc.stdout,
            "push_stdout": push_proc.stdout,
            "push_stderr": push_proc.stderr,
        }
