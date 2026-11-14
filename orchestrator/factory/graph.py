"""LangGraph state machine definition for the factory orchestrator."""

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import get_settings
from .nodes import (
    assign_grinders_node,
    collect_results_node,
    decompose_node,
    escalate_node,
    finalize_node,
    grind_node,
    human_review_code_node,
    merge_check_node,
    pm_diagnose_node,
    pm_review_node,
    task_intake_node,
    validate_armory_node,
)
from .nodes.collect import TERMINAL_STATUSES, in_flight_subtask_ids
from .nodes.grind import next_step_after_grind
from .state import FactoryState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_after_intake(state: FactoryState) -> str:
    """Route after task intake."""
    if state.get("graph_status") == "failed":
        return "abort"
    if state.get("graph_status") == "grinding":
        return "skip_decompose"
    if state.get("decomposition_approved"):
        return "skip_decompose"
    return "decompose"


def route_after_grinding(state: FactoryState) -> list[Send] | str:
    """Route after a single grinder instance completes.

    Fan-out semantics: each completed grind branch is sent immediately to its
    own ``pm_review`` instance via ``Send()``.  A first failure goes to
    ``pm_diagnose`` (the PM rewrites the task and the subtask is ground again);
    any later failure escalates the whole group.

    Subtasks that were held back by the concurrency cap or by file-conflict
    serialization are not handled here: they stay ``pending`` until this
    round's branches land at the ``collect`` join, and ``route_after_collect``
    then loops back to ``assign_grinders``.

    The subtask identity is communicated via ``_current_subtask_id`` in the
    sent payload so ``pm_review_node`` knows which single subtask to review.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state["subtasks"] if s["id"] == subtask_id),
        None,
    )

    if subtask is None:
        # Should not happen — fall back to escalate path.
        logger.error(
            "route_after_grinding: _current_subtask_id %r not found — escalating",
            subtask_id,
        )
        return "escalate"

    next_step = next_step_after_grind(subtask)
    if next_step == "escalate":
        return "escalate"
    # "pm_diagnose": first failure — let PM diagnose and rewrite the task
    # before retrying.  "pm_review": fan out to its own pm_review immediately.
    return [Send(next_step, {**state, "_current_subtask_id": subtask_id})]


def route_after_pm_diagnose(state: FactoryState) -> list[Send] | str:
    """Route after the PM diagnosed a first grinder failure.

    When the PM produced a revised description the subtask is ``pending``
    again and is ground once more (same branch, still in flight).  Otherwise
    the subtask is still ``failed`` and the group escalates.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state["subtasks"] if s["id"] == subtask_id),
        None,
    )
    if subtask is not None and subtask["status"] == "pending":
        return [Send("grind", {**state, "_current_subtask_id": subtask_id})]
    return "escalate"


def route_after_collect(state: FactoryState) -> str:
    """Decide what happens after the ``collect`` join.

    ``collect`` runs once per superstep on the merged state of every
    ``pm_review`` branch that just finished, so — unlike a per-branch router —
    this function sees all siblings' results.  In order:

    * ``"wait"`` — another subtask's grind → review branch is still in flight
      (for example it is being re-ground after a PM diagnosis).  Nothing to
      decide yet; that branch lands here too and triggers this router again.
    * ``"escalate"`` — a subtask was sent back by the PM but has used up its
      attempts.
    * ``"dispatch"`` — subtasks are waiting for a grinder: ones the PM sent
      back for rework, and ones held back so far by the concurrency cap or by
      file-conflict serialization.  Back to ``assign_grinders`` for the next
      round.
    * ``"all_approved"`` / ``"skip_human_review"`` — every subtask is terminal
      (merged/done/failed/skipped); continue to the review/merge pipeline.

    Because a new round is only dispatched once the previous one has fully
    landed, the concurrency cap and the file-conflict rule hold per round.
    """
    subtasks = state.get("subtasks") or []

    in_flight = in_flight_subtask_ids(state)
    if in_flight:
        logger.info("route_after_collect: waiting for in-flight %s", in_flight)
        return "wait"

    waiting = [s for s in subtasks if s["status"] in ("pending", "changes_requested")]
    exhausted = [
        s["id"]
        for s in waiting
        if s["status"] == "changes_requested" and s["attempt"] >= s["max_attempts"]
    ]
    if exhausted:
        logger.warning(
            "route_after_collect: rework attempts exhausted for %s — escalating",
            exhausted,
        )
        return "escalate"

    if waiting:
        logger.info(
            "route_after_collect: %d subtask(s) waiting for a grinder — next round",
            len(waiting),
        )
        return "dispatch"

    unfinished = [s["id"] for s in subtasks if s["status"] not in TERMINAL_STATUSES]
    if unfinished:
        # Neither in flight, nor dispatchable, nor terminal (e.g. a status no
        # node produces).  Nothing in the graph can move these on.
        logger.error(
            "route_after_collect: subtasks %s are stuck in a non-terminal status",
            unfinished,
        )
        return "wait"

    # All subtasks are terminal — proceed to the review/merge pipeline.
    # "skipped" subtasks are ones retired by a redecompose round.
    if get_settings().auto_merge:
        return "skip_human_review"
    return "all_approved"


