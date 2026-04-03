"""FastAPI webhook server receiving MeshWiki outbound events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from .config import get_settings
from .graph import build_graph
from .state import FactoryState

logger = logging.getLogger(__name__)

app = FastAPI(title="Factory Orchestrator")

# Single compiled graph instance shared across webhook handlers.
# The MemorySaver checkpointer is in-process; replace with PostgresSaver for
# production deployments (see graph.build_graph TODO).
_graph = build_graph()


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
        initial_state = _build_initial_state(page_name, data)
        config = {"configurable": {"thread_id": page_name}}
        asyncio.create_task(
            _graph.ainvoke(initial_state, config=config),
            name=f"graph:{page_name}",
        )
        logger.info("webhook: started graph task for %s", page_name)
        return {"status": "started"}

    if event == "task.approved":
        config = {"configurable": {"thread_id": page_name}}
        approval = data.get("approval", "approve")
        feedback = data.get("feedback")
        asyncio.create_task(
            _graph.ainvoke(
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
