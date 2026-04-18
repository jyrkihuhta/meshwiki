"""Tests for the LangGraph node functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from factory.agents.pm_agent import _build_subtask
from factory.nodes.collect import collect_results_node
from factory.nodes.decompose import _build_subtask_page, decompose_node
from factory.nodes.escalate import escalate_node
from factory.nodes.finalize import finalize_node
from factory.nodes.merge_check import merge_check_node
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


def _mock_client_for_cm(mock_client: AsyncMock) -> AsyncMock:
    """Configure an AsyncMock to work as a context manager returning itself.

    When MeshWikiClient/GitHubClient is patched with return_value=mock_client,
    the code calls `async with mock_client as cm`.  By default AsyncMock's
    __aenter__ returns a new AsyncMock (not mock_client), so method calls go
    to the wrong object.  This helper fixes __aenter__ / __aexit__ so that
    `cm is mock_client`.
    """
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


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
        "metadata": {
            "title": "Test Task Title",
            "status": "planned",
            "assignee": "factory",
        },
    }

    mock_client = _mock_client_for_cm(AsyncMock())
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

    mock_client = _mock_client_for_cm(AsyncMock())
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

    mock_meshwiki = _mock_client_for_cm(AsyncMock())
    mock_meshwiki.create_page = AsyncMock(return_value={})
    mock_meshwiki.transition_task = AsyncMock(return_value={})

    with (
        patch("factory.nodes.decompose.MeshWikiClient", return_value=mock_meshwiki),
        patch(
            "factory.nodes.decompose.decompose_with_pm",
            new=AsyncMock(
                return_value={"subtasks": [mock_subtask], "incremental_cost_usd": 0.0}
            ),
        ),
    ):
        result = await decompose_node(state)

    assert result["graph_status"] == "dispatching"
    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["title"] == "Build something"

    # Should have created the wiki page
    mock_meshwiki.create_page.assert_awaited_once()
    create_args = mock_meshwiki.create_page.call_args
    assert create_args[0][0] == "Task_0042_Sub_01_build"

    # Should have transitioned the parent task to decomposed
    assert mock_meshwiki.transition_task.await_count == 1
    calls = [c[0] for c in mock_meshwiki.transition_task.call_args_list]
    assert ("Task_0042_test", "decomposed") in calls


@pytest.mark.asyncio
async def test_decompose_node_no_subtasks() -> None:
    """decompose_node handles empty subtask list from PM agent."""
    state = _make_state()

    mock_meshwiki = _mock_client_for_cm(AsyncMock())
    mock_meshwiki.create_page = AsyncMock(return_value={})
    mock_meshwiki.transition_task = AsyncMock(return_value={})

    with (
        patch("factory.nodes.decompose.MeshWikiClient", return_value=mock_meshwiki),
        patch(
            "factory.nodes.decompose.decompose_with_pm",
            new=AsyncMock(return_value={"subtasks": [], "incremental_cost_usd": 0.0}),
        ),
    ):
        result = await decompose_node(state)

    assert result["graph_status"] == "dispatching"
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

    mock_meshwiki = _mock_client_for_cm(AsyncMock())
    mock_meshwiki.get_page = AsyncMock(return_value={"content": "criteria"})

    mock_github = _mock_client_for_cm(AsyncMock())
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

    mock_meshwiki = _mock_client_for_cm(AsyncMock())
    mock_meshwiki.get_page = AsyncMock(return_value=None)

    mock_github = _mock_client_for_cm(AsyncMock())

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

    mock_meshwiki = _mock_client_for_cm(AsyncMock())
    mock_github = _mock_client_for_cm(AsyncMock())

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


# ---------------------------------------------------------------------------
# task_intake_node — direct grind (skip_decomposition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_intake_direct_grind() -> None:
    """task_intake_node returns a single subtask and decomposition_approved=True when skip_decomposition is set."""
    state = _make_state()

    mock_page = {
        "name": "Task_0042_test",
        "content": "## Requirements\nDo the thing directly.",
        "metadata": {
            "title": "Direct Grind Task",
            "status": "planned",
            "skip_decomposition": "true",
            "assignee": "factory",
            "expected_files": ["src/meshwiki/main.py"],
            "token_budget": "30000",
        },
    }

    mock_client = _mock_client_for_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result["decomposition_approved"] is True
    assert result["graph_status"] == "grinding"
    assert len(result["subtasks"]) == 1

    subtask = result["subtasks"][0]
    assert subtask["id"] == "Task_0042_test"
    assert subtask["wiki_page"] == "Task_0042_test"
    assert subtask["title"] == "Direct Grind Task"
    assert subtask["description"] == "## Requirements\nDo the thing directly."
    assert subtask["status"] == "pending"
    assert subtask["attempt"] == 0
    assert subtask["max_attempts"] == 3
    assert subtask["error_log"] == []
    assert subtask["files_touched"] == ["src/meshwiki/main.py"]
    assert subtask["token_budget"] == 30000
    assert subtask["tokens_used"] == 0
    assert subtask["assigned_grinder"] is None
    assert subtask["branch_name"] is None
    assert subtask["pr_url"] is None
    assert subtask["pr_number"] is None
    assert subtask["review_feedback"] is None


@pytest.mark.asyncio
async def test_task_intake_direct_grind_boolean_flag() -> None:
    """task_intake_node handles skip_decomposition as a boolean True (not just string)."""
    state = _make_state()

    mock_page = {
        "name": "Task_0042_test",
        "content": "Do it.",
        "metadata": {
            "title": "Bool Flag Task",
            "status": "planned",
            "skip_decomposition": True,
            "assignee": "factory",
        },
    }

    mock_client = _mock_client_for_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result["decomposition_approved"] is True
    assert result["graph_status"] == "grinding"
    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["token_budget"] == 50000  # default
    assert result["subtasks"][0]["files_touched"] == []  # default


# ---------------------------------------------------------------------------
# collect_results_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_results_node() -> None:
    """collect_results_node tallies completed and failed subtask IDs correctly."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01", title="Sub 01", status="review"
    )
    merged_sub = _make_subtask(
        wiki_page="Task_0042_Sub_02", title="Sub 02", status="merged"
    )
    failed_sub = _make_subtask(
        wiki_page="Task_0042_Sub_03", title="Sub 03", status="failed"
    )
    pending_sub = _make_subtask(
        wiki_page="Task_0042_Sub_04", title="Sub 04", status="pending"
    )

    state = _make_state(subtasks=[review_sub, merged_sub, failed_sub, pending_sub])

    result = await collect_results_node(state)

    assert set(result["completed_subtask_ids"]) == {review_sub["id"], merged_sub["id"]}
    assert result["failed_subtask_ids"] == [failed_sub["id"]]
    assert result["graph_status"] == "reviewing"


