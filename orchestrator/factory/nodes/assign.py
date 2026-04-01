"""Assign grinders node: fan out pending subtasks to parallel grinder instances."""

import logging

from langgraph.types import Send

from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


def assign_grinders_node(state: FactoryState) -> dict:
    """No-op state update before fan-out.

    The actual fan-out to parallel grinders is handled by :func:`route_grinders`,
    which is registered as the conditional edge routing function on this node.
    LangGraph requires nodes to return dicts; ``Send`` objects must come from
    routing functions, not nodes directly.
    """
    return {}


def route_grinders(state: FactoryState) -> list[Send]:
    """Fan out pending/changes_requested subtasks to parallel grinder instances.

    Detects file overlap and serializes conflicting subtasks — only subtasks
    with non-overlapping ``files_touched`` sets are dispatched in this round.
    The rest remain 'pending' and will be picked up in a subsequent round.

    Returns:
        List of ``Send`` commands, one per non-conflicting pending subtask.
    """
    pending = [
        s
        for s in state.get("subtasks", [])
        if s["status"] in ("pending", "changes_requested")
    ]

    logger.info(
        "assign_grinders: dispatching %d pending subtask(s) for task %s",
        len(pending),
        state.get("task_wiki_page", "<unknown>"),
    )

    # Detect file overlaps — serialize conflicting pairs
    assigned_files: set[str] = set()
    to_dispatch: list[SubTask] = []

    for subtask in pending:
        files = set(subtask.get("files_touched") or [])
        if files & assigned_files:
            logger.debug(
                "assign_grinders: deferring subtask %s due to file conflict",
                subtask["id"],
            )
        else:
            assigned_files |= files
            to_dispatch.append(subtask)

    return [
        Send("grind", {**state, "_current_subtask_id": subtask["id"]})
        for subtask in to_dispatch
    ]
