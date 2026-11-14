"""Collect results node: the fan-in join after per-subtask PM review."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)

# Statuses a subtask keeps for good once it has reached them successfully.  A
# subtask in one of these can no longer be in flight.
SETTLED_STATUSES: frozenset[str] = frozenset({"merged", "done", "skipped"})

# Statuses that no longer block the parent task.  "failed" is included, as it
# always has been in finalize_node; "skipped" marks subtasks retired by a
# redecompose round.
TERMINAL_STATUSES: frozenset[str] = SETTLED_STATUSES | {"failed"}


def in_flight_subtask_ids(state: FactoryState) -> list[str]:
    """Return the IDs of subtasks whose grind → review branch has not landed yet.

    A subtask is in flight when it is listed in ``active_grinders`` (dispatched
    to a grinder, and not yet through PM review or handed to escalation) or
    when its status is ``"review"`` (its grinder is done and its ``pm_review``
    is running or about to).  Entries in ``active_grinders`` that point at a
    settled subtask are stale and ignored.

    Args:
        state: Current FactoryState.

    Returns:
        Subtask IDs in ``subtasks`` order.
    """
    active = set(state.get("active_grinders") or [])
    return [
        s["id"]
        for s in state.get("subtasks") or []
        if s["status"] == "review"
        or (s["id"] in active and s["status"] not in SETTLED_STATUSES)
    ]


async def collect_results_node(state: FactoryState) -> dict:
    """Join the per-subtask review branches before the next routing decision.

    Every ``pm_review`` instance flows into this node by a plain edge, so it
    runs once per superstep on the merged state of all branches that finished
    their review in the previous superstep — unlike a conditional edge on
    ``pm_review`` itself, which is evaluated per branch and never sees its
    siblings' results.  ``route_after_collect`` in ``factory.graph`` then
    decides what happens next (wait, dispatch another round, escalate, or move
    on to the post-review pipeline).

    The node itself only does bookkeeping:

    * records the subtasks completed so far in ``completed_subtask_ids``
      (reported by ``/status``);
    * drops stale ``active_grinders`` entries — IDs that are unknown or whose
      subtask is already settled — so the concurrency cap never counts
      grinders that are not running.

    ``failed_subtask_ids`` is deliberately left alone: ``escalate_node`` bases
    its retry / redecompose decision on it, and feeding it from here would
    change escalation behaviour.

    Args:
        state: Current FactoryState after one or more ``pm_review`` branches
            have written their result.

    Returns:
        Partial state update with ``completed_subtask_ids`` and, when stale
        entries were found, an ``active_grinders`` removal.
    """
    subtasks = state.get("subtasks") or []
    status_by_id = {s["id"]: s["status"] for s in subtasks}
    completed = [
        sid for sid, status in status_by_id.items() if status in ("merged", "done")
    ]
    stale = [
        sid
        for sid in state.get("active_grinders") or []
        if status_by_id.get(sid, "merged") in SETTLED_STATUSES
    ]
    logger.info(
        "collect_results: task %s — statuses=%s in_flight=%s stale_active=%s",
        state.get("task_wiki_page", "<unknown>"),
        status_by_id,
        in_flight_subtask_ids(state),
        stale,
    )
    update: dict = {"completed_subtask_ids": completed}
    if stale:
        update["active_grinders"] = {"remove": stale}
    return update
