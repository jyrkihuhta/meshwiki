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
    _append_cost,
    _merge_active_grinders,
    _merge_subtasks,
    _union_ids,
)

# ---------------------------------------------------------------------------
# Reducer unit tests
# ---------------------------------------------------------------------------


def test_merge_active_grinders_unions_nonempty() -> None:
    """Non-empty update is unioned into current — parallel branches can safely add IDs."""
    result = _merge_active_grinders(["id-1"], ["id-2"])
    assert set(result) == {"id-1", "id-2"}


def test_merge_active_grinders_deduplicates() -> None:
    """Duplicate IDs are removed after a union."""
    result = _merge_active_grinders(["id-1", "id-2"], ["id-1"])
    assert result.count("id-1") == 1
    assert "id-2" in result


def test_merge_active_grinders_empty_update_resets() -> None:
    """Empty update list signals a full reset — used by collect_results_node."""
    result = _merge_active_grinders(["id-1", "id-2"], [])
    assert result == []


def test_merge_active_grinders_both_empty_stays_empty() -> None:
    """Union of two empty lists returns empty."""
    result = _merge_active_grinders([], [])
    assert result == []


def test_merge_active_grinders_parallel_add_semantics() -> None:
    """Simulate two parallel grind_node branches each adding their own ID.

    Branch A returns active_grinders=["task-001"].
    Branch B returns active_grinders=["task-002"].

    After fan-in via two reducer applications, both IDs must be present.
    """
    after_a = _merge_active_grinders([], ["task-001"])
    after_b = _merge_active_grinders(after_a, ["task-002"])
    assert set(after_b) == {"task-001", "task-002"}


def test_union_ids_merges_two_lists() -> None:
    """Both branches' IDs survive the merge."""
    result = _union_ids(["task-001", "task-002"], ["task-003", "task-001"])
    assert set(result) == {"task-001", "task-002", "task-003"}


def test_union_ids_handles_empty_lists() -> None:
    """Empty input from one branch does not wipe the other branch's IDs."""
    assert set(_union_ids([], ["task-001"])) == {"task-001"}
    assert set(_union_ids(["task-002"], [])) == {"task-002"}
    assert set(_union_ids([], [])) == set()


def test_append_cost_accumulates() -> None:
    """Incremental cost lists are concatenated by the reducer."""
    assert _append_cost([0.001, 0.002], [0.003]) == [0.001, 0.002, 0.003]


def test_append_cost_empty_current() -> None:
    """Starting from an empty list appends the update."""
    assert _append_cost([], [0.005]) == [0.005]


def test_append_cost_empty_update() -> None:
    """An empty update leaves the current list unchanged."""
    assert _append_cost([0.01], []) == [0.01]


def test_append_cost_parallel_branches_sum_correctly() -> None:
    """Simulates two parallel grind branches each adding their cost delta.

    After fan-in via two reducer applications, all three cost entries must
    be present and their sum matches the expected total.
    """
    after_a = _append_cost([], [0.001])  # decompose phase
    after_b = _append_cost(after_a, [0.002])  # grind branch A
    after_c = _append_cost(after_b, [0.003])  # grind branch B
    assert after_c == [0.001, 0.002, 0.003]
    assert abs(sum(after_c) - 0.006) < 1e-9


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
    review_02 = SubTask(
        **{**sub_02, "status": "review", "pr_url": "https://github.com/o/r/pull/2"}
    )
    final = _merge_subtasks(after_a, [review_02])

    by_id = {s["id"]: s for s in final}
    assert by_id["task-001"]["status"] == "failed"
    assert by_id["task-002"]["status"] == "review"


def _make_subtask_raw(
    id_: str,
    wiki_page: str,
    status: str,
    error_log: list | None = None,
    pr_url: str | None = None,
) -> SubTask:
    """Minimal SubTask factory for reducer tests."""
    return SubTask(
        id=id_,
        wiki_page=wiki_page,
        parent_task="ParentEpic",
        title=f"Subtask {id_}",
        description="Test",
        status=status,  # type: ignore[arg-type]
        assigned_grinder=None,
        branch_name=None,
        pr_url=pr_url,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=error_log or [],
        files_touched=[],
        acceptance_criteria=[],
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )


