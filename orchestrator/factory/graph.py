"""LangGraph state machine definition for the factory orchestrator."""

import logging

from langgraph.graph import END, START, StateGraph

from .nodes import (
    assign_grinders_node,
    collect_results_node,
    decompose_node,
    escalate_node,
    finalize_node,
    grind_node,
    human_review_code_node,
    human_review_plan_node,
    merge_check_node,
    pm_review_node,
    route_grinders,
    task_intake_node,
)
from .state import FactoryState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_after_intake(state: FactoryState) -> str:
    """Route after task intake: skip decompose if decomposition_approved is set."""
    if state.get("decomposition_approved"):
        return "skip_decompose"
    return "decompose"


def route_after_plan_review(state: FactoryState) -> str:
    """Route after human reviews the decomposition plan."""
    resp = state.get("human_approval_response")
    if resp == "approve":
        return "approved"
    return "rejected"  # both "reject" and "modify" restart decomposition


def route_after_grinding(state: FactoryState) -> str:
    """Route after all grinder instances complete."""
    failed = [s for s in state["subtasks"] if s["status"] == "failed"]
    succeeded = [s for s in state["subtasks"] if s["status"] == "review"]
    if not failed:
        return "all_succeeded"
    if not succeeded:
        return "all_failed"
    return "some_failed"


def route_after_pm_review(state: FactoryState) -> str:
    """Route after PM reviews grinder-produced code."""
    from .config import get_settings
    needs_rework = [s for s in state["subtasks"] if s["status"] == "changes_requested"]
    exhausted = [s for s in needs_rework if s["attempt"] >= s["max_attempts"]]
    if exhausted:
        return "escalate"
    if needs_rework:
        return "changes_requested"
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
    return state.get("escalation_decision", "abandon")


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
    graph.add_node("human_review_plan", human_review_plan_node)
    graph.add_node("assign_grinders", assign_grinders_node)
    graph.add_node("grind", grind_node)
    graph.add_node("collect_results", collect_results_node)
    graph.add_node("pm_review", pm_review_node)
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
        {"decompose": "decompose", "skip_decompose": "assign_grinders"},
    )
    graph.add_edge("decompose", "human_review_plan")

    graph.add_conditional_edges(
        "human_review_plan",
        route_after_plan_review,
        {"approved": "assign_grinders", "rejected": "decompose"},
    )

    graph.add_conditional_edges("assign_grinders", route_grinders)
    graph.add_edge("grind", "collect_results")

    graph.add_conditional_edges(
        "collect_results",
        route_after_grinding,
        {
            "all_succeeded": "pm_review",
            "some_failed": "escalate",
            "all_failed": "escalate",
        },
    )

    graph.add_conditional_edges(
        "pm_review",
        route_after_pm_review,
        {
            "all_approved": "human_review_code",
            "skip_human_review": "merge_check",
            "changes_requested": "assign_grinders",
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
        interrupt_before=["human_review_plan", "human_review_code"],
    )
