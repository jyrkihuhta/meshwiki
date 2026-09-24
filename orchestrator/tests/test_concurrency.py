"""Tests for grinder fan-out concurrency limiting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.nodes.assign import assign_grinders_node, route_grinders
from factory.nodes.grind import grind_node
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
        parent_task="Task_0042_test",
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
        acceptance_criteria=[],
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
        active_grinders=list(active_ids),
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        incremental_costs_usd=[],
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


class TestAssignGrindersNodeIsNoOp:
    """assign_grinders_node returns an empty dict (no-op before fan-out).

    The fan-out to parallel grinder branches is handled entirely by
    route_grinders.  assign_grinders_node only exists because LangGraph
    requires a node (not a routing function) to own the conditional edge.
    """

    def test_returns_empty_dict(self) -> None:
        """assign_grinders_node always returns {}."""
        state = _make_state(["t1", "t2"], [])
        result = assign_grinders_node(state)
        assert result == {}

    def test_returns_empty_dict_when_at_cap(self) -> None:
        """assign_grinders_node returns {} even when at concurrency cap."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t2", "t3"], ["t1"])
            result = assign_grinders_node(state)
            assert result == {}

    def test_returns_empty_dict_when_nothing_pending(self) -> None:
        """assign_grinders_node returns {} when there are no pending subtasks."""
        state = _make_state([], [])
        result = assign_grinders_node(state)
        assert result == {}


class TestGrindNodeAddsToActive:
    """grind_node adds its subtask ID to active_grinders for crash-recovery tracking.

    The _merge_active_grinders reducer unions single-element additions from
    parallel branches.  collect_results_node resets active_grinders to []
    after all parallel branches join.
    """

    @staticmethod
    def _mock_meshwiki_client(transition_return=None):
        """Return an AsyncMock MeshWikiClient configured as a context manager."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.transition_task = AsyncMock(return_value=transition_return or {})
        return mock_client

    @pytest.mark.asyncio
    async def test_adds_subtask_id_on_success(self) -> None:
        """grind_node includes its own ID in active_grinders after a successful run."""
        subtask = _make_subtask("t1")
        state = _make_state([], [])
        state["subtasks"] = [subtask]
        state["_current_subtask_id"] = "t1"  # type: ignore[typeddict-unknown-key]

        updated_subtask = {
            **subtask,
            "status": "review",
            "pr_url": "https://github.com/o/r/pull/1",
        }
        mock_client = self._mock_meshwiki_client()

        with (
            patch("factory.nodes.grind.MeshWikiClient", return_value=mock_client),
            patch(
                "factory.nodes.grind.grind_subtask",
                new=AsyncMock(
                    return_value={
                        "subtask": updated_subtask,
                        "incremental_cost_usd": 0.0,
                    }
                ),
            ),
        ):
            result = await grind_node(state)

        assert "active_grinders" in result
        assert "t1" in result["active_grinders"]

    @pytest.mark.asyncio
    async def test_adds_subtask_id_on_failure(self) -> None:
        """grind_node includes its ID in active_grinders even when the run fails."""
        subtask = _make_subtask("t1")
        state = _make_state([], [])
        state["subtasks"] = [subtask]
        state["_current_subtask_id"] = "t1"  # type: ignore[typeddict-unknown-key]

        failed_subtask = {**subtask, "status": "failed", "error_log": ["boom"]}
        mock_client = self._mock_meshwiki_client()

        with (
            patch("factory.nodes.grind.MeshWikiClient", return_value=mock_client),
            patch(
                "factory.nodes.grind.grind_subtask",
                new=AsyncMock(
                    return_value={
                        "subtask": failed_subtask,
                        "incremental_cost_usd": 0.0,
                    }
                ),
            ),
        ):
            result = await grind_node(state)

        assert "active_grinders" in result
        assert "t1" in result["active_grinders"]

    @pytest.mark.asyncio
    async def test_active_grinders_is_single_element_list(self) -> None:
        """grind_node returns a single-element list for the reducer to union safely."""
        subtask = _make_subtask("t1")
        state = _make_state([], [])
        state["subtasks"] = [subtask]
        state["_current_subtask_id"] = "t1"  # type: ignore[typeddict-unknown-key]

        done_subtask = {**subtask, "status": "review"}
        mock_client = self._mock_meshwiki_client()

        with (
            patch("factory.nodes.grind.MeshWikiClient", return_value=mock_client),
            patch(
                "factory.nodes.grind.grind_subtask",
                new=AsyncMock(
                    return_value={"subtask": done_subtask, "incremental_cost_usd": 0.0}
                ),
            ),
        ):
            result = await grind_node(state)

        assert result["active_grinders"] == ["t1"]
