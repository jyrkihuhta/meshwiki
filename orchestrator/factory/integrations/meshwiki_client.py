"""Async HTTP client wrapping the MeshWiki JSON API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


def _patch_frontmatter(content: str, updates: dict[str, Any]) -> str:
    """Update or add fields in YAML frontmatter of a page content string.

    Only modifies the first ``---`` block.  Fields already present are
    updated in-place; new fields are appended before the closing ``---``.
    Returns *content* unchanged if no frontmatter block is found.
    """
    if not content.startswith("---\n"):
        return content
    close = content.find("\n---\n", 4)
    if close == -1:
        return content
    front_lines = content[4:close].split("\n")
    body = content[close + 5 :]

    updated_keys: set[str] = set()
    new_front: list[str] = []
    for line in front_lines:
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            if key in updates:
                new_front.append(f"{key}: {updates[key]}")
                updated_keys.add(key)
                continue
        new_front.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_front.append(f"{key}: {value}")

    return "---\n" + "\n".join(new_front) + "\n---\n" + body


class MeshWikiClient:
    """Async client for the MeshWiki JSON API (``/api/v1/``)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.meshwiki_url).rstrip("/")
        self._api_key = api_key or settings.meshwiki_api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=30.0,
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> "MeshWikiClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def get_page(self, name: str) -> dict | None:
        """
        Fetch a wiki page by name.

        Returns the page dict (``{name, content, metadata}``) or ``None`` if
        the page does not exist.
        """
        url = f"{self._base_url}/api/v1/pages/{name}"
        resp = await self._client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def create_page(self, name: str, content: str) -> dict:
        """
        Create or update a wiki page.

        Uses PUT /pages/{name} which creates or overwrites the page.
        Returns the saved page dict.
        """
        url = f"{self._base_url}/api/v1/pages/{name}"
        resp = await self._client.put(
            url,
            json={"name": name, "content": content},
        )
        resp.raise_for_status()
        return resp.json()

    async def transition_task(
        self,
        name: str,
        status: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict:
        """
        Transition a task page to a new status.

        Args:
            name: Task wiki page name.
            status: Target status (must be a valid transition for the current status).
            extra_fields: Additional frontmatter fields to set (e.g. ``pr_url``).

        Returns:
            The updated task page dict.
        """
        url = f"{self._base_url}/api/v1/tasks/{name}/transition"
        payload: dict[str, Any] = {"status": status}
        if extra_fields:
            payload.update(extra_fields)
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def relay_terminal(self, task_name: str, data: str) -> None:
        """Relay a raw PTY / stdout chunk to the MeshWiki live terminal stream.

        Fire-and-forget: errors are logged at DEBUG level and never raised so
        that a transient MeshWiki connectivity issue never aborts the grinder.

        Args:
            task_name: Wiki page name of the task (used as the stream key).
            data: Raw text to push (may contain ANSI escape codes).
        """
        url = f"{self._base_url}/api/v1/tasks/{task_name}/terminal"
        try:
            await self._client.post(url, json={"data": data})
        except Exception as exc:
            logger.debug("terminal relay failed (non-critical): %s", exc)

    async def list_tasks(self, status: str | None = None) -> list[dict]:
        """
        List task pages, optionally filtered by status.

        Returns a list of task dicts.
        """
        url = f"{self._base_url}/api/v1/tasks"
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def rename_page(self, old_name: str, new_name: str) -> None:
        """Move a wiki page to a new name/location.

        Args:
            old_name: Current page name.
            new_name: New page name (may include new path segments).
        """
        url = f"{self._base_url}/api/v1/pages/{old_name}/rename"
        resp = await self._client.post(
            url,
            json={"new_name": new_name},
        )
        resp.raise_for_status()

    async def append_to_page(
        self,
        page_name: str,
        content_to_append: str,
        frontmatter_updates: dict[str, Any] | None = None,
    ) -> None:
        """
        Append content_to_append to the body of the named wiki page.

        Gets the current page content, optionally patches frontmatter fields,
        strips trailing whitespace, appends "\n\n" + content_to_append, then
        PUTs the updated content back in a single round-trip.
        """
        page = await self.get_page(page_name)
        if page is None:
            raise ValueError(f"Page not found: {page_name!r}")
        current_content = page.get("content", "")
        if frontmatter_updates:
            current_content = _patch_frontmatter(current_content, frontmatter_updates)
        new_content = current_content.rstrip() + "\n\n" + content_to_append
        await self.create_page(page_name, new_content)
