from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from metadata import atomic_write_json


COMMON_CONFIG_FILENAME = "common_config.json"


def server_root_dir() -> Path:
    return Path(__file__).resolve().parent


def common_config_path() -> Path:
    return server_root_dir() / ".mcp" / COMMON_CONFIG_FILENAME


@dataclass
class CommonConfig:
    root_path: Optional[str] = None
    remote_server: Optional[str] = None
    remote_host: Optional[str] = None
    remote_base_dir: Optional[str] = None
    gitea_base_url: Optional[str] = None
    repo_owner: Optional[str] = None
    repo_default_branch: Optional[str] = None
    gitea_token: Optional[str] = None
    ssh_user: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_key_path: Optional[str] = None
    auth_token: Optional[str] = None

    @classmethod
    def load(cls) -> "CommonConfig":
        path = common_config_path()
        if not path.exists():
            return cls()

        import json

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise RuntimeError(f"常用配置文件格式错误：{path}")
        return cls(**data)

    def save(self) -> None:
        atomic_write_json(common_config_path(), asdict(self))

    def merge_updates(
        self,
        *,
        root_path: Optional[str] = None,
        remote_server: Optional[str] = None,
        remote_host: Optional[str] = None,
        remote_base_dir: Optional[str] = None,
        gitea_base_url: Optional[str] = None,
        repo_owner: Optional[str] = None,
        repo_default_branch: Optional[str] = None,
        gitea_token: Optional[str] = None,
        ssh_user: Optional[str] = None,
        ssh_port: Optional[int] = None,
        ssh_key_path: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> "CommonConfig":
        if root_path is not None:
            self.root_path = root_path
        if remote_server is not None:
            self.remote_server = remote_server
        if remote_host is not None:
            self.remote_host = remote_host
        if remote_base_dir is not None:
            self.remote_base_dir = remote_base_dir
        if gitea_base_url is not None:
            self.gitea_base_url = gitea_base_url
        if repo_owner is not None:
            self.repo_owner = repo_owner
        if repo_default_branch is not None:
            self.repo_default_branch = repo_default_branch
        if gitea_token is not None:
            self.gitea_token = gitea_token
        if ssh_user is not None:
            self.ssh_user = ssh_user
        if ssh_port is not None:
            self.ssh_port = ssh_port
        if ssh_key_path is not None:
            self.ssh_key_path = ssh_key_path
        if auth_token is not None:
            self.auth_token = auth_token
        return self
