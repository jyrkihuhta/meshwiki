"""Integration smoke test for the full factory graph pipeline.

Exercises the happy path from assign_grinders through merge_check → finalize
with all external calls (MeshWiki API, GitHub API, Anthropic API) mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.graph import build_graph
from factory.state import FactoryState, SubTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtask(**kwargs) -> SubTask:
    """Return a minimal SubTask for testing."""
    defaults: dict = {
        "id": "task-0042-sub-abc123",
        "wiki_page": "Task_0042_Sub_01_add_search",
        "title": "Add search endpoint",
        "description": "Implement GET /search route.",
        "status": "pending",
        "assigned_grinder": None,
        "branch_name": None,
        "pr_url": "https://github.com/owner/repo/pull/99",
        "pr_number": 99,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": ["src/meshwiki/main.py"],
        "token_budget": 50000,
        "tokens_used": 0,
        "review_feedback": None,
    }
    defaults.update(kwargs)
    return SubTask(**defaults)


def _make_initial_state(subtask: SubTask) -> FactoryState:
    """Return a FactoryState that skips PM decomposition (pre-decomposed).

    Sets ``decomposition_approved=True`` so ``route_after_intake`` bypasses
    the decompose node and goes straight to ``assign_grinders``.
    """
    return FactoryState(
        thread_id="Task_0042_test",
        task_wiki_page="Task_0042_test",
        title="Test Task",
        requirements="Implement a test feature.",
        subtasks=[subtask],
        decomposition_approved=True,
        active_grinders={},
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        graph_status="intake",
        error=None,
        escalation_decision=None,
    )


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


def _mock_meshwiki_client(*, task_page_metadata: dict | None = None):
    """Return an AsyncMock MeshWikiClient that returns sensible defaults."""
    client = AsyncMock()
    page_data = {
        "name": "Task_0042_test",
        "content": "# Test Task\n\nRequirements here.",
        "metadata": task_page_metadata or {"title": "Test Task", "status": "planned"},
    }
    client.get_page = AsyncMock(return_value=page_data)
    client.create_page = AsyncMock(return_value=page_data)
    client.transition_task = AsyncMock(return_value=page_data)
    client.list_tasks = AsyncMock(return_value=[])
    return client


def _mock_github_client(*, pr_merged: bool = True):
    """Return an AsyncMock GitHubClient that reports a merged PR."""
    client = AsyncMock()
    pr_state = "closed" if pr_merged else "open"
    client.get_pr = AsyncMock(
        return_value={"merged": pr_merged, "state": pr_state, "number": 99}
    )
    client.get_pr_diff = AsyncMock(
        return_value="diff --git a/main.py b/main.py\n+# added"
    )
    client.approve_pr = AsyncMock(return_value={})
    client.create_pr_comment = AsyncMock(return_value={})
    return client


def _make_pm_review_result(decision: str = "approved") -> dict:
    """Return a mock PM review result dict."""
    feedback = "LGTM!" if decision == "approved" else "Fix tests."
    return {"decision": decision, "feedback": feedback}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_happy_path_reaches_done() -> None:
    """Full pipeline: grind → collect → pm_review → merge_check → finalize.

    Feeds a pre-decomposed FactoryState (decomposition_approved=True) so the
    graph bypasses decompose and human_review_plan entirely.
    Mocks all external I/O so no network calls are made.
    Asserts graph_status == "completed" at the end.
    """
    subtask = _make_subtask(status="pending")
    initial_state = _make_initial_state(subtask)

    meshwiki = _mock_meshwiki_client()
    github = _mock_github_client(pr_merged=True)

    # The grinder updates the subtask to "review" status with a PR number set
    grinder_result = _make_subtask(
        status="review",
        pr_number=99,
        pr_url="https://github.com/owner/repo/pull/99",
        tokens_used=1234,
    )

    graph = build_graph()
    config = {"configurable": {"thread_id": "Task_0042_test"}}

    with (
        patch(
            "factory.nodes.task_intake.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.grind_subtask",
            new=AsyncMock(return_value=grinder_result),
        ),
        patch(
            "factory.nodes.pm_review.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.pm_review.GitHubClient",
            return_value=github,
        ),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(return_value=_make_pm_review_result("approved")),
        ),
        patch(
            "factory.nodes.merge_check.GitHubClient",
            return_value=github,
        ),
        patch(
            "factory.nodes.finalize.MeshWikiClient",
            return_value=meshwiki,
        ),
    ):
        # First invoke: runs until human_review_code interrupt
        await graph.ainvoke(initial_state, config=config)

        # Inject human approval into the checkpoint, then resume
        await graph.aupdate_state(
            config, {"human_approval_response": "approve", "human_feedback": None}
        )
        final_state = await graph.ainvoke(None, config=config)

    assert final_state["graph_status"] == "completed"


@pytest.mark.asyncio
async def test_pipeline_grind_failure_triggers_escalate_then_abandon() -> None:
    """When grinder fails and retries are exhausted, graph ends via escalate→abandon."""
    # Subtask already at max attempts so it won't retry
    subtask = _make_subtask(status="pending", attempt=2, max_attempts=3)
    initial_state = _make_initial_state(subtask)

    meshwiki = _mock_meshwiki_client()

    # Grinder fails: returns subtask with status="failed"
    failed_result = _make_subtask(
        status="failed",
        attempt=2,
        max_attempts=3,
        error_log=["Compilation error: undefined variable"],
    )

    graph = build_graph()
    config = {"configurable": {"thread_id": "Task_0042_test_fail"}}

    with (
        patch(
            "factory.nodes.task_intake.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.grind_subtask",
            new=AsyncMock(return_value=failed_result),
        ),
        patch(
            "factory.nodes.escalate.MeshWikiClient",
            return_value=meshwiki,
        ),
    ):
        final_state = await graph.ainvoke(initial_state, config=config)

    # With all retries exhausted, escalate decides "abandon" → END
    assert final_state["graph_status"] == "escalated"
    assert final_state["escalation_decision"] == "abandon"


@pytest.mark.asyncio
async def test_pipeline_pm_review_requests_changes_reruns_grinder() -> None:
    """When PM requests changes, subtask goes back through assign_grinders → grind."""
    subtask = _make_subtask(status="pending")
    initial_state = _make_initial_state(subtask)

    meshwiki = _mock_meshwiki_client()
    github = _mock_github_client(pr_merged=True)

    # First grind: subtask in "review" status
    review_result = _make_subtask(
        status="review",
        pr_number=99,
        pr_url="https://github.com/owner/repo/pull/99",
    )
    # Second grind (after PM requests changes): subtask moves back to "review"
    regrind_result = _make_subtask(
        status="review",
        pr_number=100,
        pr_url="https://github.com/owner/repo/pull/100",
        attempt=1,
    )

    # PM: first call requests changes, second call approves
    pm_review_side_effects = [
        _make_pm_review_result("changes_requested"),
        _make_pm_review_result("approved"),
    ]

    graph = build_graph()
    config = {"configurable": {"thread_id": "Task_0042_test_rework"}}

    with (
        patch(
            "factory.nodes.task_intake.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.grind.grind_subtask",
            new=AsyncMock(side_effect=[review_result, regrind_result]),
        ),
        patch(
            "factory.nodes.pm_review.MeshWikiClient",
            return_value=meshwiki,
        ),
        patch(
            "factory.nodes.pm_review.GitHubClient",
            return_value=github,
        ),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(side_effect=pm_review_side_effects),
        ),
        patch(
            "factory.nodes.merge_check.GitHubClient",
            return_value=github,
        ),
        patch(
            "factory.nodes.finalize.MeshWikiClient",
            return_value=meshwiki,
        ),
    ):
        # First run: pauses at human_review_code after second PM review approves
        await graph.ainvoke(initial_state, config=config)

        # Inject approval into checkpoint, then resume
        await graph.aupdate_state(
            config, {"human_approval_response": "approve", "human_feedback": None}
        )
        final_state = await graph.ainvoke(None, config=config)

    assert final_state["graph_status"] == "completed"


@pytest.mark.asyncio
async def test_finalize_calls_transition_to_done() -> None:
    """finalize_node transitions the parent task to 'done' via MeshWikiClient."""
    subtask = _make_subtask(status="pending")
    initial_state = _make_initial_state(subtask)

    meshwiki = _mock_meshwiki_client()
    github = _mock_github_client(pr_merged=True)

    grinder_result = _make_subtask(
        status="review",
        pr_number=99,
        pr_url="https://github.com/owner/repo/pull/99",
    )

    graph = build_graph()
    config = {"configurable": {"thread_id": "Task_0042_test_finalize"}}

    with (
        patch("factory.nodes.task_intake.MeshWikiClient", return_value=meshwiki),
        patch("factory.nodes.grind.MeshWikiClient", return_value=meshwiki),
        patch(
            "factory.nodes.grind.grind_subtask",
            new=AsyncMock(return_value=grinder_result),
        ),
        patch("factory.nodes.pm_review.MeshWikiClient", return_value=meshwiki),
        patch("factory.nodes.pm_review.GitHubClient", return_value=github),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(return_value=_make_pm_review_result("approved")),
        ),
        patch("factory.nodes.merge_check.GitHubClient", return_value=github),
        patch("factory.nodes.finalize.MeshWikiClient", return_value=meshwiki),
    ):
        await graph.ainvoke(initial_state, config=config)
        await graph.aupdate_state(
            config, {"human_approval_response": "approve", "human_feedback": None}
        )
        await graph.ainvoke(None, config=config)

    # Verify transition_task was called with "done"
    calls = meshwiki.transition_task.call_args_list
    done_calls = [c for c in calls if c.args and c.args[1] == "done"]
    assert done_calls, "finalize_node must call transition_task(..., 'done')"
