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


def _task_matches(
    extra: dict,
    status: str | None,
    assignee: str | None,
    parent_task: str | None,
    priority: str | None,
    repo: str | None,
) -> bool:
    """Return whether a page's frontmatter matches the task-list filters."""
    if extra.get("type") not in ("task", "epic"):
        return False
    if status is not None and extra.get("status") != status:
        return False
    if assignee is not None and extra.get("assignee") != assignee:
        return False
    if parent_task is not None and extra.get("parent_task") != parent_task:
        return False
    if priority is not None and extra.get("priority") != priority:
        return False
    if repo is not None and extra.get("repo") != repo:
        return False
    return True


def _candidate_task_names(
    status: str | None,
    assignee: str | None,
    parent_task: str | None,
    priority: str | None,
    repo: str | None,
) -> list[str] | None:
    """Use the in-memory graph index to narrow task pages by the given filters.

    Returns a candidate list of page names (a superset of the true matches, to
    be re-checked against disk by ``_task_matches``), or ``None`` if the graph
    engine is unavailable so the caller falls back to a full scan.

    The engine holds every page's frontmatter in memory and is kept current by
    the file watcher, so this avoids reading and parsing all pages on disk for
    every request. It can lag disk by the watcher's latency, which is fine for
    the factory's polling: a just-changed page is picked up on the next poll.
    """
    from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE, get_engine

    if not GRAPH_ENGINE_AVAILABLE:
        return None
    engine = get_engine()
    if engine is None:
        return None
    # Page writes reach the index only through the file watcher, so trust the
    # index as a query source only while a watcher is keeping it current;
    # otherwise it is a stale snapshot and we must scan disk instead.
    if not engine.is_watching():
        return None

    from graph_core import Filter  # type: ignore[import]

    base = []
    if status is not None:
        base.append(Filter.equals("status", status))
    if assignee is not None:
        base.append(Filter.equals("assignee", assignee))
    if parent_task is not None:
        base.append(Filter.equals("parent_task", parent_task))
    if priority is not None:
        base.append(Filter.equals("priority", priority))
    if repo is not None:
        base.append(Filter.equals("repo", repo))

    # type is task OR epic, and query filters are AND-ed, so query per type.
    names: list[str] = []
    seen: set[str] = set()
    for page_type in ("task", "epic"):
        for info in engine.query([Filter.equals("type", page_type), *base]):
            if info.name not in seen:
                seen.add(info.name)
                names.append(info.name)
    return names


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    assignee: str | None = None,
    parent_task: str | None = None,
    priority: str | None = None,
    repo: str | None = None,
    storage: FileStorage = Depends(get_storage),
) -> list[dict]:
    """List task pages with optional filters.

    Fast path: the graph index narrows candidates in memory, then only those
    pages are loaded from disk. Falls back to a full scan when the engine is
    unavailable. Both paths re-check ``_task_matches`` so the result reflects
    what is actually on disk.
    """
    names = _candidate_task_names(status, assignee, parent_task, priority, repo)

    if names is not None:
        # Load the candidates in one batch off the event loop; missing ones
        # (indexed but since deleted) are skipped.
        pages = await storage.get_pages(names)
        return [
            {"name": page.name, "metadata": page.metadata.model_dump()}
            for page in pages
            if _task_matches(
                page.metadata.model_extra or {},
                status,
                assignee,
                parent_task,
                priority,
                repo,
            )
        ]

    # Fallback: full scan (runs off the event loop in list_pages_with_metadata).
    pages = await storage.list_pages_with_metadata()
    return [
        {"name": page.name, "metadata": page.metadata.model_dump()}
        for page in pages
        if _task_matches(
            page.metadata.model_extra or {},
            status,
            assignee,
            parent_task,
            priority,
            repo,
        )
    ]


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
