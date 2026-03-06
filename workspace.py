"""
workspace.py – Session-scoped workspace management.

Responsibilities
────────────────
1. Lifecycle  – create / list / delete workspace directories.
2. Path jail  – four-layer path-traversal prevention.
3. Sync info  – build rsync/scp command strings so clients can push
                source files directly to the workspace via SSH, bypassing
                the MCP protocol entirely.

File-sync design
────────────────
File content is NEVER transmitted through the MCP tools.  Instead:

  a. init_workspace() creates the workspace directory and returns a
     ready-to-run rsync command for the user.
  b. The user (or a local script) runs that command; files arrive on
     disk via SSH without touching the agent.
  c. list_workspace_files() lets the agent verify what arrived.
  d. Execution tools (run_predefined_command, execute_command) operate
     on the files already present in the workspace.

Path-jail layers
────────────────
Layer 1 – Lexical: reject '..' path components before any filesystem I/O.
Layer 2 – Incremental realpath: walk each path prefix through
           os.path.realpath(); a symlink pointing outside is caught
           at the step it appears.
Layer 3 – Final prefix check on the fully resolved path (redundant safety net).
Layer 4 – Re-verification at exec time (TOCTOU guard, in executor.py).
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from config import SyncConfig


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class WorkspaceInfo:
    session_id:   str
    workspace_id: str
    topic:        str
    path:         Path
    created_at:   str

    def summary(self) -> str:
        label = self.topic or "(no topic)"
        return "\n".join([
            f"session    : {self.session_id}",
            f"workspace  : {self.workspace_id}",
            f"topic      : {label}",
            f"path       : {self.path}",
            f"created_at : {self.created_at}",
        ])


@dataclass
class SyncInfo:
    """Everything the user needs to push files into a workspace."""
    workspace_path: str          # absolute path on the server
    rsync_command:  str          # ready-to-run rsync command (local → server)
    scp_command:    str          # alternative scp command
    note:           str          # human-readable instructions

    def summary(self) -> str:
        return "\n".join([
            "## Workspace ready – sync your files",
            "",
            f"Server path : {self.workspace_path}",
            "",
            "Run ONE of the following on your local machine:",
            "",
            "### rsync (recommended – incremental, re-runnable)",
            f"```",
            self.rsync_command,
            f"```",
            "",
            "### scp (alternative)",
            f"```",
            self.scp_command,
            f"```",
            "",
            self.note,
        ])


# ── Manager ────────────────────────────────────────────────────────────────────

class WorkspaceManager:
    _META = ".workspace_meta"

    def __init__(self, workspaces_root: str | Path) -> None:
        self.root = Path(os.path.realpath(
            Path(workspaces_root).expanduser()
        ))
        self.root.mkdir(parents=True, exist_ok=True)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str, topic: str = "") -> WorkspaceInfo:
        """Return existing workspace for *session_id*, or create one."""
        existing = self._find_by_session(session_id)
        if existing:
            return existing
        suffix  = session_id
        slug    = _slugify(topic) if topic else ""
        ws_id   = f"{slug}-{suffix}" if slug else suffix
        ws_dir  = self.root / ws_id
        ws_dir.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc).isoformat()
        self._write_meta(ws_dir, session_id=session_id, ws_id=ws_id,
                         topic=topic, created_at=created)
        return WorkspaceInfo(session_id=session_id, workspace_id=ws_id,
                             topic=topic, path=ws_dir, created_at=created)

    def get_for_session(self, session_id: str) -> WorkspaceInfo:
        ws = self._find_by_session(session_id)
        if ws is None:
            raise ValueError(
                f"No workspace for session '{session_id}…'. "
                "Call init_workspace first."
            )
        return ws

    def set_topic(self, session_id: str, topic: str) -> WorkspaceInfo:
        ws = self.get_for_session(session_id)
        self._write_meta(ws.path, session_id=session_id, ws_id=ws.workspace_id,
                         topic=topic, created_at=ws.created_at)
        return WorkspaceInfo(session_id=session_id, workspace_id=ws.workspace_id,
                             topic=topic, path=ws.path, created_at=ws.created_at)

    def list_all(self) -> list[WorkspaceInfo]:
        results: list[WorkspaceInfo] = []
        for d in self.root.iterdir():
            if d.is_dir() and (d / self._META).exists():
                m = self._read_meta(d)
                results.append(WorkspaceInfo(
                    session_id   = m.get("session_id", ""),
                    workspace_id = d.name,
                    topic        = m.get("topic", ""),
                    path         = d,
                    created_at   = m.get("created_at", ""),
                ))
        return sorted(results, key=lambda w: w.created_at, reverse=True)

    def delete(self, workspace_id: str) -> None:
        import shutil
        ws_dir = (self.root / workspace_id).resolve()
        self._assert_under_root(ws_dir)
        if not ws_dir.is_dir():
            raise ValueError(f"Workspace '{workspace_id}' does not exist.")
        shutil.rmtree(ws_dir)

    # ── Sync command builder ───────────────────────────────────────────────────

    def build_sync_info(
        self,
        ws: WorkspaceInfo,
        sync_cfg: "SyncConfig",
        local_source: str = "./",
    ) -> SyncInfo:
        """
        Build rsync / scp command strings for pushing *local_source* into *ws*.

        *local_source* is a placeholder shown to the user; it defaults to
        the current directory ("./").  The user substitutes their actual path.

        The server workspace path is the authoritative destination; it is
        derived from the session ID and cannot be tampered with by the client.
        """
        host     = sync_cfg.ssh_host
        port     = sync_cfg.ssh_port
        user     = sync_cfg.ssh_user
        key      = sync_cfg.ssh_key
        extra    = sync_cfg.extra_rsync_opts.strip()
        dest_dir = str(ws.path) + "/"   # trailing slash = contents of ws dir

        # rsync -avz --delete -e "ssh -p PORT -i KEY" SRC USER@HOST:DEST
        ssh_opts = f"ssh -p {port}"
        if key:
            ssh_opts += f" -i {shlex.quote(str(Path(key).expanduser()))}"

        rsync_parts = [
            "rsync", "-avz", "--delete",
            "-e", shlex.quote(ssh_opts),
        ]
        if extra:
            rsync_parts.append(extra)
        rsync_parts += [
            shlex.quote(local_source),
            f"{user}@{host}:{dest_dir}",
        ]
        rsync_cmd = " ".join(rsync_parts)

        # scp -P PORT -i KEY -r SRC USER@HOST:DEST
        scp_parts = ["scp", f"-P {port}"]
        if key:
            scp_parts += ["-i", shlex.quote(str(Path(key).expanduser()))]
        scp_parts += ["-r", shlex.quote(local_source),
                      f"{user}@{host}:{dest_dir}"]
        scp_cmd = " ".join(scp_parts)

        note = (
            "Replace './' with your local project directory. "
            "--delete removes files in the workspace that no longer exist locally. "
            "Re-run the same command after making local edits to sync changes."
        )
        return SyncInfo(
            workspace_path = dest_dir,
            rsync_command  = rsync_cmd,
            scp_command    = scp_cmd,
            note           = note,
        )

    # ── Path jail ─────────────────────────────────────────────────────────────

    def resolve_path(self, session_id: str, relative: str) -> Path:
        ws = self.get_or_create(session_id)
        return _jail_resolve(ws.path, relative)

    def assert_work_dir(self, session_id: str, work_dir: Optional[str]) -> str:
        """Validate work_dir is inside the workspace; create and return abs path."""
        ws = self.get_or_create(session_id)
        if not work_dir:
            return str(ws.path)
        resolved = _jail_resolve(ws.path, work_dir)
        resolved.mkdir(parents=True, exist_ok=True)
        # Layer 4: re-verify after mkdir (symlink may have been planted)
        _assert_prefix(ws.path, resolved)
        return str(resolved)

    def list_files(self, session_id: str) -> str:
        """Return a tree-style listing of all files in the session workspace."""
        ws = self.get_or_create(session_id)
        lines: list[str] = [f"workspace: {ws.workspace_id}", f"path: {ws.path}", ""]
        for p in sorted(ws.path.rglob("*")):
            if p.name == self._META:
                continue
            rel = p.relative_to(ws.path)
            indent = "  " * (len(rel.parts) - 1)
            marker = "/" if p.is_dir() else ""
            lines.append(f"{indent}{p.name}{marker}")
        if len(lines) == 3:
            lines.append("(empty)")
        return "\n".join(lines)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _find_by_session(self, session_id: str) -> Optional[WorkspaceInfo]:
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            m = self._read_meta(d)
            if m.get("session_id") == session_id:
                return WorkspaceInfo(
                    session_id   = session_id,
                    workspace_id = d.name,
                    topic        = m.get("topic", ""),
                    path         = d,
                    created_at   = m.get("created_at", ""),
                )
        return None

    def _write_meta(self, ws_dir: Path, *, session_id: str,
                    ws_id: str, topic: str, created_at: str) -> None:
        (ws_dir / self._META).write_text(
            f"session_id={session_id}\n"
            f"workspace_id={ws_id}\n"
            f"topic={topic}\n"
            f"created_at={created_at}\n"
        )

    @staticmethod
    def _read_meta(ws_dir: Path) -> dict[str, str]:
        meta: dict[str, str] = {}
        try:
            for line in (ws_dir / WorkspaceManager._META).read_text().splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    meta[k.strip()] = v.strip()
        except OSError:
            pass
        return meta

    def _assert_under_root(self, path: Path) -> None:
        root_s = str(self.root).rstrip("/") + "/"
        path_s = str(path).rstrip("/") + "/"
        if path_s != root_s and not path_s.startswith(root_s):
            raise ValueError(f"Path '{path}' is outside workspaces root.")


# ── Path-jail implementation ───────────────────────────────────────────────────

class PathEscapeError(ValueError):
    """Raised when a path would escape its workspace jail."""


def _jail_resolve(ws_root: Path, relative: str) -> Path:
    """
    Three-layer path resolution with jail enforcement.

    Layer 1 – Lexical: reject any '..' component immediately.
    Layer 2 – Incremental symlink resolution: realpath at every path prefix.
    Layer 3 – Final canonical prefix check.
    """
    clean = relative.lstrip("/")

    # Layer 1: lexical
    for part in Path(clean).parts:
        if part == "..":
            raise PathEscapeError(
                f"Path '{relative}' contains '..' – upward traversal is not allowed."
            )

    # Layer 2: incremental realpath walk
    candidate = ws_root
    for part in Path(clean).parts:
        candidate = candidate / part
        _assert_prefix(ws_root, Path(os.path.realpath(candidate)),
                        original=relative)

    # Layer 3: final check
    final = Path(os.path.realpath(ws_root / clean))
    _assert_prefix(ws_root, final, original=relative)
    return final


def _assert_prefix(ws_root: Path, resolved: Path, original: str = "") -> None:
    ws_s  = str(ws_root).rstrip("/") + "/"
    res_s = str(resolved).rstrip("/") + "/"
    if res_s != ws_s and not res_s.startswith(ws_s):
        hint = f" (from '{original}')" if original else ""
        raise PathEscapeError(
            f"Path{hint} resolves to '{resolved}', "
            f"which is outside workspace '{ws_root}'."
        )


# ── Utilities ──────────────────────────────────────────────────────────────────
# 合法文件名
def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")[:32] or "ws"