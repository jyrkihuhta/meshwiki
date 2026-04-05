"""Tests for the factory LangGraph definition."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from factory.graph import build_graph, route_after_intake

EXPECTED_NODES = {
    "task_intake",
    "decompose",
    "human_review_plan",
    "assign_grinders",
    "grind",
    "collect_results",
    "pm_review",
    "human_review_code",
    "merge_check",
    "finalize",
    "escalate",
}


def test_graph_compiles() -> None:
    """build_graph(MemorySaver()) must not raise."""
    graph = build_graph(MemorySaver())
    assert graph is not None


def test_graph_has_expected_nodes() -> None:
    """The compiled graph must contain exactly the nodes listed in the PRD."""
    graph = build_graph(MemorySaver())
    # LangGraph exposes node names via the underlying StateGraph's nodes dict
    node_names = set(graph.nodes.keys())
    assert EXPECTED_NODES.issubset(
        node_names
    ), f"Missing nodes: {EXPECTED_NODES - node_names}"


def test_graph_default_checkpointer() -> None:
    """build_graph(MemorySaver()) must compile (used in tests; prod uses AsyncSqliteSaver)."""
    graph = build_graph(MemorySaver())
    assert graph is not None


def test_graph_interrupt_nodes() -> None:
    """The graph should declare interrupt_before for human review nodes."""
    graph = build_graph(MemorySaver())
    # LangGraph exposes interrupt_before_nodes on the compiled graph
    interrupt_nodes = set(graph.interrupt_before_nodes)
    assert "human_review_plan" in interrupt_nodes
    assert "human_review_code" in interrupt_nodes


# ---------------------------------------------------------------------------
# route_after_intake
# ---------------------------------------------------------------------------


def test_graph_route_after_intake_direct() -> None:
    """route_after_intake returns 'skip_decompose' when decomposition_approved is True."""
    state = {"decomposition_approved": True}
    assert route_after_intake(state) == "skip_decompose"


def test_graph_route_after_intake_normal() -> None:
    """route_after_intake returns 'decompose' when decomposition_approved is False/absent."""
    assert route_after_intake({}) == "decompose"
    assert route_after_intake({"decomposition_approved": False}) == "decompose"
    assert route_after_intake({"decomposition_approved": None}) == "decompose"