def test_fanin_full_list_return_would_clobber_earlier_branch() -> None:
    """Regression: if grind_node returned the FULL subtask list (old behavior),
    the later branch would clobber the earlier branch's status update.

    This test documents the bug and verifies it does NOT happen with the delta
    reducer approach (each branch returns only its own updated entry).

    Scenario:
    - Initial state: [sub-01 pending, sub-02 pending]
    - Branch A finishes first: sets sub-01 → failed.
      State after branch A: [sub-01 failed, sub-02 pending]
    - Branch B finishes later: sets sub-02 → review.
      Branch B had a stale snapshot where sub-01 was still "pending".

    OLD behavior (full list return, last-write-wins):
      Branch B returns [sub-01 PENDING, sub-02 review]  ← stale sub-01
      LangGraph applies branch B's full list, overwriting branch A's update.
      Result: sub-01 ends up "pending" again — the failed status is LOST.

    NEW behavior (delta return + _merge_subtasks reducer):
      Branch B returns only [sub-02 review].
      Reducer merges by ID: sub-01 keeps "failed", sub-02 gets "review".
      Result: both statuses are preserved correctly.
    """
    initial = [
        _make_subtask_raw("sub-01", "Page1", "pending"),
        _make_subtask_raw("sub-02", "Page2", "pending"),
    ]

    # ── Old (buggy) behavior: each branch returns the full list ──────────────
    # Branch A finishes first: returns full list with sub-01=failed, sub-02=pending
    branch_a_full = [
        _make_subtask_raw("sub-01", "Page1", "failed", error_log=["broke"]),
        _make_subtask_raw("sub-02", "Page2", "pending"),  # stale snapshot of sub-02
    ]
    after_a_old = _merge_subtasks(initial, branch_a_full)
    assert after_a_old[0]["status"] == "failed" or after_a_old[1]["status"] == "failed"

    # Branch B finishes later: returns full list with sub-01=pending (stale!), sub-02=review
    branch_b_full = [
        _make_subtask_raw(
            "sub-01", "Page1", "pending"
        ),  # stale — branch B saw sub-01 as pending
        _make_subtask_raw(
            "sub-02", "Page2", "review", pr_url="https://github.com/o/r/pull/2"
        ),
    ]
    after_b_old = _merge_subtasks(after_a_old, branch_b_full)
    by_id_old = {s["id"]: s for s in after_b_old}
    # BUG: sub-01's "failed" status is clobbered by branch B's stale "pending"
    assert (
        by_id_old["sub-01"]["status"] == "pending"
    ), "Demonstrates the OLD bug: full-list return causes branch B to clobber branch A's status"

    # ── New (fixed) behavior: each branch returns only its own delta ──────────
    # Branch A returns only its updated entry
    branch_a_delta = [
        _make_subtask_raw("sub-01", "Page1", "failed", error_log=["broke"]),
    ]
    after_a_new = _merge_subtasks(initial, branch_a_delta)

    # Branch B returns only its updated entry (no stale sub-01 in the list)
    branch_b_delta = [
        _make_subtask_raw(
            "sub-02", "Page2", "review", pr_url="https://github.com/o/r/pull/2"
        ),
    ]
    after_b_new = _merge_subtasks(after_a_new, branch_b_delta)
    by_id_new = {s["id"]: s for s in after_b_new}

    # FIX: both statuses are preserved
    assert (
        by_id_new["sub-01"]["status"] == "failed"
    ), "sub-01's failed status must survive the fan-in (branch A's delta is not clobbered)"
    assert (
        by_id_new["sub-02"]["status"] == "review"
    ), "sub-02's review status must also be present after fan-in"
    assert len(after_b_new) == 2, "Both subtasks must be present after merge"


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
