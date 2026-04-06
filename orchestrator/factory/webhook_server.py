"""FastAPI webhook server receiving MeshWiki outbound events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import get_settings
from .graph import build_graph
from .integrations.meshwiki_client import MeshWikiClient
from .state import FactoryState

logger = logging.getLogger(__name__)


async def _resume_interrupted_tasks(graph, saver, settings) -> None:
    """On startup, resume any tasks that were active when the orchestrator died.

    Strategy:
    1. Ask MeshWiki for all tasks with status=in_progress or status=review
       that are assigned to factory (both statuses represent active graph runs).
    2. For each, check if the SQLite checkpointer has a saved state.
    3. If yes, call ainvoke(None) with the same thread_id — LangGraph resumes
       from the last node boundary rather than restarting from scratch.
    """
    client = MeshWikiClient(settings.meshwiki_url, settings.meshwiki_api_key)
    all_factory_tasks: list[dict] = []
    for status in ("in_progress", "review"):
        try:
            tasks = await client.list_tasks(status=status)
        except Exception as exc:
            logger.warning("factory: could not fetch %s tasks on startup: %s", status, exc)
            continue
        all_factory_tasks.extend(
            t for t in tasks
            if t.get("metadata", {}).get("assignee") == "factory"
            or t.get("assignee") == "factory"  # flat format (defensive)
        )

    if not all_factory_tasks:
        return

    factory_tasks = all_factory_tasks
    logger.info("factory: found %d active factory task(s) on startup", len(factory_tasks))
    for task in factory_tasks:
        page_name = task.get("name", "")
        if not page_name:
            continue

        config = {"configurable": {"thread_id": page_name}}
        # Check whether a checkpoint exists for this thread.
        checkpoint_tuple = await saver.aget_tuple(config)
        if checkpoint_tuple is None:
            logger.info("factory: no checkpoint for %s — skipping resume", page_name)
            continue

        logger.info("factory: resuming interrupted task %s from checkpoint", page_name)

        def _log_exc(t: asyncio.Task, name: str = page_name) -> None:
            if not t.cancelled() and (exc := t.exception()):
                logger.error("graph task %s failed: %s", name, exc, exc_info=exc)

        resume_task = asyncio.create_task(
            graph.ainvoke(None, config=config),
            name=f"graph:{page_name}:resume",
        )
        resume_task.add_done_callback(_log_exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the SQLite checkpoint DB, build the graph, close on shutdown."""
    settings = get_settings()
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db) as saver:
        app.state.graph = build_graph(saver)
        logger.info("factory: graph initialised with SQLite checkpointer at %s", settings.checkpoint_db)
        await _resume_interrupted_tasks(app.state.graph, saver, settings)
        yield
    logger.info("factory: SQLite checkpointer closed")


app = FastAPI(title="Factory Orchestrator", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """
    Verify the HMAC-SHA256 signature sent in ``X-MeshWiki-Signature``.

    Raises ``HTTPException(403)`` when the secret is configured but the
    signature is missing or invalid.  When the secret is empty (dev mode),
    verification is skipped entirely.
    """
    settings = get_settings()
    if not settings.webhook_secret:
        return  # Dev mode: skip verification

    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing X-MeshWiki-Signature-256")

    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    # Support "sha256=<hex>" prefix (same convention as GitHub webhooks)
    provided = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Invalid signature")


def _build_initial_state(page_name: str, data: dict[str, Any]) -> FactoryState:
    """Build the initial FactoryState for a new graph thread.

    ``skip_decomposition`` is handled by ``task_intake_node`` which reads the
    full page from MeshWiki — no need to inspect the webhook payload here.
    """
    return FactoryState(
        thread_id=page_name,
        task_wiki_page=page_name,
        title=data.get("title", page_name),
        requirements=data.get("requirements", ""),
        subtasks=[],
        decomposition_approved=False,
        active_grinders={},
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        graph_status="intake",
        error=None,
        escalation_decision=None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_meshwiki_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    """
    Receive an outbound webhook event from MeshWiki.

    Expected JSON body::

        {
            "event": "task.assigned",
            "page":  "Task_0001_implement_feature",
            "data":  { ... extra fields ... }
        }

    Events handled:
    - ``task.assigned``  — start a new graph thread for the task
    - ``task.approved``  — resume a paused thread (human approved decomposition)
    - ``task.pr_merged`` — log the merge (GitHub→MeshWiki→orchestrator loop completes)
    - everything else   — log and return ``{"status": "ignored"}``
    """
    body = await request.body()
    _verify_signature(body, x_meshwiki_signature_256)

    payload = await request.json()
    # MeshWiki sends a raw event like "task.planned_to_in_progress" in "event"
    # and the semantic name (e.g. "task.assigned") in "canonical_event".
    raw_event: str = payload.get("event", "")
    event: str = payload.get("canonical_event") or raw_event
    page_name: str = payload.get("page", "")
    data: dict[str, Any] = payload.get("data", {})

    logger.info("webhook: received event=%s (raw=%s) page=%s", event, raw_event, page_name)

    if event == "task.assigned":
        graph = request.app.state.graph
        initial_state = _build_initial_state(page_name, data)
        config = {"configurable": {"thread_id": page_name}}

        def _log_exc(t: asyncio.Task, name: str = page_name) -> None:
            if not t.cancelled() and (exc := t.exception()):
                logger.error("graph task %s failed: %s", name, exc, exc_info=exc)

        task = asyncio.create_task(
            graph.ainvoke(initial_state, config=config),
            name=f"graph:{page_name}",
        )
        task.add_done_callback(_log_exc)
        logger.info("webhook: started graph task for %s", page_name)
        return {"status": "started"}

    if event == "task.approved":
        graph = request.app.state.graph
        config = {"configurable": {"thread_id": page_name}}
        approval = data.get("approval", "approve")
        feedback = data.get("feedback")
        asyncio.create_task(
            graph.ainvoke(
                {"human_approval_response": approval, "human_feedback": feedback},
                config=config,
            ),
            name=f"graph:{page_name}:resume",
        )
        logger.info(
            "webhook: resumed graph task for %s (approval=%s)", page_name, approval
        )
        return {"status": "resumed"}

    if event == "task.pr_merged":
        # The GitHub→MeshWiki→orchestrator loop completes here.
        # The grind node will have already been notified; this event is
        # informational — no graph action required at this stage.
        logger.info(
            "webhook: PR merged for %s — GitHub loop complete",
            page_name,
        )
        return {"status": "acknowledged"}

    logger.debug("webhook: ignoring event=%s page=%s", event, page_name)
    return {"status": "ignored"}
