"""Agent listing endpoints for the agent factory JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from meshwiki.api.auth import require_api_key
from meshwiki.core.dependencies import get_storage
from meshwiki.core.storage import FileStorage

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/agents")
async def list_agents(
    status: str | None = None,
    agent_role: str | None = None,
    storage: FileStorage = Depends(get_storage),
) -> list[dict]:
    """List agent pages with optional filters."""
    pages = await storage.list_pages_with_metadata()

    results = []
    for page in pages:
        extra = page.metadata.model_extra or {}

        if extra.get("type") != "agent":
            continue
        if status is not None and extra.get("status") != status:
            continue
        if agent_role is not None and extra.get("agent_role") != agent_role:
            continue

        results.append(
            {
                "name": page.name,
                "metadata": page.metadata.model_dump(),
            }
        )

    return results


@router.get("/agents/{name:path}")
async def get_agent(
    name: str,
    storage: FileStorage = Depends(get_storage),
) -> dict:
    """Get a single agent page."""
    page = await storage.get_page(name)
    if page is None or not page.exists:
        raise HTTPException(status_code=404, detail=f"Agent page not found: {name!r}")
    return {
        "name": page.name,
        "content": page.content,
        "metadata": page.metadata.model_dump(),
    }
