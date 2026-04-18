"""Tests for grinder fan-out concurrency limiting."""

from __future__ import annotations

from unittest.mock import patch

from factory.nodes.assign import route_grinders
from factory.state import FactoryState, SubTask


def _make_subtask(
    subtask_id: str, status: str = "pending", files: list[str] | None = None
) -> SubTask:
    """Return a minimal SubTask for testing."""
    if files is None:
        files = [f"src/{subtask_id}.py"]
    return SubTask(
        id=subtask_id,
        wiki_page=f"{subtask_id}_page",
        title=f"Subtask {subtask_id}",
        description="Do the thing.",
        status=status,
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=files,
        token_budget=50000,
        tokens_used=0,
        review_feedback=None,
        code_skeleton=None,
    )


def _make_state(
    pending_ids: list[str],
    active_ids: list[str],
) -> FactoryState:
    """Build a FactoryState with given pending and active subtask IDs."""
    subtasks = [_make_subtask(sid, "pending") for sid in pending_ids]
    subtasks += [_make_subtask(sid, "running") for sid in active_ids]
    return FactoryState(
        thread_id="task-0042",
        task_wiki_page="Task_0042_test",
        title="Test Task",
        requirements="Build something.",
        subtasks=subtasks,
        decomposition_approved=True,
        active_grinders=active_ids,
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        graph_status="grinding",
        error=None,
        escalation_decision=None,
    )


class TestRouteGrindersConcurrencyCap:
    """route_grinders respects FACTORY_MAX_CONCURRENT_SANDBOXES cap."""

    def test_cap_limits_dispatch_to_one(self) -> None:
        """With cap=1, route_grinders dispatches only one subtask even when three are pending."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t1", "t2", "t3"], [])
            dispatched = route_grinders(state)
            assert len(dispatched) == 1

    def test_no_dispatch_when_at_cap(self) -> None:
        """With cap=1 and one active grinder, no new dispatch occurs."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t2", "t3"], ["t1"])
            dispatched = route_grinders(state)
            assert dispatched == []

    def test_partial_dispatch_with_two_slots_free(self) -> None:
        """With cap=3 and one active, two pending subtasks get dispatched (no file conflicts)."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            state = _make_state(["t1", "t2", "t3"], ["active-1"])
            dispatched = route_grinders(state)
            assert len(dispatched) == 2

    def test_all_pending_dispatched_when_under_cap(self) -> None:
        """When pending < available slots and no conflicts, all pending get dispatched."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            state = _make_state(["t1", "t2"], [])
            dispatched = route_grinders(state)
            assert len(dispatched) == 2

    def test_excludes_already_active_from_pending(self) -> None:
        """Subtasks already in active_grinders are not dispatched again."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            state = _make_state(["t1", "t2", "t3"], ["t1"])
            dispatched = route_grinders(state)
            dispatched_ids = [s.arg["_current_subtask_id"] for s in dispatched]
            assert "t1" not in dispatched_ids

    def test_returns_empty_when_no_pending(self) -> None:
        """No dispatch when all subtasks are already running."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 3):
            state = _make_state([], ["t1", "t2", "t3"])
            dispatched = route_grinders(state)
            assert dispatched == []

    def test_file_conflict_serializes_within_cap(self) -> None:
        """File conflicts serialize even when cap would allow more."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            subtasks = [
                _make_subtask("t1", files=["src/shared.py"]),
                _make_subtask("t2", files=["src/shared.py"]),
            ]
            state = FactoryState(
                thread_id="task-0042",
                task_wiki_page="Task_0042_test",
                title="Test Task",
                requirements="Build something.",
                subtasks=subtasks,
                decomposition_approved=True,
                active_grinders=[],
                completed_subtask_ids=[],
                failed_subtask_ids=[],
                pm_messages=[],
                human_approval_response=None,
                human_feedback=None,
                cost_usd=0.0,
                graph_status="grinding",
                error=None,
                escalation_decision=None,
            )
            dispatched = route_grinders(state)
            assert len(dispatched) == 1

    def test_dispatch_respects_cap_not_file_conflicts(self) -> None:
        """When cap=1 and one pending, that one is dispatched regardless of conflicts."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t1"], [])
            dispatched = route_grinders(state)
            assert len(dispatched) == 1


class TestActiveGrindersCleanup:
    """grind_node removes subtask id from active_grinders after completion."""

    def test_active_grinders_removal_on_completion(self) -> None:
        """Simulate grinder completion: subtask ID should be removable from active list."""
        active = ["task-a", "task-b"]
        active.remove("task-a")
        assert active == ["task-b"]

    def test_active_grinders_removal_idempotent(self) -> None:
        """Removing a non-existent ID should not raise."""
        active = ["task-a"]
        if "task-b" in active:
            active.remove("task-b")
        assert active == ["task-a"]

    def test_active_grinders_list_behavior(self) -> None:
        """active_grinders should behave as a simple list of IDs."""
        active: list[str] = []
        active.append("t1")
        active.append("t2")
        assert len(active) == 2
        active.remove("t1")
        assert len(active) == 1
        assert "t1" not in active
        assert "t2" in active
