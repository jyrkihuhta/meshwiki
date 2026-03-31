"""Task state machine for the agent factory.

Enforces legal status transitions on task wiki pages and fires outbound
webhook events after each successful transition.
"""

from __future__ import annotations

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
    "review": ["merged", "rejected", "blocked"],
    "merged": ["done"],
    "done": [],
    "failed": ["planned", "blocked"],
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
    ("merged", "done"): "task.completed",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


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

    # Apply status change
    await storage.update_frontmatter_field(page_name, "status", new_status)

    # Apply optional extra fields
    if extra_fields:
        for field_name, value in extra_fields.items():
            await storage.update_frontmatter_field(page_name, field_name, value)

    # Reload to return current metadata
    updated = await storage.get_page(page_name)
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

    return metadata_dict
