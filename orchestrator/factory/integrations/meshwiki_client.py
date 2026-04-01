"""Async HTTP client wrapping the MeshWiki JSON API."""

import logging
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


class MeshWikiClient:
    """Async client for the MeshWiki JSON API (``/api/v1/``)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.meshwiki_url).rstrip("/")
        self._api_key = api_key or settings.meshwiki_api_key

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def get_page(self, name: str) -> dict | None:
        """
        Fetch a wiki page by name.

        Returns the page dict (``{name, content, metadata}``) or ``None`` if
        the page does not exist.
        """
        url = f"{self._base_url}/api/v1/pages/{name}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def create_page(self, name: str, content: str) -> dict:
        """
        Create or update a wiki page.

        Returns the saved page dict.
        """
        url = f"{self._base_url}/api/v1/pages/{name}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json={"content": content},
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
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_tasks(self, status: str | None = None) -> list[dict]:
        """
        List task pages, optionally filtered by status.

        Returns a list of task dicts.
        """
        url = f"{self._base_url}/api/v1/tasks"
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            return resp.json()
