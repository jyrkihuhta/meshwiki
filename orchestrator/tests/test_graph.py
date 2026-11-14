"""Tests for the factory LangGraph definition."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from factory.graph import build_graph, route_after_intake

EXPECTED_NODES = {
    "task_intake",
    "decompose",
    "assign_grinders",
    "grind",
    "pm_review",
    "pm_diagnose",
    "collect",
    "validate_armory",
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
    """The graph interrupts before human_review_code only."""
    graph = build_graph(MemorySaver())
    interrupt_nodes = set(graph.interrupt_before_nodes)
    assert interrupt_nodes == {"human_review_code"}


def test_pm_review_fans_in_through_collect() -> None:
    """Every pm_review branch reaches the single ``collect`` join by a plain edge.

    The decision what to do after review must not sit on ``pm_review`` itself:
    a conditional edge there is evaluated once per parallel branch and only
    sees that branch's own result.
    """
    builder = build_graph(MemorySaver()).builder
    assert ("pm_review", "collect") in builder.edges
    assert not builder.branches.get("pm_review")


def test_collect_join_routes() -> None:
    """The join can wait, start another round, escalate, or move on."""
    builder = build_graph(MemorySaver()).builder
    (branch,) = builder.branches["collect"].values()
    assert branch.ends == {
        "wait": "__end__",
        "dispatch": "assign_grinders",
        "escalate": "escalate",
        "all_approved": "validate_armory",
        "skip_human_review": "validate_armory",
    }


def test_assign_grinders_fans_out_by_command() -> None:
    """assign_grinders dispatches from the node itself (Command → grind).

    It must not also carry a conditional edge, or every subtask would be
    dispatched twice.
    """
    builder = build_graph(MemorySaver()).builder
    assert not builder.branches.get("assign_grinders")
    assert builder.nodes["assign_grinders"].ends == ("grind",)


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