def route_after_validate_armory(state: FactoryState) -> list[Send] | str:
    """Route after the armory validation gate.

    On failure, re-dispatches each subtask that needs rework via ``Send("grind",...)``.
    On success, continues to ``human_review_code`` (or ``merge_check`` when
    ``auto_merge`` is enabled).
    """
    if state.get("graph_status") == "armory_validation_failed":
        failed = [
            s for s in state.get("subtasks", []) if s["status"] == "changes_requested"
        ]
        if failed:
            return [
                Send("grind", {**state, "_current_subtask_id": s["id"]}) for s in failed
            ]
        return "escalate"

    if get_settings().auto_merge:
        return "skip_human_review"
    return "all_approved"


def route_after_human_code_review(state: FactoryState) -> str:
    """Route after human reviews the final code."""
    if state.get("human_approval_response") == "approve":
        return "approved"
    return "rejected"


def route_after_escalation(state: FactoryState) -> str:
    """Route after escalation decision is made."""
    return state.get("escalation_decision") or "abandon"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(checkpointer):
    """
    Build and compile the factory StateGraph.

    Args:
        checkpointer: A LangGraph checkpointer instance (e.g. AsyncSqliteSaver
            in production, MemorySaver in tests).

    Returns:
        A compiled LangGraph graph ready for .invoke() / .ainvoke().
    """

    graph = StateGraph(FactoryState)

    # -----------------------------------------------------------------------
    # Nodes
    # -----------------------------------------------------------------------
    graph.add_node("task_intake", task_intake_node)
    graph.add_node("decompose", decompose_node)
    graph.add_node("assign_grinders", assign_grinders_node)
    graph.add_node("grind", grind_node)
    graph.add_node("pm_review", pm_review_node)
    graph.add_node("collect", collect_results_node)
    graph.add_node("pm_diagnose", pm_diagnose_node)
    graph.add_node("validate_armory", validate_armory_node)
    graph.add_node("human_review_code", human_review_code_node)
    graph.add_node("merge_check", merge_check_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("escalate", escalate_node)

    # -----------------------------------------------------------------------
    # Edges
    # -----------------------------------------------------------------------
    graph.add_edge(START, "task_intake")
    graph.add_conditional_edges(
        "task_intake",
        route_after_intake,
        {"decompose": "decompose", "skip_decompose": "assign_grinders", "abort": END},
    )
    # Human plan review is disabled — dispatch grinders immediately after decompose.
    graph.add_edge("decompose", "assign_grinders")

    # assign_grinders has no outgoing edge: the node fans out itself, returning
    # a Command that marks the dispatched subtasks in active_grinders and Sends
    # each of them to "grind".

    graph.add_conditional_edges(
        "grind",
        route_after_grinding,
        {
            "escalate": "escalate",
        },
    )

    # After PM diagnosis, re-grind the same subtask with the revised description.
    # If PM returned empty, the subtask is still "failed" and the group escalates.
    graph.add_conditional_edges(
        "pm_diagnose",
        route_after_pm_diagnose,
        {"escalate": "escalate"},
    )

    # Fan-in: every pm_review branch lands at the single "collect" join, which
    # runs once per superstep on the merged state.  The routing decision is
    # made there, not per branch — a per-branch router only sees its own
    # subtask's result, never its siblings'.
    graph.add_edge("pm_review", "collect")
    graph.add_conditional_edges(
        "collect",
        route_after_collect,
        {
            "wait": END,
            "dispatch": "assign_grinders",
            "escalate": "escalate",
            "all_approved": "validate_armory",
            "skip_human_review": "validate_armory",
        },
    )

    graph.add_conditional_edges(
        "validate_armory",
        route_after_validate_armory,
        {
            "all_approved": "human_review_code",
            "skip_human_review": "merge_check",
            "escalate": "escalate",
        },
    )

    graph.add_conditional_edges(
        "human_review_code",
        route_after_human_code_review,
        {"approved": "merge_check", "rejected": "pm_review"},
    )

    graph.add_edge("merge_check", "finalize")
    graph.add_edge("finalize", END)

    graph.add_conditional_edges(
        "escalate",
        route_after_escalation,
        {
            "retry": "assign_grinders",
            "redecompose": "decompose",
            "abandon": END,
        },
    )

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review_code"],
    )
