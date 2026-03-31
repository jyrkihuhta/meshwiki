"""Task-specific endpoints for the agent factory JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from meshwiki.api.auth import require_api_key
from meshwiki.core.dependencies import get_storage
from meshwiki.core.storage import FileStorage
from meshwiki.core.task_machine import InvalidTransitionError, transition_task

router = APIRouter(dependencies=[Depends(require_api_key)])


class TransitionRequest(BaseModel):
    status: str
    extra_fields: dict[str, str] | None = None


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    assignee: str | None = None,
    parent_task: str | None = None,
    priority: str | None = None,
    storage: FileStorage = Depends(get_storage),
) -> list[dict]:
    """List task pages with optional filters."""
    pages = await storage.list_pages_with_metadata()

    results = []
    for page in pages:
        extra = page.metadata.model_extra or {}

        if extra.get("type") != "task":
            continue
        if status is not None and extra.get("status") != status:
            continue
        if assignee is not None and extra.get("assignee") != assignee:
            continue
        if parent_task is not None and extra.get("parent_task") != parent_task:
            continue
        if priority is not None and extra.get("priority") != priority:
            continue

        results.append(
            {
                "name": page.name,
                "metadata": page.metadata.model_dump(),
            }
        )

    return results


@router.post("/tasks/{name:path}/transition")
async def transition(
    name: str,
    body: TransitionRequest,
    storage: FileStorage = Depends(get_storage),
) -> dict:
    """Apply a state machine transition to a task page."""
    try:
        metadata = await transition_task(
            storage,
            name,
            body.status,
            extra_fields=body.extra_fields,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    return {"success": True, "metadata": metadata}
