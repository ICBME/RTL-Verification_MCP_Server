from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


MCP_DIRNAME = ".mcp"
META_FILENAME = "workspace.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_topic_id() -> str:
    return f"tp_{uuid.uuid4().hex}"


@dataclass
class WorkspaceMetadata:
    version: int
    topic_id: str
    remote_server: str
    remote_host: Optional[str]
    remote_base_dir: Optional[str]
    workspace_name: str
    created_at: str
    updated_at: str
    root_fingerprint: Optional[str]
    last_sync_at: Optional[str]
    auth_token: Optional[str] = None
    gitea_base_url: Optional[str] = None
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    repo_clone_url: Optional[str] = None
    repo_default_branch: Optional[str] = None
    last_source_revision: Optional[str] = None

    @classmethod
    def new(
        cls,
        *,
        topic_id: str,
        remote_server: str,
        remote_host: Optional[str],
        remote_base_dir: Optional[str],
        workspace_name: str,
        root_fingerprint: Optional[str] = None,
        auth_token: Optional[str] = None,
        gitea_base_url: Optional[str] = None,
        repo_owner: Optional[str] = None,
        repo_name: Optional[str] = None,
        repo_clone_url: Optional[str] = None,
        repo_default_branch: Optional[str] = None,
        last_source_revision: Optional[str] = None,
    ) -> "WorkspaceMetadata":
        now = utc_now_iso()
        return cls(
            version=2,
            topic_id=topic_id,
            remote_server=remote_server,
            remote_host=remote_host,
            remote_base_dir=remote_base_dir,
            workspace_name=workspace_name,
            created_at=now,
            updated_at=now,
            root_fingerprint=root_fingerprint,
            last_sync_at=None,
            auth_token=auth_token,
            gitea_base_url=gitea_base_url,
            repo_owner=repo_owner,
            repo_name=repo_name,
            repo_clone_url=repo_clone_url,
            repo_default_branch=repo_default_branch,
            last_source_revision=last_source_revision,
        )


def mcp_dir(root: Path) -> Path:
    return root / MCP_DIRNAME


def meta_path(root: Path) -> Path:
    return mcp_dir(root) / META_FILENAME


def ensure_meta_container(root: Path) -> None:
    p = mcp_dir(root)
    if p.exists() and not p.is_dir():
        raise RuntimeError(
            f"路径冲突：{p} 已存在但不是目录，无法创建 metadata 目录。"
        )
    p.mkdir(parents=True, exist_ok=True)


def load_metadata(root: Path) -> Optional[WorkspaceMetadata]:
    p = meta_path(root)
    if not p.exists():
        return None
    if p.is_dir():
        raise RuntimeError(f"metadata 路径异常：{p} 是目录，不是文件。")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return WorkspaceMetadata(**data)


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def save_metadata(root: Path, meta: WorkspaceMetadata) -> None:
    meta.updated_at = utc_now_iso()
    atomic_write_json(meta_path(root), asdict(meta))


def backup_invalid_metadata(root: Path) -> Optional[Path]:
    p = meta_path(root)
    if not p.exists() or p.is_dir():
        return None
    backup = p.with_name(f"{p.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
    p.rename(backup)
    return backup


def compute_root_fingerprint(root: Path) -> str:
    """
    简化版目录指纹：
    只基于根目录第一层文件/目录名，避免大目录扫描过慢。
    """
    entries = []
    for child in sorted(root.iterdir(), key=lambda x: x.name):
        if child.name == MCP_DIRNAME:
            continue
        entries.append(f"{child.name}:{'d' if child.is_dir() else 'f'}")
    raw = "|".join(entries)
    import hashlib
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


OnExisting = Literal["ask", "reuse", "overwrite", "fail"]


def init_or_bind_workspace(
    *,
    root_path: str,
    remote_server: str,
    remote_host: Optional[str],
    remote_base_dir: Optional[str],
    auth_token: Optional[str] = None,
    gitea_base_url: Optional[str] = None,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    repo_clone_url: Optional[str] = None,
    repo_default_branch: Optional[str] = None,
    on_existing: OnExisting = "ask",
) -> dict:
    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"目录不存在或不是目录：{root}")

    ensure_meta_container(root)

    try:
        existing = load_metadata(root)
    except Exception:
        backup = backup_invalid_metadata(root)
        return {
            "status": "invalid_metadata",
            "message": "检测到损坏或非法 metadata，已建议重建。",
            "backup_path": str(backup) if backup else None,
        }

    if existing is not None:
        if on_existing == "reuse":
            return {
                "status": "reused",
                "message": "继续使用已有工作区绑定。",
                "topic_id": existing.topic_id,
                "metadata": asdict(existing),
            }
        if on_existing == "overwrite":
            pass
        elif on_existing == "fail":
            raise RuntimeError(
                f"目录已绑定工作区，topic_id={existing.topic_id}。"
            )
        else:
            return {
                "status": "needs_confirmation",
                "message": "检测到目录已有 workspace metadata，请选择 reuse 或 overwrite。",
                "topic_id": existing.topic_id,
                "metadata": asdict(existing),
            }

    topic_id = gen_topic_id()
    meta = WorkspaceMetadata.new(
        topic_id=topic_id,
        remote_server=remote_server,
        remote_host=remote_host,
        remote_base_dir=remote_base_dir,
        workspace_name=root.name,
        root_fingerprint=compute_root_fingerprint(root),
        auth_token=auth_token,
        gitea_base_url=gitea_base_url,
        repo_owner=repo_owner,
        repo_name=repo_name,
        repo_clone_url=repo_clone_url,
        repo_default_branch=repo_default_branch,
    )
    save_metadata(root, meta)

    return {
        "status": "created",
        "message": "已创建新的工作区 metadata。",
        "topic_id": topic_id,
        "metadata": asdict(meta),
    }
