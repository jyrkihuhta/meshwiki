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
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def get_pr(self, pr_number: int) -> dict:
        """Fetch full pull request metadata.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            PR object dict (includes ``state``, ``merged``, ``mergeable``, etc.).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"/repos/{self._repo}/pulls/{pr_number}"
        resp = await self._client.get(url, headers=self._headers())
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
        url = f"/repos/{self._repo}/pulls/{pr_number}"
        headers = {**self._headers(), "Accept": "application/vnd.github.v3.diff"}
        resp = await self._client.get(url, headers=headers)
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
        url = f"/repos/{self._repo}/issues/{pr_number}/comments"
        resp = await self._client.post(
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
        url = f"/repos/{self._repo}/pulls/{pr_number}/reviews"
        resp = await self._client.post(
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
        url = f"/repos/{self._repo}/pulls/{pr_number}/reviews"
        resp = await self._client.post(
            url,
            headers=self._headers(),
            json={"event": "APPROVE", "body": body},
        )
        resp.raise_for_status()
        return resp.json()

    async def merge_pr(
        self,
        pr_number: int,
        commit_title: str = "",
        merge_method: str = "squash",
    ) -> dict:
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
        url = f"/repos/{self._repo}/pulls/{pr_number}/merge"
        body: dict = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        resp = await self._client.put(url, headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_file_content(self, path: str, ref: str = "staging") -> str:
        """Fetch raw content of a file from the repository.

        Args:
            path: File path relative to repo root, e.g. ``src/meshwiki/core/parser.py``.
            ref: Branch, tag, or commit SHA. Defaults to ``"staging"``.

        Returns:
            Decoded file content as a UTF-8 string.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
            ValueError: If the API response is not a file object.
        """
        import base64

        url = f"/repos/{self._repo}/contents/{path}"
        resp = await self._client.get(url, headers=self._headers(), params={"ref": ref})
        resp.raise_for_status()
        data = resp.json()
        if data.get("type") != "file":
            raise ValueError(f"Not a file: {path!r} (type={data.get('type')})")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    async def close_pr(self, pr_number: int) -> dict:
        """Close a pull request without merging.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            Updated PR object dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"/repos/{self._repo}/pulls/{pr_number}"
        resp = await self._client.patch(
            url,
            headers=self._headers(),
            json={"state": "closed"},
        )
        resp.raise_for_status()
        return resp.json()


    async def get_pr_files(self, pr_number: int) -> list[dict]:
        """List files changed by a pull request.

        Args:
            pr_number: GitHub pull request number.

        Returns:
            List of file objects, each with ``filename``, ``status``,
            ``additions``, ``deletions``, and ``patch`` (unified diff hunk).
            Up to 300 files are returned (GitHub's API limit per page).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """
        url = f"/repos/{self._repo}/pulls/{pr_number}/files"
        resp = await self._client.get(
            url,
            headers=self._headers(),
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    async def list_open_prs(self, head_prefix: str = "factory/") -> list[dict]:
        """List open PRs whose head branch starts with *head_prefix*.

        Args:
            head_prefix: Branch name prefix to filter on (default ``"factory/"``).

        Returns:
            List of PR objects from the GitHub API.
        """
        url = f"/repos/{self._repo}/pulls"
        resp = await self._client.get(
            url,
            headers=self._headers(),
            params={"state": "open", "per_page": 100},
        )
        resp.raise_for_status()
        return [
            pr for pr in resp.json()
            if pr.get("head", {}).get("ref", "").startswith(head_prefix)
        ]

    async def get_check_runs(self, sha: str) -> list[dict]:
        """Get all check runs for a commit SHA.

        Args:
            sha: Full commit SHA.

        Returns:
            List of check run objects from the GitHub Checks API.
        """
        url = f"/repos/{self._repo}/commits/{sha}/check-runs"
        resp = await self._client.get(
            url,
            headers=self._headers(),
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json().get("check_runs", [])

    async def get_job_log(self, job_id: int) -> str:
        """Fetch the plain-text log for a GitHub Actions job.

        Follows the redirect that GitHub returns and returns the log as text.

        Args:
            job_id: GitHub Actions job ID.

        Returns:
            Log content as a string (may be large).
        """
        url = f"/repos/{self._repo}/actions/jobs/{job_id}/logs"
        # GitHub returns a 302 redirect to a pre-signed URL for the log.
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        try:
            resp = await client.get(
                f"{_BASE_URL}{url}",
                headers={**self._headers(), "Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            return resp.text
        finally:
            await client.aclose()


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
