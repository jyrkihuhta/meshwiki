"""Tests for the LangGraph node functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.agents.pm_agent import _build_subtask
from factory.nodes.decompose import _build_subtask_page, decompose_node
from factory.nodes.pm_review import pm_review_node
from factory.nodes.task_intake import task_intake_node
from factory.state import FactoryState, SubTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> FactoryState:
    """Return a minimal FactoryState for testing."""
    defaults: dict = {
        "thread_id": "task-0042",
        "task_wiki_page": "Task_0042_test",
        "title": "",
        "requirements": "",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": {},
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "graph_status": "intake",
        "error": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


def _make_subtask(**kwargs) -> SubTask:
    """Return a minimal SubTask for testing."""
    tool_input = {
        "page_name": kwargs.pop("wiki_page", "Task_0042_Sub_01"),
        "title": kwargs.pop("title", "Test subtask"),
        "description": kwargs.pop("description", "Do the thing."),
        "acceptance_criteria": kwargs.pop("acceptance_criteria", ["It works"]),
        "parent_task": kwargs.pop("parent_task", "Task_0042_test"),
        "estimation": kwargs.pop("estimation", "s"),
        "expected_files": kwargs.pop("expected_files", []),
    }
    subtask = _build_subtask(tool_input, parent_thread_id="task-0042")
    for k, v in kwargs.items():
        subtask[k] = v  # type: ignore[literal-required]
    return subtask


# ---------------------------------------------------------------------------
# task_intake_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_intake_node() -> None:
    """task_intake_node returns title, requirements, and graph_status from MeshWiki."""
    state = _make_state()

    mock_page = {
        "name": "Task_0042_test",
        "content": "## Requirements\nBuild the feature.",
        "metadata": {"title": "Test Task Title", "status": "planned"},
    }

    mock_client = AsyncMock()
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result["title"] == "Test Task Title"
    assert result["requirements"] == "## Requirements\nBuild the feature."
    assert result["graph_status"] == "decomposing"
    mock_client.get_page.assert_awaited_once_with("Task_0042_test")


@pytest.mark.asyncio
async def test_task_intake_node_page_not_found() -> None:
    """task_intake_node falls back gracefully when the page is not found."""
    state = _make_state()

    mock_client = AsyncMock()
    mock_client.get_page = AsyncMock(return_value=None)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result["graph_status"] == "decomposing"
    assert result["title"] == "Task_0042_test"  # falls back to page name
    assert result["requirements"] == ""


# ---------------------------------------------------------------------------
# decompose_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_node() -> None:
    """decompose_node calls PM agent, writes subtask pages, and transitions tasks."""
    state = _make_state(
        title="Test Task",
        requirements="Build something.",
        graph_status="decomposing",
    )

    mock_subtask = _make_subtask(
        wiki_page="Task_0042_Sub_01_build",
        title="Build something",
    )

    mock_meshwiki = AsyncMock()
    mock_meshwiki.create_page = AsyncMock(return_value={})
    mock_meshwiki.transition_task = AsyncMock(return_value={})

    with (
        patch("factory.nodes.decompose.MeshWikiClient", return_value=mock_meshwiki),
        patch(
            "factory.nodes.decompose.decompose_with_pm",
            new=AsyncMock(return_value=[mock_subtask]),
        ),
    ):
        result = await decompose_node(state)

    assert result["graph_status"] == "awaiting_approval"
    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["title"] == "Build something"

    # Should have created the wiki page
    mock_meshwiki.create_page.assert_awaited_once()
    create_args = mock_meshwiki.create_page.call_args
    assert create_args[0][0] == "Task_0042_Sub_01_build"

    # Should have transitioned subtask to planned and parent to decomposed
    assert mock_meshwiki.transition_task.await_count == 2
    calls = [c[0] for c in mock_meshwiki.transition_task.call_args_list]
    assert ("Task_0042_Sub_01_build", "planned") in calls
    assert ("Task_0042_test", "decomposed") in calls


@pytest.mark.asyncio
async def test_decompose_node_no_subtasks() -> None:
    """decompose_node handles empty subtask list from PM agent."""
    state = _make_state()

    mock_meshwiki = AsyncMock()
    mock_meshwiki.create_page = AsyncMock(return_value={})
    mock_meshwiki.transition_task = AsyncMock(return_value={})

    with (
        patch("factory.nodes.decompose.MeshWikiClient", return_value=mock_meshwiki),
        patch(
            "factory.nodes.decompose.decompose_with_pm",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await decompose_node(state)

    assert result["graph_status"] == "awaiting_approval"
    assert result["subtasks"] == []
    # Only the parent task transition should have been called
    mock_meshwiki.create_page.assert_not_awaited()
    mock_meshwiki.transition_task.assert_awaited_once_with(
        "Task_0042_test", "decomposed"
    )


# ---------------------------------------------------------------------------
# pm_review_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_review_node_approved() -> None:
    """pm_review_node sets subtask status to 'merged' when PM approves."""
    review_subtask = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=10,
    )
    state = _make_state(subtasks=[review_subtask])

    mock_meshwiki = AsyncMock()
    mock_meshwiki.get_page = AsyncMock(return_value={"content": "criteria"})

    mock_github = AsyncMock()
    mock_github.get_pr_diff = AsyncMock(return_value="diff content")

    with (
        patch("factory.nodes.pm_review.MeshWikiClient", return_value=mock_meshwiki),
        patch("factory.nodes.pm_review.GitHubClient", return_value=mock_github),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(return_value={"decision": "approved", "feedback": "LGTM"}),
        ),
    ):
        result = await pm_review_node(state)

    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["status"] == "merged"
    assert result["subtasks"][0]["review_feedback"] == "LGTM"


@pytest.mark.asyncio
async def test_pm_review_node_changes_requested() -> None:
    """pm_review_node sets status to 'changes_requested' and stores feedback."""
    review_subtask = _make_subtask(
        wiki_page="Task_0042_Sub_02",
        title="Sub 02",
        status="review",
        pr_number=11,
    )
    state = _make_state(subtasks=[review_subtask])

    mock_meshwiki = AsyncMock()
    mock_meshwiki.get_page = AsyncMock(return_value=None)

    mock_github = AsyncMock()

    with (
        patch("factory.nodes.pm_review.MeshWikiClient", return_value=mock_meshwiki),
        patch("factory.nodes.pm_review.GitHubClient", return_value=mock_github),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(
                return_value={
                    "decision": "changes_requested",
                    "feedback": "Add more tests.",
                }
            ),
        ),
    ):
        result = await pm_review_node(state)

    assert result["subtasks"][0]["status"] == "changes_requested"
    assert result["subtasks"][0]["review_feedback"] == "Add more tests."


@pytest.mark.asyncio
async def test_pm_review_node_skips_non_review_subtasks() -> None:
    """pm_review_node leaves subtasks not in 'review' status unchanged."""
    pending_subtask = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Pending",
        status="pending",
    )
    state = _make_state(subtasks=[pending_subtask])

    mock_meshwiki = AsyncMock()
    mock_github = AsyncMock()

    with (
        patch("factory.nodes.pm_review.MeshWikiClient", return_value=mock_meshwiki),
        patch("factory.nodes.pm_review.GitHubClient", return_value=mock_github),
        patch(
            "factory.nodes.pm_review.review_with_pm",
            new=AsyncMock(return_value={"decision": "approved", "feedback": None}),
        ) as mock_review,
    ):
        result = await pm_review_node(state)

    # review_with_pm should never be called for non-review subtasks
    mock_review.assert_not_awaited()
    assert result["subtasks"][0]["status"] == "pending"


# ---------------------------------------------------------------------------
# _build_subtask_page
# ---------------------------------------------------------------------------


def test_build_subtask_page_contains_expected_sections() -> None:
    """_build_subtask_page returns valid Markdown with all required sections."""
    subtask = _make_subtask(
        wiki_page="Task_0042_Sub_01_build",
        title="Build something",
        description="Detailed description here.",
        expected_files=["src/meshwiki/main.py", "src/tests/test_main.py"],
    )

    page = _build_subtask_page(subtask, parent_task="Task_0042_test")

    assert "---" in page
    assert 'title: "Build something"' in page
    assert "type: task" in page
    assert "status: planned" in page
    assert 'parent_task: "Task_0042_test"' in page
    assert "tags:" in page
    assert "  - factory" in page
    assert "# Build something" in page
    assert "## Description" in page
    assert "Detailed description here." in page
    assert "## Acceptance Criteria" in page
    assert "## Files Expected" in page
    assert "src/meshwiki/main.py" in page
    assert "## Agent Log" in page
    assert "<!-- Agents append progress notes below this line -->" in page
