"""Tests for FactoryState reducers — especially fan-in merge behavior.

When two parallel grinder branches write state concurrently, LangGraph fans in
by applying each branch's updates through the field reducers.  These tests
verify that the union reducers on completed_subtask_ids / failed_subtask_ids
preserve both branches' writes and that the subtasks merge-by-ID reducer keeps
the latest status per subtask.
"""

from __future__ import annotations

from factory.state import (
    FactoryState,
    SubTask,
    _merge_subtasks,
    _union_ids,
)

# ---------------------------------------------------------------------------
# Reducer unit tests
# ---------------------------------------------------------------------------


def test_union_ids_merges_two_lists() -> None:
    """Both branches' IDs survive the merge."""
    result = _union_ids(["task-001", "task-002"], ["task-003", "task-001"])
    assert set(result) == {"task-001", "task-002", "task-003"}


def test_union_ids_handles_empty_lists() -> None:
    """Empty input from one branch does not wipe the other branch's IDs."""
    assert set(_union_ids([], ["task-001"])) == {"task-001"}
    assert set(_union_ids(["task-002"], [])) == {"task-002"}
    assert set(_union_ids([], [])) == set()


def test_merge_subtasks_last_status_wins_per_id() -> None:
    """When the same subtask ID appears in both lists, the latter wins."""
    subtask_a = SubTask(
        id="task-001",
        wiki_page="Page1",
        title="Title",
        description="Desc",
        status="failed",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=["error"],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    subtask_b = SubTask(
        id="task-001",
        wiki_page="Page1",
        title="Title",
        description="Desc",
        status="merged",  # different status
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    # current contains subtask_a, update contains subtask_b → b should win
    result = _merge_subtasks([subtask_a], [subtask_b])
    assert len(result) == 1
    assert result[0]["status"] == "merged"


def test_merge_subtasks_preserves_distinct_ids() -> None:
    """Distinct IDs from both branches survive the merge."""
    subtask_a = SubTask(
        id="task-001",
        wiki_page="Page1",
        title="Title",
        description="Desc",
        status="failed",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    subtask_b = SubTask(
        id="task-002",
        wiki_page="Page2",
        title="Title",
        description="Desc",
        status="merged",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    result = _merge_subtasks([subtask_a], [subtask_b])
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert ids == {"task-001", "task-002"}


# ---------------------------------------------------------------------------
# Full state fan-in simulation
# ---------------------------------------------------------------------------


def test_fanin_preserves_failed_and_completed_subtask_ids() -> None:
    """Simulate two parallel branch writes merging through union reducers.

    Branch A writes failed_subtask_ids=["task-001"].
    Branch B writes completed_subtask_ids=["task-002"].

    After fan-in, both IDs must be present in the merged state.
    """
    base_state: FactoryState = {
        "thread_id": "Task_0042",
        "task_wiki_page": "Task_0042",
        "title": "Test",
        "requirements": "Test reqs",
        "subtasks": [],
        "decomposition_approved": True,
        "active_grinders": [],
        "completed_subtask_ids": [],  # no reducer needed for base state
        "failed_subtask_ids": [],  # no reducer needed for base state
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "graph_status": "grinding",
        "error": None,
        "escalation_decision": None,
    }

    branch_a_update = {"failed_subtask_ids": ["task-001"]}
    branch_b_update = {"completed_subtask_ids": ["task-002"]}

    # Apply each branch update through the union reducer
    merged_failed = _union_ids(
        base_state["failed_subtask_ids"],
        branch_a_update["failed_subtask_ids"],
    )
    merged_completed = _union_ids(
        base_state["completed_subtask_ids"],
        branch_b_update["completed_subtask_ids"],
    )

    assert "task-001" in merged_failed
    assert "task-002" in merged_completed


def test_fanin_delta_return_preserves_concurrent_status_updates() -> None:
    """With delta returns (each branch returns only its updated subtask),
    both parallel branches' status updates survive the fan-in merge.

    This is the regression test for the fan-in merge bug fixed in Milestone F8.
    The bug: each parallel grind branch returned the full subtask list. The later
    branch's stale snapshot (sub-01 still "pending") clobbered the earlier
    branch's "failed" update. Fix: return only [updated_subtask] (delta).
    """
    sub_01 = SubTask(
        id="task-001",
        wiki_page="Page1",
        title="Sub 01",
        description="Desc",
        status="pending",
        parent_task="Parent",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    sub_02 = SubTask(
        id="task-002",
        wiki_page="Page2",
        title="Sub 02",
        description="Desc",
        status="pending",
        parent_task="Parent",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    initial = [sub_01, sub_02]

    # Branch A returns only its delta: sub-01 failed
    failed_01 = SubTask(**{**sub_01, "status": "failed", "error_log": ["oops"]})
    after_a = _merge_subtasks(initial, [failed_01])

    # Branch B returns only its delta: sub-02 in review
    review_02 = SubTask(**{**sub_02, "status": "review", "pr_url": "https://github.com/o/r/pull/2"})
    final = _merge_subtasks(after_a, [review_02])

    by_id = {s["id"]: s for s in final}
    assert by_id["task-001"]["status"] == "failed"
    assert by_id["task-002"]["status"] == "review"


def test_fanin_subtasks_both_failed_and_completed_in_list() -> None:
    """Simulate two parallel branches updating subtasks with different statuses.

    Branch A marks task-001 as failed.
    Branch B marks task-002 as completed.

    After fan-in merge, both subtasks are present with their correct statuses.
    """
    subtask_1_failed = SubTask(
        id="task-001",
        wiki_page="Page1",
        title="Title",
        description="Desc",
        status="failed",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=["error"],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )
    subtask_2_completed = SubTask(
        id="task-002",
        wiki_page="Page2",
        title="Title",
        description="Desc",
        status="merged",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )

    base_subtasks = [subtask_1_failed]
    branch_b_subtasks = [subtask_2_completed]

    merged = _merge_subtasks(base_subtasks, branch_b_subtasks)

    assert len(merged) == 2
    by_id = {s["id"]: s for s in merged}
    assert by_id["task-001"]["status"] == "failed"
    assert by_id["task-002"]["status"] == "merged"
