from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class GiteaWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class GiteaWorkspaceConfig:
    base_url: str
    username: str
    password: str
    owner: str
    default_branch: str
    token_scopes: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "GiteaWorkspaceConfig":
        base_url = os.getenv("GITEA_BASE_URL", "").strip().rstrip("/")
        username = os.getenv("GITEA_USERNAME", "").strip()
        password = os.getenv("GITEA_PASSWORD", "").strip()
        owner = os.getenv("GITEA_OWNER", "").strip() or username
        default_branch = os.getenv("GITEA_DEFAULT_BRANCH", "").strip() or "main"
        scopes_raw = os.getenv("GITEA_TOKEN_SCOPES", "").strip()
        token_scopes = tuple(
            item.strip() for item in scopes_raw.split(",") if item.strip()
        ) or ("read:repository", "write:repository")

        if not base_url or not username or not password:
            raise GiteaWorkspaceError(
                "Missing Gitea provisioning config. "
                "Set GITEA_BASE_URL, GITEA_USERNAME, and GITEA_PASSWORD."
            )

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            owner=owner,
            default_branch=default_branch,
            token_scopes=token_scopes,
        )


def slugify(value: str) -> str:
    cleaned = []
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {"-", "_", " "}:
            cleaned.append("-")
    result = "".join(cleaned).strip("-")
    while "--" in result:
        result = result.replace("--", "-")
    return result or "workspace"


class GiteaWorkspaceAdmin:
    def __init__(self, cfg: GiteaWorkspaceConfig, *, timeout: float = 20.0) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            auth=(cfg.username, cfg.password),
            headers={"Accept": "application/json"},
            timeout=timeout,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GiteaWorkspaceAdmin":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: tuple[int, ...],
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        response = await self._client.request(method, path, json=json_body)
        if response.status_code not in expected:
            raise GiteaWorkspaceError(
                f"Gitea API {method} {path} failed with {response.status_code}: {response.text.strip()}"
            )
        return response

    async def get_repo(self, owner: str, repo_name: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/api/v1/repos/{owner}/{repo_name}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GiteaWorkspaceError(
                f"Gitea API GET /api/v1/repos/{owner}/{repo_name} failed with "
                f"{response.status_code}: {response.text.strip()}"
            )
        return response.json()

    async def ensure_repo(self, *, topic: str, topic_id: str) -> dict[str, Any]:
        repo_name = f"{slugify(topic)}-{topic_id}"
        existing = await self.get_repo(self._cfg.owner, repo_name)
        if existing is not None:
            return {"status": "exists", "repo": existing}

        path = (
            "/api/v1/user/repos"
            if self._cfg.owner == self._cfg.username
            else f"/api/v1/orgs/{self._cfg.owner}/repos"
        )
        response = await self._request(
            "POST",
            path,
            expected=(201,),
            json_body={
                "name": repo_name,
                "description": f"Workspace repo for {topic} ({topic_id})",
                "private": True,
                "auto_init": False,
                "default_branch": self._cfg.default_branch,
            },
        )
        return {"status": "created", "repo": response.json()}

    async def create_access_token(self, *, token_name: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/api/v1/users/{self._cfg.username}/tokens",
            expected=(201,),
            json_body={
                "name": token_name,
                "scopes": list(self._cfg.token_scopes),
            },
        )
        return response.json()
