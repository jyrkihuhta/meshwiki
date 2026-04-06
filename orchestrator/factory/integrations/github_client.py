"""GitHub API client for the factory orchestrator."""

from __future__ import annotations

import logging
import re

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"


class GitHubClient:
    """Async HTTP client for the GitHub REST API.

    Args:
        token: GitHub personal access token (or app token).  Defaults to
            ``FACTORY_GITHUB_TOKEN`` from settings.
        repo: Repository slug in ``owner/name`` format.  Defaults to
            ``FACTORY_GITHUB_REPO`` from settings.
    """

    def __init__(self, token: str | None = None, repo: str | None = None) -> None:
        settings = get_settings()
        self._token = token or settings.github_token
        self._repo = repo or settings.github_repo

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def get_pr(self, pr_number: int) -> dict:
        """Fetch full pull request metadata.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            PR object dict (includes ``state``, ``merged``, ``mergeable``, etc.).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_pr_diff(self, pr_number: int) -> str:
        """Fetch the unified diff for a pull request.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            Unified diff as a string.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}"
        headers = {**self._headers(), "Accept": "application/vnd.github.v3.diff"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    async def create_pr_comment(self, pr_number: int, body: str) -> dict:
        """Post an issue comment on a pull request.

        Args:
            pr_number: GitHub pull request number.
            body: Markdown comment body.

        Returns:
            Created comment object dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/issues/{pr_number}/comments"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"body": body},
            )
            resp.raise_for_status()
            return resp.json()

    async def request_changes(self, pr_number: int, body: str) -> dict:
        """Submit a 'REQUEST_CHANGES' review on a pull request.

        Args:
            pr_number: GitHub pull request number.
            body: Review comment body.

        Returns:
            Created review object dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}/reviews"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"event": "REQUEST_CHANGES", "body": body},
            )
            resp.raise_for_status()
            return resp.json()

    async def approve_pr(self, pr_number: int, body: str = "") -> dict:
        """Submit an 'APPROVE' review on a pull request.

        Args:
            pr_number: GitHub pull request number.
            body: Optional review comment body.

        Returns:
            Created review object dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}/reviews"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"event": "APPROVE", "body": body},
            )
            resp.raise_for_status()
            return resp.json()

    async def merge_pr(self, pr_number: int, commit_title: str = "", merge_method: str = "squash") -> dict:
        """Merge a pull request via the GitHub API.

        Args:
            pr_number: GitHub pull request number.
            commit_title: Optional merge commit title. Defaults to GitHub's default.
            merge_method: One of "merge", "squash", or "rebase". Defaults to "squash".

        Returns:
            Merge result dict (includes ``merged`` boolean).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}/merge"
        body: dict = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        async with httpx.AsyncClient() as client:
            resp = await client.put(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            return resp.json()

    async def close_pr(self, pr_number: int) -> dict:
        """Close a pull request without merging.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            Updated PR object dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"{_BASE_URL}/repos/{self._repo}/pulls/{pr_number}"
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                url,
                headers=self._headers(),
                json={"state": "closed"},
            )
            resp.raise_for_status()
            return resp.json()


def _extract_pr_number(pr_url: str) -> int | None:
    """Extract a PR number from a GitHub PR URL.

    Args:
        pr_url: Full GitHub pull request URL, e.g.
            ``https://github.com/owner/repo/pull/42``.

    Returns:
        Integer PR number, or ``None`` if the URL cannot be parsed.
    """
    match = re.search(r"/pull/(\d+)", pr_url)
    if match:
        return int(match.group(1))
    return None
