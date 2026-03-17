from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from http_client import build_async_client


class GiteaApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class GiteaRepo:
    owner: str
    name: str
    clone_url: str
    ssh_url: str
    html_url: str
    default_branch: str
    private: bool
    empty: bool

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "GiteaRepo":
        owner = payload.get("owner") or {}
        return cls(
            owner=str(owner.get("login") or owner.get("username") or ""),
            name=str(payload["name"]),
            clone_url=str(payload.get("clone_url") or ""),
            ssh_url=str(payload.get("ssh_url") or ""),
            html_url=str(payload.get("html_url") or ""),
            default_branch=str(payload.get("default_branch") or "main"),
            private=bool(payload.get("private", True)),
            empty=bool(payload.get("empty", False)),
        )


class GiteaClient:
    """
    Minimal async Gitea API client for workspace repository lifecycle.

    API references:
    - GET /api/v1/version
    - GET /api/v1/user
    - GET /api/v1/repos/{owner}/{repo}
    - POST /api/v1/user/repos
    - POST /api/v1/orgs/{org}/repos
    - DELETE /api/v1/repos/{owner}/{repo}
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = build_async_client(
            base_url=self._base_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self._cached_user: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GiteaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_status: tuple[int, ...] = (200,),
        json_body: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        response = await self._client.request(method, path, json=json_body)
        if response.status_code not in expected_status:
            detail = response.text.strip()
            raise GiteaApiError(
                f"Gitea API {method} {path} failed with {response.status_code}: {detail}"
            )
        return response

    async def probe(self) -> dict[str, Any]:
        response = await self._request("GET", "/api/v1/version")
        return response.json()

    async def get_authenticated_user(self) -> str:
        if self._cached_user is not None:
            return self._cached_user

        response = await self._request("GET", "/api/v1/user")
        payload = response.json()
        user = str(payload.get("login") or payload.get("username") or "").strip()
        if not user:
            raise GiteaApiError(f"Unexpected Gitea user payload: {payload}")
        self._cached_user = user
        return user

    async def get_repo(self, owner: str, repo: str) -> Optional[GiteaRepo]:
        response = await self._client.get(f"/api/v1/repos/{owner}/{repo}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            detail = response.text.strip()
            raise GiteaApiError(
                f"Gitea API GET /api/v1/repos/{owner}/{repo} failed with "
                f"{response.status_code}: {detail}"
            )
        payload = response.json()
        return GiteaRepo.from_api(payload)

    async def create_user_repo(
        self,
        name: str,
        *,
        description: str = "",
        private: bool = True,
        auto_init: bool = False,
        default_branch: str = "main",
    ) -> GiteaRepo:
        response = await self._request(
            "POST",
            "/api/v1/user/repos",
            expected_status=(201,),
            json_body={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
                "default_branch": default_branch,
            },
        )
        return GiteaRepo.from_api(response.json())

    async def create_org_repo(
        self,
        org: str,
        name: str,
        *,
        description: str = "",
        private: bool = True,
        auto_init: bool = False,
        default_branch: str = "main",
    ) -> GiteaRepo:
        response = await self._request(
            "POST",
            f"/api/v1/orgs/{org}/repos",
            expected_status=(201,),
            json_body={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
                "default_branch": default_branch,
            },
        )
        return GiteaRepo.from_api(response.json())

    async def ensure_repo(
        self,
        *,
        owner: Optional[str],
        repo_name: str,
        description: str = "",
        private: bool = True,
        auto_init: bool = False,
        default_branch: str = "main",
    ) -> dict[str, Any]:
        resolved_owner = owner or await self.get_authenticated_user()
        existing = await self.get_repo(resolved_owner, repo_name)
        if existing is not None:
            return {
                "status": "exists",
                "repo": existing,
            }

        current_user = await self.get_authenticated_user()
        if resolved_owner == current_user:
            repo = await self.create_user_repo(
                repo_name,
                description=description,
                private=private,
                auto_init=auto_init,
                default_branch=default_branch,
            )
        else:
            repo = await self.create_org_repo(
                resolved_owner,
                repo_name,
                description=description,
                private=private,
                auto_init=auto_init,
                default_branch=default_branch,
            )

        return {
            "status": "created",
            "repo": repo,
        }

    async def delete_repo(self, owner: str, repo: str) -> None:
        await self._request(
            "DELETE",
            f"/api/v1/repos/{owner}/{repo}",
            expected_status=(204,),
        )
