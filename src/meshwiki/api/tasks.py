"""Task-specific endpoints for the agent factory JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from meshwiki.api.auth import require_api_key
from meshwiki.api.validation import validate_api_page_name
from meshwiki.core.dependencies import get_storage
from meshwiki.core.storage import FileStorage
from meshwiki.core.task_machine import InvalidTransitionError, transition_task
from meshwiki.core.terminal_sessions import close_session, create_session, put_chunk

router = APIRouter(dependencies=[Depends(require_api_key)])


class TransitionRequest(BaseModel):
    status: str
    extra_fields: dict[str, str] | None = None


class TerminalChunkRequest(BaseModel):
    data: str


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    assignee: str | None = None,
    parent_task: str | None = None,
    priority: str | None = None,
    repo: str | None = None,
    storage: FileStorage = Depends(get_storage),
) -> list[dict]:
    """List task pages with optional filters."""
    pages = await storage.list_pages_with_metadata()

    results = []
    for page in pages:
        extra = page.metadata.model_extra or {}

        if extra.get("type") not in ("task", "epic"):
            continue
        if status is not None and extra.get("status") != status:
            continue
        if assignee is not None and extra.get("assignee") != assignee:
            continue
        if parent_task is not None and extra.get("parent_task") != parent_task:
            continue
        if priority is not None and extra.get("priority") != priority:
            continue
        if repo is not None and extra.get("repo") != repo:
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
    validate_api_page_name(name)
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

    # Manage terminal session lifecycle.
    if body.status == "in_progress":
        create_session(name)
    else:
        await close_session(name)

    return {"success": True, "metadata": metadata}


@router.post("/tasks/{name:path}/terminal")
async def append_terminal_chunk(name: str, body: TerminalChunkRequest) -> dict:
    """Receive a raw PTY chunk from the orchestrator and relay to browser.

    The orchestrator calls this endpoint once per ``on_data`` callback from the
    E2B PTY.  The chunk is dropped silently if no browser is connected or the
    queue is full.

    If no session exists (e.g. orchestrator restarted mid-grind and the
    in_progress transition was rejected as a no-op), create one on the fly so
    chunks are not silently dropped.
    """
    from meshwiki.core.terminal_sessions import create_session, get_session

    validate_api_page_name(name)
    if get_session(name) is None:
        create_session(name)
    await put_chunk(name, body.data)
    return {"ok": True}
