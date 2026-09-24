"""Task state machine for the agent factory.

Enforces legal status transitions on task wiki pages and fires outbound
webhook events after each successful transition.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshwiki.core.storage import FileStorage

# ---------------------------------------------------------------------------
# State graph
# ---------------------------------------------------------------------------

TASK_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["planned", "blocked"],
    "planned": ["decomposed", "in_progress", "blocked"],
    "decomposed": ["approved", "planned", "blocked"],
    "approved": ["in_progress", "blocked"],
    "in_progress": ["review", "failed", "blocked"],
    "review": ["merged", "rejected", "in_progress", "blocked"],
    "merged": ["done"],
    "done": [],
    "failed": ["planned", "in_progress", "blocked"],
    "rejected": ["in_progress", "blocked"],
    "blocked": ["planned", "approved", "in_progress"],
}

# Canonical event names for well-known transitions
CANONICAL_EVENTS: dict[tuple[str, str], str] = {
    ("planned", "decomposed"): "task.decomposed",
    ("decomposed", "approved"): "task.approved",
    ("approved", "in_progress"): "task.assigned",
    ("in_progress", "review"): "task.pr_created",
    ("review", "merged"): "task.pr_merged",
    ("review", "rejected"): "task.pr_rejected",
    ("review", "in_progress"): "task.rework",
    ("merged", "done"): "task.completed",
    ("planned", "in_progress"): "task.assigned",  # direct grind (no PM decomposition)
    (
        "failed",
        "in_progress",
    ): "task.retry",  # escalator retry — does not re-trigger webhook
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

# One lock per task page so the status check and the write happen as a unit.
# Without it, two concurrent transitions can both validate against the same
# old status and the later write silently wins. MeshWiki runs as a single
# process, so an in-process lock is sufficient. Weak values let idle locks be
# garbage-collected instead of accumulating one per page forever.
_page_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _page_lock(page_name: str) -> asyncio.Lock:
    """Return the transition lock for *page_name*, creating it if needed."""
    lock = _page_locks.get(page_name)
    if lock is None:
        lock = asyncio.Lock()
        _page_locks[page_name] = lock
    return lock


async def transition_task(
    storage: "FileStorage",
    page_name: str,
    new_status: str,
    *,
    extra_fields: dict[str, str] | None = None,
) -> dict:
    """Apply a state machine transition to a task page.

    Args:
        storage: FileStorage instance.
        page_name: Name of the task wiki page.
        new_status: Target status.
        extra_fields: Optional extra frontmatter fields to write
            (e.g. ``{"branch": "factory/task-0001-foo", "assignee": "grinder-1"}``).

    Returns:
        The updated page metadata as a plain dict.

    Raises:
        ValueError: If the page does not exist.
        InvalidTransitionError: If the transition is not permitted.
    """
    async with _page_lock(page_name):
        page = await storage.get_page(page_name)
        if page is None:
            raise ValueError(f"Page not found: {page_name!r}")

        current_status: str = (page.metadata.model_extra or {}).get("status", "draft")

        allowed = TASK_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {current_status!r} to {new_status!r}. "
                f"Allowed: {allowed}"
            )

        # C13: factory tasks must have assignee:factory before going in_progress
        extras = page.metadata.model_extra or {}
        page_type = extras.get("type")
        if (
            new_status == "in_progress"
            and current_status in ("planned", "approved")
            and page_type in ("task", "epic")
            and extras.get("assignee") != "factory"
        ):
            raise InvalidTransitionError(
                f"Cannot start task {page_name!r}: assignee is "
                f"{extras.get('assignee')!r}, expected 'factory'. "
                f"Set 'assignee: factory' in the page frontmatter first."
            )

        # Apply the status change together with any extra fields in one write.
        # Doing one write per field (as before) re-read and re-wrote the whole
        # file N times; combined with the page lock this makes the transition
        # all-or-nothing with respect to other transitions (e.g. the webhook merge
        # flow's review→merged→done).
        fields: dict[str, str | None] = {"status": new_status}
        if extra_fields:
            fields.update(extra_fields)
        updated = await storage.patch_frontmatter(page_name, fields)
        assert updated is not None
        metadata_dict = updated.metadata.model_dump()

    # Emit webhook (lazy import to avoid circular dependency)
    from meshwiki.config import settings

    if settings.factory_enabled:
        from meshwiki.core.webhooks import dispatcher

        raw_event = f"task.{current_status}_to_{new_status}"
        canonical = CANONICAL_EVENTS.get((current_status, new_status))
        await dispatcher.emit(
            event=raw_event,
            page_name=page_name,
            data=metadata_dict,
            canonical_event=canonical,
        )

        from meshwiki.core.factory_ws_manager import factory_ws_manager

        await factory_ws_manager.broadcast(
            {
                "type": "transition",
                "name": page_name,
                "from": current_status,
                "to": new_status,
                "time": time.time() * 1000,
            }
        )

    return metadata_dict
