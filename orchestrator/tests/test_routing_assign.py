"""Unit tests for the assign-node routing functions.

Covers branches in:
- ``_select_subtasks_to_dispatch`` (internal helper)
- ``route_grinders`` (fan-out router on assign_grinders node)
- ``route_after_grinding`` (per-subtask post-grind router)
- ``route_after_pm_review`` (per-subtask post-review router)

These complement the existing tests in test_concurrency.py and test_routing.py
without duplicating them.  Focus: branch coverage gaps, status edge cases,
payload correctness, and the _select_subtasks_to_dispatch helper directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Send

from factory.graph import route_after_grinding, route_after_pm_review
from factory.nodes.assign import _select_subtasks_to_dispatch, route_grinders
from factory.state import FactoryState, SubTask

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_subtask(
    subtask_id: str = "t1",
    status: str = "pending",
    files: list[str] | None = None,
    attempt: int = 0,
    max_attempts: int = 3,
    review_feedback: str | None = None,
) -> SubTask:
    """Return a fully populated SubTask for testing."""
    if files is None:
        files = [f"src/{subtask_id}.py"]
    return SubTask(
        id=subtask_id,
        wiki_page=f"{subtask_id}_page",
        parent_task="Task_0042_test",
        title=f"Subtask {subtask_id}",
        description="Do the thing.",
        status=status,
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=attempt,
        max_attempts=max_attempts,
        error_log=[],
        files_touched=files,
        acceptance_criteria=[],
        token_budget=50_000,
        tokens_used=0,
        review_feedback=review_feedback,
        code_skeleton=None,
    )


def _make_state(**kwargs) -> FactoryState:
    defaults: dict = {
        "thread_id": "task-0042",
        "task_wiki_page": "Task_0042_test",
        "title": "Test task",
        "requirements": "Build something.",
        "subtasks": [],
        "decomposition_approved": True,
        "active_grinders": [],
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "incremental_costs_usd": [],
        "graph_status": "grinding",
        "error": None,
        "escalation_decision": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


# ---------------------------------------------------------------------------
# _select_subtasks_to_dispatch — direct unit tests of the helper
# ---------------------------------------------------------------------------


class TestSelectSubtasksToDispatch:
    """Direct tests of the internal helper (not exercised through route_grinders alone)."""

    def test_changes_requested_treated_as_pending(self) -> None:
        """'changes_requested' subtasks are dispatched just like 'pending'."""
        sub = _make_subtask("t1", status="changes_requested")
        state = _make_state(subtasks=[sub], active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            result = _select_subtasks_to_dispatch(state)
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_terminal_statuses_not_dispatched(self) -> None:
        """Subtasks with terminal statuses (merged, done, failed) are never dispatched."""
        for terminal_status in (
            "merged",
            "done",
            "failed",
            "skipped",
            "in_progress",
            "assigned",
            "review",
        ):
            sub = _make_subtask("t1", status=terminal_status)
            state = _make_state(subtasks=[sub], active_grinders=[])
            with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
                result = _select_subtasks_to_dispatch(state)
            assert result == [], f"Expected empty for status={terminal_status!r}"

    def test_subtask_with_empty_files_never_conflicts(self) -> None:
        """Two subtasks with no files_touched never block each other."""
        sub1 = _make_subtask("t1", files=[])
        sub2 = _make_subtask("t2", files=[])
        state = _make_state(subtasks=[sub1, sub2], active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            result = _select_subtasks_to_dispatch(state)
        assert len(result) == 2

    def test_partial_file_overlap_blocks_conflicting_subtask(self) -> None:
        """t2 shares one file with t1; only t1 is dispatched in this round."""
        sub1 = _make_subtask("t1", files=["src/a.py", "src/b.py"])
        sub2 = _make_subtask("t2", files=["src/b.py", "src/c.py"])
        sub3 = _make_subtask("t3", files=["src/d.py"])
        state = _make_state(subtasks=[sub1, sub2, sub3], active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            result = _select_subtasks_to_dispatch(state)
        dispatched_ids = [s["id"] for s in result]
        assert "t1" in dispatched_ids
        assert "t3" in dispatched_ids
        assert "t2" not in dispatched_ids

    def test_active_subtask_id_skipped_even_if_status_pending(self) -> None:
        """Subtask in active_grinders is not re-dispatched even if its status is 'pending'."""
        sub = _make_subtask("t1", status="pending")
        state = _make_state(subtasks=[sub], active_grinders=["t1"])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            result = _select_subtasks_to_dispatch(state)
        assert result == []

    def test_slot_limit_caps_output(self) -> None:
        """Helper never returns more subtasks than slots_free."""
        subtasks = [
            _make_subtask(f"t{i}", files=[f"src/unique_{i}.py"]) for i in range(5)
        ]
        state = _make_state(subtasks=subtasks, active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 2):
            result = _select_subtasks_to_dispatch(state)
        assert len(result) == 2

    def test_returns_empty_when_active_fills_cap(self) -> None:
        """Returns [] immediately when active_grinders == cap."""
        sub = _make_subtask("t2", status="pending")
        state = _make_state(subtasks=[sub], active_grinders=["t1"])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            result = _select_subtasks_to_dispatch(state)
        assert result == []

    def test_hbr_budget_exhausted_blocks_dispatch(self) -> None:
        """When HBR reports budget exhausted, no subtasks are dispatched."""
        from unittest.mock import MagicMock

        sub = _make_subtask("t1", files=["src/t1.py"])
        state = _make_state(subtasks=[sub], active_grinders=[])
        mock_hbr = MagicMock()
        mock_hbr.can_allocate_sandbox.return_value = False
        with (
            patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5),
            patch("factory.nodes.assign.get_hbr", return_value=mock_hbr),
        ):
            result = _select_subtasks_to_dispatch(state)
        assert result == []

    def test_hbr_budget_ok_does_not_block(self) -> None:
        """When HBR reports budget available, dispatch proceeds normally."""
        from unittest.mock import MagicMock

        sub = _make_subtask("t1", files=["src/t1.py"])
        state = _make_state(subtasks=[sub], active_grinders=[])
        mock_hbr = MagicMock()
        mock_hbr.can_allocate_sandbox.return_value = True
        with (
            patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5),
            patch("factory.nodes.assign.get_hbr", return_value=mock_hbr),
        ):
            result = _select_subtasks_to_dispatch(state)
        assert len(result) == 1

    def test_mixed_statuses_only_eligible_dispatched(self) -> None:
        """Only 'pending'/'changes_requested' subtasks are considered."""
        sub_pending = _make_subtask("t1", status="pending")
        sub_cr = _make_subtask("t2", status="changes_requested")
        sub_done = _make_subtask("t3", status="merged", files=["src/t3.py"])
        sub_fail = _make_subtask("t4", status="failed", files=["src/t4.py"])
        state = _make_state(
            subtasks=[sub_pending, sub_cr, sub_done, sub_fail],
            active_grinders=[],
        )
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            result = _select_subtasks_to_dispatch(state)
        dispatched_ids = [s["id"] for s in result]
        assert "t1" in dispatched_ids
        assert "t2" in dispatched_ids
        assert "t3" not in dispatched_ids
        assert "t4" not in dispatched_ids


# ---------------------------------------------------------------------------
# route_grinders — Send command correctness
# ---------------------------------------------------------------------------


class TestRouteGrindersPayload:
    """Tests that Send commands from route_grinders carry correct payloads."""

    def test_send_targets_grind_node(self) -> None:
        """Every Send returned by route_grinders targets the 'grind' node."""
        subtasks = [
            _make_subtask("t1", files=["src/t1.py"]),
            _make_subtask("t2", files=["src/t2.py"]),
        ]
        state = _make_state(subtasks=subtasks, active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            sends = route_grinders(state)
        assert all(isinstance(s, Send) for s in sends)
        assert all(s.node == "grind" for s in sends)

    def test_send_payload_contains_current_subtask_id(self) -> None:
        """Each Send payload's _current_subtask_id matches its subtask."""
        subtasks = [
            _make_subtask("t1", files=["src/t1.py"]),
            _make_subtask("t2", files=["src/t2.py"]),
        ]
        state = _make_state(subtasks=subtasks, active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            sends = route_grinders(state)
        dispatched_ids = {s.arg["_current_subtask_id"] for s in sends}
        assert dispatched_ids == {"t1", "t2"}

    def test_send_payload_carries_full_state(self) -> None:
        """Send payloads include the full state so grind_node has all context."""
        sub = _make_subtask("t1", files=["src/t1.py"])
        state = _make_state(
            subtasks=[sub],
            active_grinders=[],
            task_wiki_page="Task_9999_test",
        )
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            sends = route_grinders(state)
        assert len(sends) == 1
        payload = sends[0].arg
        assert payload["task_wiki_page"] == "Task_9999_test"
        assert payload["subtasks"] == [sub]

    def test_returns_empty_list_not_none_when_no_pending(self) -> None:
        """route_grinders returns [] (not None) when nothing is eligible."""
        state = _make_state(subtasks=[], active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            result = route_grinders(state)
        assert result == []

    def test_changes_requested_subtask_dispatched(self) -> None:
        """A 'changes_requested' subtask is included in the fan-out."""
        sub = _make_subtask("t1", status="changes_requested")
        state = _make_state(subtasks=[sub], active_grinders=[])
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            sends = route_grinders(state)
        assert len(sends) == 1
        assert sends[0].arg["_current_subtask_id"] == "t1"

    def test_active_grinder_not_included_in_send(self) -> None:
        """An already-active subtask is never sent again via route_grinders."""
        sub_active = _make_subtask("t1", status="pending", files=["src/t1.py"])
        sub_pending = _make_subtask("t2", status="pending", files=["src/t2.py"])
        state = _make_state(
            subtasks=[sub_active, sub_pending],
            active_grinders=["t1"],
        )
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            sends = route_grinders(state)
        dispatched_ids = [s.arg["_current_subtask_id"] for s in sends]
        assert "t1" not in dispatched_ids
        assert "t2" in dispatched_ids


# ---------------------------------------------------------------------------
# route_after_grinding — uncovered branches
# ---------------------------------------------------------------------------


class TestRouteAfterGrindingExtraEdgeCases:
    """Additional branches not covered by test_routing.py::TestRouteAfterGrinding."""

    def test_review_status_routes_to_pm_review(self) -> None:
        """Subtask with status 'review' sends to pm_review (canonical success path)."""
        sub = _make_subtask("t1", status="review")
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_grinding(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].node == "pm_review"

    def test_non_failed_non_review_status_still_routes_to_pm_review(self) -> None:
        """Any non-failed subtask status (e.g. 'in_progress') still fans out to pm_review.

        The routing function only gates on status == 'failed'; all other states
        are treated as 'completed enough' and sent to PM review.
        """
        sub = _make_subtask("t1", status="in_progress")
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_grinding(state)
        # Should be a Send to pm_review, not "escalate"
        assert isinstance(result, list)
        assert result[0].node == "pm_review"

    def test_send_payload_current_subtask_id_correct(self) -> None:
        """The _current_subtask_id in the Send payload equals the finishing subtask."""
        sub = _make_subtask("task-0042-sub-07", status="review")
        state = _make_state(subtasks=[sub], _current_subtask_id="task-0042-sub-07")
        result = route_after_grinding(state)
        assert isinstance(result, list)
        assert result[0].arg["_current_subtask_id"] == "task-0042-sub-07"

    def test_multiple_subtasks_only_current_sent(self) -> None:
        """When siblings exist, only the current subtask's branch is sent to pm_review."""
        sub1 = _make_subtask("t1", status="review", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="in_progress", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        result = route_after_grinding(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].arg["_current_subtask_id"] == "t1"


# ---------------------------------------------------------------------------
# route_after_pm_review — uncovered branches
# ---------------------------------------------------------------------------


class TestRouteAfterPmReviewExtraEdgeCases:
    """Additional branches not covered by test_routing.py::TestRouteAfterPmReview."""

    def test_approved_with_sibling_failed_all_terminal(self) -> None:
        """All subtasks terminal (one merged, one failed) → all_approved when no auto_merge."""
        sub1 = _make_subtask("t1", status="merged", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="failed", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == "all_approved"

    def test_approved_with_sibling_done_all_terminal(self) -> None:
        """'done' status counts as terminal — all done → all_approved."""
        sub1 = _make_subtask("t1", status="merged", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="done", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == "all_approved"

    def test_approved_sibling_still_pending_returns_end(self) -> None:
        """Sibling with 'pending' status means not all done → this branch returns END."""
        sub1 = _make_subtask("t1", status="merged", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="pending", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == END

    def test_changes_requested_with_one_retry_left_sends_to_grind(self) -> None:
        """attempt == max_attempts - 1: still has one retry left → Send to grind."""
        sub = _make_subtask("t1", status="changes_requested", attempt=2, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_pm_review(state)
        assert isinstance(result, list)
        assert result[0].node == "grind"
        assert result[0].arg["_current_subtask_id"] == "t1"

    def test_changes_requested_attempt_zero_max_one_escalates(self) -> None:
        """attempt=0, max_attempts=1 — first rejection is immediately at cap."""
        sub = _make_subtask("t1", status="changes_requested", attempt=1, max_attempts=1)
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_pm_review(state)
        assert result == "escalate"

    def test_changes_requested_attempt_exceeds_max_escalates(self) -> None:
        """attempt > max_attempts (defensive) also escalates."""
        sub = _make_subtask("t1", status="changes_requested", attempt=5, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_pm_review(state)
        assert result == "escalate"

    def test_none_current_id_escalates(self) -> None:
        """_current_subtask_id absent from state → escalate (not a crash)."""
        sub = _make_subtask("t1", status="merged")
        state = _make_state(subtasks=[sub])
        # No _current_subtask_id key — .get() returns None
        result = route_after_pm_review(state)
        assert result == "escalate"

    def test_rework_send_targets_grind_node(self) -> None:
        """Send for rework always targets 'grind', not any other node."""
        sub = _make_subtask("t1", status="changes_requested", attempt=0, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        result = route_after_pm_review(state)
        assert isinstance(result, list)
        assert result[0].node == "grind"

    def test_auto_merge_true_routes_skip_human_review(self) -> None:
        """auto_merge=True and all subtasks terminal → skip_human_review."""
        sub = _make_subtask("t1", status="merged")
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = True
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == "skip_human_review"

    def test_single_subtask_approved_routes_all_done(self) -> None:
        """Single subtask, merged — all done, routes to all_approved without auto_merge."""
        sub = _make_subtask("t1", status="merged")
        state = _make_state(subtasks=[sub], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == "all_approved"

    def test_sibling_in_changes_requested_not_terminal_returns_end(self) -> None:
        """Sibling in 'changes_requested' is not terminal → this branch returns END."""
        sub1 = _make_subtask("t1", status="merged", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="changes_requested", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == END

    def test_skipped_subtask_counts_as_terminal(self) -> None:
        """'skipped' subtasks (retired by redecompose) count as terminal.

        When all active subtasks are done and siblings are skipped, the whole
        task should proceed to human_review / merge rather than stalling.
        """
        sub1 = _make_subtask("t1", status="merged", files=["src/t1.py"])
        sub2 = _make_subtask("t2", status="skipped", files=["src/t2.py"])
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id="t1")
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        # 'skipped' is in terminal_statuses, so all_done → all_approved
        assert result == "all_approved"