@pytest.mark.asyncio
async def test_collect_results_node_all_succeeded() -> None:
    """collect_results_node produces empty failed list when all subtasks passed."""
    sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", title="Sub 01", status="review")
    sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", title="Sub 02", status="merged")
    state = _make_state(subtasks=[sub1, sub2])

    result = await collect_results_node(state)

    assert len(result["completed_subtask_ids"]) == 2
    assert result["failed_subtask_ids"] == []
    assert result["graph_status"] == "reviewing"


# ---------------------------------------------------------------------------
# finalize_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_node() -> None:
    """finalize_node calls transition_task with 'done' and returns completed status."""
    state = _make_state(cost_usd=0.0042)

    mock_client_instance = _mock_client_for_cm(AsyncMock())
    mock_client_instance.transition_task = AsyncMock(return_value={})
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with patch("factory.nodes.finalize.MeshWikiClient", mock_client_cls):
        result = await finalize_node(state)

    assert result["graph_status"] == "completed"
    mock_client_instance.transition_task.assert_awaited_once()
    call_args = mock_client_instance.transition_task.call_args
    assert call_args[0][0] == "Task_0042_test"
    assert call_args[0][1] == "done"
    assert "cost_usd" in call_args[1]["extra_fields"]


@pytest.mark.asyncio
async def test_finalize_node_handles_client_error() -> None:
    """finalize_node logs and swallows MeshWiki client errors, still returns completed."""
    state = _make_state()

    mock_client_instance = _mock_client_for_cm(AsyncMock())
    mock_client_instance.transition_task = AsyncMock(
        side_effect=RuntimeError("network error")
    )
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with patch("factory.nodes.finalize.MeshWikiClient", mock_client_cls):
        result = await finalize_node(state)

    assert result["graph_status"] == "completed"


