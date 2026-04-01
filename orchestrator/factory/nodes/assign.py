"""Assign grinders node: fan out pending subtasks to parallel grinder instances."""

import logging

from langgraph.types import Send

from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


def assign_grinders_node(state: FactoryState) -> list[Send]:
    """
    Fan out pending/changes_requested subtasks to parallel grinder instances.

    Detects file overlap and serializes conflicting subtasks — only subtasks
    with non-overlapping ``files_touched`` sets are dispatched in this round.
    The rest remain 'pending' and will be dispatched in a subsequent round.

    Stub: builds Send commands with correct structure but grind_node is also
    a stub. File-overlap detection logic is present as specified.
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
            # Defer: leave status as-is, will be picked up next round
            logger.debug(
                "assign_grinders: deferring subtask %s due to file conflict",
                subtask["id"],
            )
        else:
            assigned_files |= files
            to_dispatch.append(subtask)

    sends = [
        Send("grind", {**state, "_current_subtask_id": subtask["id"]})
        for subtask in to_dispatch
    ]
    return sends
