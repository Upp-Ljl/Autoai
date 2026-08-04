from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


@dataclass
class GitHubError(RuntimeError):
    method: str
    path: str
    status_code: int
    detail: str
    params: dict[str, Any] | None = None
    request_id: str | None = None

    def __str__(self) -> str:
        query = f" params={json.dumps(self.params, sort_keys=True)}" if self.params else ""
        request = f" request_id={self.request_id}" if self.request_id else ""
        return f"GitHub {self.method} {self.path}{query}: {self.status_code} {self.detail[:500]}{request}"

    @property
    def retryable(self) -> bool:
        return self.status_code in {408, 409, 425, 429, 500, 502, 503, 504}


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        *,
        token_source: str = "unknown",
        token_fingerprint: str | None = None,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ):
        self.token = token
        self.token_source = token_source
        self.token_fingerprint = token_fingerprint
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sat2-relay/2.2.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=True,
            transport=transport,
        )
        self._etag_cache: dict[str, tuple[str, Any]] = {}
        self.rate_limit: dict[str, str | None] = {}

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _cache_key(method: str, path: str, params: dict[str, Any] | None) -> str:
        return f"{method}:{path}:{json.dumps(params or {}, sort_keys=True, separators=(',', ':'))}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        params = kwargs.get("params")
        key = self._cache_key(method, path, params)
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() == "GET" and key in self._etag_cache:
            headers["If-None-Match"] = self._etag_cache[key][0]
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.rate_limit = {
            "limit": response.headers.get("x-ratelimit-limit"),
            "remaining": response.headers.get("x-ratelimit-remaining"),
            "reset": response.headers.get("x-ratelimit-reset"),
            "resource": response.headers.get("x-ratelimit-resource"),
        }
        if response.status_code == 304 and key in self._etag_cache:
            return self._etag_cache[key][1]
        if response.status_code >= 400:
            raise GitHubError(
                method=method,
                path=path,
                status_code=response.status_code,
                detail=response.text,
                params=params,
                request_id=response.headers.get("x-github-request-id"),
            )
        payload = response.json() if response.content else None
        etag = response.headers.get("etag")
        if method.upper() == "GET" and etag:
            self._etag_cache[key] = (etag, payload)
        return payload

    def get_repository(self, repository: str) -> dict[str, Any]:
        owner, repo = repository.split("/", 1)
        return self._request("GET", f"/repos/{owner}/{repo}")

    def get_content(self, repository: str, path: str, ref: str | None = None) -> dict[str, Any]:
        owner, repo = repository.split("/", 1)
        encoded = quote(path.strip("/"), safe="/")
        params = {"ref": ref} if ref else None
        payload = self._request("GET", f"/repos/{owner}/{repo}/contents/{encoded}", params=params)
        if not isinstance(payload, dict):
            raise GitHubError("GET", f"/repos/{owner}/{repo}/contents/{encoded}", 422, "expected file object", params)
        return payload

    def get_content_text(self, repository: str, path: str, ref: str | None = None) -> str:
        payload = self.get_content(repository, path, ref)
        if payload.get("encoding") != "base64":
            raise GitHubError("GET", path, 422, "unexpected repository content encoding", {"ref": ref} if ref else None)
        return base64.b64decode(str(payload["content"])).decode("utf-8")

    def get_pull_request(self, repository: str, pr_number: int) -> dict[str, Any]:
        owner, repo = repository.split("/", 1)
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

    def list_pull_request_files(self, repository: str, pr_number: int) -> list[dict[str, Any]]:
        owner, repo = repository.split("/", 1)
        page = 1
        rows: list[dict[str, Any]] = []
        while True:
            batch = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    def list_pull_request_commits(self, repository: str, pr_number: int) -> list[dict[str, Any]]:
        owner, repo = repository.split("/", 1)
        page = 1
        rows: list[dict[str, Any]] = []
        while True:
            batch = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pr_number}/commits",
                params={"per_page": 100, "page": page},
            )
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    def list_issue_comments(self, repository: str, pr_number: int) -> list[dict[str, Any]]:
        owner, repo = repository.split("/", 1)
        page = 1
        rows: list[dict[str, Any]] = []
        while True:
            batch = self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
                params={"per_page": 100, "page": page, "sort": "created", "direction": "asc"},
            )
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    def create_issue_comment(self, repository: str, issue_number: int, body: str) -> dict[str, Any]:
        owner, repo = repository.split("/", 1)
        return self._request("POST", f"/repos/{owner}/{repo}/issues/{issue_number}/comments", json={"body": body})

    def delete_issue_comment(self, repository: str, comment_id: int) -> None:
        owner, repo = repository.split("/", 1)
        self._request("DELETE", f"/repos/{owner}/{repo}/issues/comments/{comment_id}")
