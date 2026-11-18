"""Assign grinders node: fan out pending subtasks to parallel grinder instances."""

import logging
from typing import Literal

from langgraph.types import Command, Send

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

    ``active_grinders`` holds the subtasks whose grind → review branch is still
    in flight.  They count against the cap, are never dispatched a second time,
    and their ``files_touched`` block any pending subtask that overlaps them.

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

    subtasks = state.get("subtasks", [])
    pending = [
        s
        for s in subtasks
        if s["status"] in ("pending", "changes_requested") and s["id"] not in active
    ]

    logger.info(
        "assign_grinders: %d pending, %d active, %d slots free for task %s",
        len(pending),
        len(active),
        slots_free,
        state.get("task_wiki_page", "<unknown>"),
    )

    # Files held by subtasks that are still in flight are off limits too.
    assigned_files: set[str] = set()
    for subtask in subtasks:
        if subtask["id"] in active:
            assigned_files |= set(subtask.get("files_touched") or [])
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


def route_grinders(state: FactoryState) -> list[Send]:
    """Build the fan-out for this round: one ``Send("grind", ...)`` per subtask.

    Detects file overlap and serializes conflicting subtasks — only subtasks
    with non-overlapping ``files_touched`` sets are dispatched in this round.
    The rest remain 'pending'; the ``collect`` join routes back to
    ``assign_grinders`` once this round's branches have landed, which picks
    them up.

    Respects FACTORY_MAX_CONCURRENT_SANDBOXES cap — only dispatches up to
    (cap - len(active_grinders)) subtasks in this round.

    Called by :func:`assign_grinders_node`, which issues the returned ``Send``
    commands and records the dispatched IDs in ``active_grinders``.

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


def assign_grinders_node(state: FactoryState) -> Command[Literal["grind"]]:
    """Dispatch this round's subtasks and mark them as in flight.

    Selects the subtasks to grind now (see :func:`route_grinders`), adds their
    IDs to ``active_grinders`` and fans out one ``grind`` instance per subtask.
    Doing both from the node — a ``Command`` carrying the state update and the
    ``Send`` list — means the checkpoint written when this node finishes
    already lists the grinders that are about to run, so the concurrency cap
    and ``/status`` see them while they are running.

    That checkpoint also holds the pending ``grind`` tasks.  If the
    orchestrator restarts while they run, ``ainvoke(None)`` re-runs the ones
    that had not finished; a ``pending`` subtask listed in ``active_grinders``
    is therefore the normal mid-grind state, not a stuck one, and startup
    recovery (``webhook_server._clear_stuck_grinders``) must not rewrite it.

    Args:
        state: Current FactoryState.

    Returns:
        A ``Command`` that adds the dispatched subtask IDs to
        ``active_grinders`` and goes to one ``grind`` per dispatched subtask.
        When nothing can be dispatched it carries no update and no
        destination.
    """
    sends = route_grinders(state)
    dispatched = [send.arg["_current_subtask_id"] for send in sends]
    logger.info(
        "assign_grinders_node: %d subtask(s) in state, dispatching %s",
        len(state.get("subtasks", [])),
        dispatched,
    )
    if not sends:
        waiting = [
            s["id"]
            for s in state.get("subtasks", [])
            if s["status"] in ("pending", "changes_requested")
        ]
        if waiting and not state.get("active_grinders"):
            # Nothing is in flight that could bring the run back here, so
            # these subtasks stay undone until the task is started again.
            logger.warning(
                "assign_grinders_node: %s waiting for a grinder but none could "
                "be dispatched (budget gate or concurrency cap) — task %s stops here",
                waiting,
                state.get("task_wiki_page", "<unknown>"),
            )
        return Command()
    return Command(update={"active_grinders": {"add": dispatched}}, goto=sends)
