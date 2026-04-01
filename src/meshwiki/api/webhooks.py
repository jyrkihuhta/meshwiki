"""Inbound GitHub webhook receiver for the agent factory.

Handles ``pull_request`` events from GitHub and auto-transitions task pages
when a PR is merged: ``review -> merged -> done``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from meshwiki.core.dependencies import get_storage
from meshwiki.core.storage import FileStorage
from meshwiki.core.task_machine import InvalidTransitionError, transition_task

import meshwiki.config as cfg

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_github_signature(
    secret: str, raw_body: bytes, signature_header: str | None
) -> bool:
    """Verify the GitHub HMAC-SHA256 webhook signature.

    Args:
        secret: The webhook secret configured in ``MESHWIKI_GITHUB_WEBHOOK_SECRET``.
        raw_body: The raw request body bytes.
        signature_header: Value of the ``X-Hub-Signature-256`` header.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, received)


async def _find_task_by_pr_number(
    storage: FileStorage,
    pr_number: int,
) -> str | None:
    """Look up a task page by its ``pr_number`` frontmatter field.

    Tries the graph engine first; falls back to scanning all pages via
    ``list_pages_with_metadata()``.

    Args:
        storage: FileStorage instance.
        pr_number: The GitHub PR number to search for.

    Returns:
        The page name if found, or ``None``.
    """
    pr_number_str = str(pr_number)

    # Try graph engine first (optional dependency)
    try:
        from meshwiki.core.graph import get_engine, GRAPH_ENGINE_AVAILABLE

        if GRAPH_ENGINE_AVAILABLE:
            engine = get_engine()
            if engine is not None:
                from graph_core import Filter  # type: ignore[import]

                results = engine.query(
                    [
                        Filter("type", "=", "task"),
                        Filter("pr_number", "=", pr_number_str),
                    ]
                )
                if results:
                    return results[0]
    except Exception:
        logger.debug(
            "Graph engine lookup failed, falling back to metadata scan", exc_info=True
        )

    # Fallback: scan all pages
    pages = await storage.list_pages_with_metadata()
    for page in pages:
        extra = page.metadata.model_extra or {}
        if extra.get("type") == "task" and extra.get("pr_number") == pr_number_str:
            return page.name

    return None


@router.post("/github/webhook")
async def github_webhook(
    request: Request,
    storage: FileStorage = Depends(get_storage),
) -> dict[str, Any]:
    """Receive and process inbound GitHub webhook events.

    Handles ``pull_request`` events where ``action == "closed"`` and
    ``pull_request.merged == true``, transitioning the matching task page
    from ``review`` through ``merged`` to ``done``.

    Authentication is via HMAC-SHA256 signature in the ``X-Hub-Signature-256``
    header.  If ``MESHWIKI_GITHUB_WEBHOOK_SECRET`` is empty, verification is
    skipped (dev mode).

    Returns:
        A dict with a ``status`` key indicating the outcome.
    """
    raw_body = await request.body()

    # HMAC verification
    secret = cfg.settings.github_webhook_secret
    if secret:
        signature = request.headers.get("X-Hub-Signature-256")
        if not _verify_github_signature(secret, raw_body, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook signature",
            )

    # Parse JSON body
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    event_type = request.headers.get("X-GitHub-Event", "")

    # Only handle pull_request events that represent a merge
    if event_type != "pull_request":
        logger.debug("Ignoring GitHub event: %s", event_type)
        return {"status": "ignored"}

    action = payload.get("action")
    pr = payload.get("pull_request", {})
    merged = pr.get("merged", False)

    if action != "closed" or not merged:
        logger.debug("Ignoring PR event: action=%s merged=%s", action, merged)
        return {"status": "ignored"}

    pr_number: int = pr["number"]
    merged_at_raw: str | None = pr.get("merged_at")

    # Normalise merged_at to ISO 8601 UTC
    if merged_at_raw:
        merged_at = merged_at_raw
    else:
        merged_at = datetime.now(timezone.utc).isoformat()

    # Look up matching task page
    page_name = await _find_task_by_pr_number(storage, pr_number)
    if page_name is None:
        logger.warning(
            "GitHub PR #%d merged but no matching task page found (pr_number=%s)",
            pr_number,
            pr_number,
        )
        return {"status": "no_task_found"}

    logger.info(
        "PR #%d merged — transitioning task page %r: review -> merged -> done",
        pr_number,
        page_name,
    )

    # Transition: review -> merged (store merged_at)
    try:
        await transition_task(
            storage,
            page_name,
            "merged",
            extra_fields={"merged_at": merged_at},
        )
    except (ValueError, InvalidTransitionError) as exc:
        logger.error("Failed to transition %r to 'merged': %s", page_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transition to 'merged' failed: {exc}",
        )

    # Transition: merged -> done
    try:
        await transition_task(storage, page_name, "done")
    except (ValueError, InvalidTransitionError) as exc:
        logger.error("Failed to transition %r to 'done': %s", page_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transition to 'done' failed: {exc}",
        )

    return {"status": "ok", "page": page_name}
