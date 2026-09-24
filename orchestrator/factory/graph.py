"""LangGraph state machine definition for the factory orchestrator."""

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import get_settings
from .nodes import (
    assign_grinders_node,
    decompose_node,
    escalate_node,
    finalize_node,
    grind_node,
    human_review_code_node,
    merge_check_node,
    pm_diagnose_node,
    pm_review_node,
    route_grinders,
    task_intake_node,
    validate_armory_node,
)
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
    own ``pm_review`` instance via ``Send()``.  If the just-finished subtask
    failed, the whole group is escalated.  If there are still pending subtasks
    waiting on a file-conflict serialization slot, loop back to
    ``assign_grinders``.

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

    if subtask["status"] == "failed":
        # First failure: let PM diagnose and rewrite the task before retrying.
        # Subsequent failures (attempt > 0) escalate immediately.
        if subtask.get("attempt", 0) == 0:
            return [Send("pm_diagnose", {**state, "_current_subtask_id": subtask_id})]
        return "escalate"

    # Subtask completed — fan out to its own pm_review immediately.
    return [Send("pm_review", {**state, "_current_subtask_id": subtask_id})]


def route_after_pm_review(state: FactoryState) -> list[Send] | str:
    """Route after PM reviews a single grinder-produced subtask.

    For rework: re-dispatches only the subtask that needs changes via
    ``Send("grind", ...)``, keeping other branches running independently.

    For approval: checks whether ALL subtasks are now in a terminal state
    (merged/done/failed).  If so, routes to ``finalize``; otherwise the branch
    simply ends — other pm_review branches are still running and one of them
    will eventually trigger ``finalize``.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state["subtasks"] if s["id"] == subtask_id),
        None,
    )

    if subtask is None:
        logger.error(
            "route_after_pm_review: _current_subtask_id %r not found — escalating",
            subtask_id,
        )
        return "escalate"

    if subtask["status"] == "changes_requested":
        if subtask["attempt"] >= subtask["max_attempts"]:
            return "escalate"
        # Re-grind just this subtask.
        return [Send("grind", {**state, "_current_subtask_id": subtask_id})]

    # Subtask approved (merged).  Check if all subtasks are now terminal.
    # "skipped" subtasks are ones retired by a redecompose round — treat as terminal.
    terminal_statuses = {"merged", "done", "failed", "skipped"}
    all_done = all(s["status"] in terminal_statuses for s in state["subtasks"])

    if not all_done:
        # Other pm_review branches are still running; this branch ends here.
        return END

    # All subtasks are terminal — proceed to the review/merge pipeline.
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
                Send("grind", {**state, "_current_subtask_id": s["id"]})
                for s in failed
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

    graph.add_conditional_edges("assign_grinders", route_grinders)
    graph.add_conditional_edges(
        "grind",
        route_after_grinding,
        {
            "escalate": "escalate",
        },
    )

    # After PM diagnosis, re-grind the same subtask with the revised description.
    # If PM returned empty, _current_subtask_id is still set so route_after_grinding
    # will escalate on the next failure (attempt > 0).
    graph.add_conditional_edges(
        "pm_diagnose",
        lambda state: (
            [Send("grind", {**state, "_current_subtask_id": state.get("_current_subtask_id")})]
            if next(
                (s for s in state["subtasks"] if s["id"] == state.get("_current_subtask_id")),
                {},
            ).get("status") == "pending"
            else "escalate"
        ),
        {"escalate": "escalate"},
    )

    graph.add_conditional_edges(
        "pm_review",
        route_after_pm_review,
        {
            "all_approved": "validate_armory",
            "skip_human_review": "validate_armory",
            "escalate": "escalate",
            END: END,
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
