"""Assign grinders node: fan out pending subtasks to parallel grinder instances."""

import logging

from langgraph.types import Send

from ..config import FACTORY_MAX_CONCURRENT_SANDBOXES
from ..hbr import get_hbr
from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


def _select_subtasks_to_dispatch(
    state: FactoryState,
) -> list[SubTask]:
    """Select which pending subtasks to dispatch in this round.

    Applies the concurrency cap and file-conflict serialization logic, returning
    the subset of pending subtasks that should be dispatched now.

    Only subtasks whose IDs are not already in ``active_grinders`` are
    considered.  This prevents re-dispatching grinders that are still running
    (relevant in crash-recovery scenarios where ``active_grinders`` is restored
    from the checkpoint).

    Args:
        state: Current FactoryState.

    Returns:
        List of SubTask dicts to dispatch, respecting the concurrency cap and
        file-overlap constraints.
    """
    active = state.get("active_grinders") or []
    slots_free = FACTORY_MAX_CONCURRENT_SANDBOXES - len(active)

    if slots_free <= 0:
        logger.info(
            "assign_grinders: at concurrency cap (%d), no slots free",
            FACTORY_MAX_CONCURRENT_SANDBOXES,
        )
        return []

    if not get_hbr().can_allocate_sandbox():
        logger.info(
            "assign_grinders: daily budget exhausted — deferring dispatch for task %s",
            state.get("task_wiki_page", "<unknown>"),
        )
        return []

    pending = [
        s
        for s in state.get("subtasks", [])
        if s["status"] in ("pending", "changes_requested") and s["id"] not in active
    ]

    logger.info(
        "assign_grinders: %d pending, %d active, %d slots free for task %s",
        len(pending),
        len(active),
        slots_free,
        state.get("task_wiki_page", "<unknown>"),
    )

    assigned_files: set[str] = set()
    to_dispatch: list[SubTask] = []

    for subtask in pending:
        if len(to_dispatch) >= slots_free:
            break
        files = set(subtask.get("files_touched") or [])
        if files & assigned_files:
            logger.debug(
                "assign_grinders: deferring subtask %s due to file conflict",
                subtask["id"],
            )
        else:
            assigned_files |= files
            to_dispatch.append(subtask)

    return to_dispatch


def assign_grinders_node(state: FactoryState) -> dict:
    """No-op state update before fan-out.

    The actual fan-out to parallel grinders is handled by :func:`route_grinders`,
    which is registered as the conditional edge routing function on this node.
    LangGraph requires nodes to return dicts; ``Send`` objects must come from
    routing functions, not nodes directly.
    """
    logger.info(
        "assign_grinders_node: reached, subtasks in state: %d",
        len(state.get("subtasks", [])),
    )
    return {}


def route_grinders(state: FactoryState) -> list[Send]:
    """Fan out pending/changes_requested subtasks to parallel grinder instances.

    Detects file overlap and serializes conflicting subtasks — only subtasks
    with non-overlapping ``files_touched`` sets are dispatched in this round.
    The rest remain 'pending' and will be picked up in a subsequent round.

    Respects FACTORY_MAX_CONCURRENT_SANDBOXES cap — only dispatches up to
    (cap - len(active_grinders)) subtasks in this round.

    Returns:
        List of ``Send`` commands, one per non-conflicting pending subtask.
    """
    active = state.get("active_grinders") or []
    to_dispatch = _select_subtasks_to_dispatch(state)

    logger.info(
        "route_grinders: dispatching %d subtask(s), %d active, cap=%d (task %s)",
        len(to_dispatch),
        len(active),
        FACTORY_MAX_CONCURRENT_SANDBOXES,
        state.get("task_wiki_page", "<unknown>"),
    )

    return [
        Send("grind", {**state, "_current_subtask_id": subtask["id"]})
        for subtask in to_dispatch
    ]