# ---------------------------------------------------------------------------
# escalate_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_retriable() -> None:
    """escalate_node sets decision='retry' and increments attempt when retries remain."""
    failed_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="failed",
        attempt=0,
        max_attempts=3,
    )
    state = _make_state(
        subtasks=[failed_sub],
        failed_subtask_ids=[failed_sub["id"]],
    )

    mock_client_instance = AsyncMock()
    mock_client_instance.get_page = AsyncMock(return_value={"content": "# Task"})
    mock_client_instance.create_page = AsyncMock(return_value={})
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with patch("factory.nodes.escalate.MeshWikiClient", mock_client_cls):
        result = await escalate_node(state)

    assert result["escalation_decision"] == "retry"
    assert result["graph_status"] == "escalated"
    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["attempt"] == 1
    assert result["subtasks"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_escalate_exhausted() -> None:
    """escalate_node sets decision='abandon' when subtask has used all attempts."""
    failed_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="failed",
        attempt=2,
        max_attempts=3,
    )
    state = _make_state(
        subtasks=[failed_sub],
        failed_subtask_ids=[failed_sub["id"]],
    )

    mock_client_instance = AsyncMock()
    mock_client_instance.get_page = AsyncMock(return_value={"content": "# Task"})
    mock_client_instance.create_page = AsyncMock(return_value={})
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with patch("factory.nodes.escalate.MeshWikiClient", mock_client_cls):
        result = await escalate_node(state)

    assert result["escalation_decision"] == "abandon"
    assert result["graph_status"] == "escalated"
    # Status should remain "failed" when not retriable
    assert result["subtasks"][0]["attempt"] == 2
    assert result["subtasks"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# merge_check_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_check_node_merged_pr() -> None:
    """merge_check_node sets subtask status to 'merged' when PR is merged."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=10,
    )
    state = _make_state(subtasks=[review_sub])

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(
        return_value={"number": 10, "state": "closed", "merged": True}
    )

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    assert result["subtasks"][0]["status"] == "merged"
    mock_github.get_pr.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_merge_check_node_closed_not_merged() -> None:
    """merge_check_node sets subtask status to 'failed' when PR is closed but not merged."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=11,
    )
    state = _make_state(subtasks=[review_sub])

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(
        return_value={"number": 11, "state": "closed", "merged": False}
    )

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    assert result["subtasks"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_merge_check_node_still_open() -> None:
    """merge_check_node leaves subtask status unchanged when PR is still open."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=12,
    )
    state = _make_state(subtasks=[review_sub])

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(
        return_value={"number": 12, "state": "open", "merged": False}
    )

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    assert result["subtasks"][0]["status"] == "review"


@pytest.mark.asyncio
async def test_merge_check_node_pr_number_from_url() -> None:
    """merge_check_node extracts pr_number from pr_url when pr_number is None."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
    )
    review_sub["pr_number"] = None
    review_sub["pr_url"] = "https://github.com/owner/repo/pull/42"
    state = _make_state(subtasks=[review_sub])

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(
        return_value={"number": 42, "state": "closed", "merged": True}
    )

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    mock_github.get_pr.assert_awaited_once_with(42)
    assert result["subtasks"][0]["status"] == "merged"


@pytest.mark.asyncio
async def test_merge_check_node_no_pr_skipped() -> None:
    """merge_check_node skips subtasks with no pr_number and no pr_url."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
    )
    review_sub["pr_number"] = None
    review_sub["pr_url"] = None
    state = _make_state(subtasks=[review_sub])

    mock_github = AsyncMock()

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    mock_github.get_pr.assert_not_awaited()
    assert result["subtasks"][0]["status"] == "review"


@pytest.mark.asyncio
async def test_merge_check_node_skips_non_review_subtasks() -> None:
    """merge_check_node does not call GitHub API for subtasks not in 'review' status."""
    pending_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="pending",
        pr_number=5,
    )
    state = _make_state(subtasks=[pending_sub])

    mock_github = AsyncMock()

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    mock_github.get_pr.assert_not_awaited()
    assert result["subtasks"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_merge_check_node_api_error_continues() -> None:
    """merge_check_node logs and continues when GitHub API returns an error."""
    review_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=13,
    )
    state = _make_state(subtasks=[review_sub])

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "https://api.github.com/repos/o/r/pulls/13"),
            response=httpx.Response(404),
        )
    )

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    # Status should remain unchanged when the API call fails
    assert result["subtasks"][0]["status"] == "review"


@pytest.mark.asyncio
async def test_merge_check_node_multiple_subtasks() -> None:
    """merge_check_node handles mixed statuses across multiple subtasks."""
    merged_sub = _make_subtask(
        wiki_page="Task_0042_Sub_01",
        title="Sub 01",
        status="review",
        pr_number=20,
    )
    open_sub = _make_subtask(
        wiki_page="Task_0042_Sub_02",
        title="Sub 02",
        status="review",
        pr_number=21,
    )
    pending_sub = _make_subtask(
        wiki_page="Task_0042_Sub_03",
        title="Sub 03",
        status="pending",
    )
    state = _make_state(subtasks=[merged_sub, open_sub, pending_sub])

    async def _fake_get_pr(pr_number: int) -> dict:
        if pr_number == 20:
            return {"number": 20, "state": "closed", "merged": True}
        return {"number": 21, "state": "open", "merged": False}

    mock_github = _mock_client_for_cm(AsyncMock())
    mock_github.get_pr = AsyncMock(side_effect=_fake_get_pr)

    with patch("factory.nodes.merge_check.GitHubClient", return_value=mock_github):
        result = await merge_check_node(state)

    statuses = {s["id"]: s["status"] for s in result["subtasks"]}
    assert statuses[merged_sub["id"]] == "merged"
    assert statuses[open_sub["id"]] == "review"
    assert statuses[pending_sub["id"]] == "pending"
