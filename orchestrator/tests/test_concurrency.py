"""Tests for grinder fan-out concurrency limiting."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command, Send

from factory.nodes.assign import assign_grinders_node, route_grinders
from factory.nodes.grind import grind_node, next_step_after_grind
from factory.nodes.pm_diagnose import pm_diagnose_node
from factory.nodes.pm_review import pm_review_node
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

    def test_file_held_by_in_flight_subtask_blocks_pending(self) -> None:
        """A pending subtask is deferred while an in-flight one holds its file."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 5):
            state = _make_state([], [])
            state["subtasks"] = [
                _make_subtask("t1", "review", files=["src/shared.py"]),
                _make_subtask("t2", files=["src/shared.py"]),
                _make_subtask("t3", files=["src/other.py"]),
            ]
            state["active_grinders"] = ["t1"]
            dispatched = route_grinders(state)
            assert [s.arg["_current_subtask_id"] for s in dispatched] == ["t3"]

    def test_dispatch_respects_cap_not_file_conflicts(self) -> None:
        """When cap=1 and one pending, that one is dispatched regardless of conflicts."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t1"], [])
            dispatched = route_grinders(state)
            assert len(dispatched) == 1


class TestAssignGrindersNodeDispatches:
    """assign_grinders_node fans out and marks the dispatched subtasks in flight.

    The node returns a ``Command``: its ``goto`` is the ``Send`` list built by
    route_grinders, its ``update`` adds exactly those subtask IDs to
    ``active_grinders``.  Because the update is written when the node finishes,
    the grinders are recorded as active *before* their sandboxes start.
    """

    def test_dispatches_and_marks_active(self) -> None:
        """Every dispatched subtask gets a Send and an active_grinders entry."""
        state = _make_state(["t1", "t2"], [])
        result = assign_grinders_node(state)
        assert isinstance(result, Command)
        assert all(isinstance(s, Send) and s.node == "grind" for s in result.goto)
        assert [s.arg["_current_subtask_id"] for s in result.goto] == ["t1", "t2"]
        assert result.update == {"active_grinders": {"add": ["t1", "t2"]}}

    def test_marks_only_what_fits_under_the_cap(self) -> None:
        """Subtasks deferred by the cap are neither sent nor marked active."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 2):
            state = _make_state(["t1", "t2", "t3"], [])
            result = assign_grinders_node(state)
        assert [s.arg["_current_subtask_id"] for s in result.goto] == ["t1", "t2"]
        assert result.update == {"active_grinders": {"add": ["t1", "t2"]}}

    def test_no_dispatch_when_at_cap(self) -> None:
        """At the concurrency cap nothing is sent and state is left untouched."""
        with patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", 1):
            state = _make_state(["t2", "t3"], ["t1"])
            result = assign_grinders_node(state)
        assert isinstance(result, Command)
        assert not result.goto
        assert not result.update

    def test_no_dispatch_when_nothing_pending(self) -> None:
        """With no pending subtasks nothing is sent and state is left untouched."""
        state = _make_state([], [])
        result = assign_grinders_node(state)
        assert isinstance(result, Command)
        assert not result.goto
        assert not result.update


def _mock_meshwiki_client() -> AsyncMock:
    """Return an AsyncMock MeshWikiClient configured as a context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.transition_task = AsyncMock(return_value={})
    mock_client.get_page = AsyncMock(return_value={"content": "## Terminal Log\nboom"})
    return mock_client


def _branch_state(subtask: SubTask) -> FactoryState:
    """State as a grind/review branch sees it: *subtask* dispatched and active."""
    state = _make_state([], [])
    state["subtasks"] = [subtask]
    state["active_grinders"] = [subtask["id"]]
    state["_current_subtask_id"] = subtask["id"]  # type: ignore[typeddict-unknown-key]
    return state


async def _run_grind(subtask: SubTask, result_subtask: dict) -> dict:
    """Run grind_node for *subtask* with the grinder returning *result_subtask*."""
    with (
        patch(
            "factory.nodes.grind.MeshWikiClient", return_value=_mock_meshwiki_client()
        ),
        patch(
            "factory.nodes.grind.grind_subtask",
            new=AsyncMock(
                return_value={"subtask": result_subtask, "incremental_cost_usd": 0.0}
            ),
        ),
    ):
        return await grind_node(_branch_state(subtask))


class TestGrindNodeActiveGrinders:
    """grind_node keeps a subtask in flight until its branch actually ends.

    A subtask is added to ``active_grinders`` when it is dispatched (by
    assign_grinders_node), not when its grinder finishes.  grind_node leaves
    the entry alone while the branch continues to pm_review or pm_diagnose and
    removes it only when the branch ends at escalate.
    """

    @pytest.mark.asyncio
    async def test_success_leaves_active_untouched(self) -> None:
        """A successful grind goes on to pm_review — still in flight."""
        subtask = _make_subtask("t1")
        result = await _run_grind(subtask, {**subtask, "status": "review"})
        assert "active_grinders" not in result
        assert result["_current_subtask_id"] == "t1"

    @pytest.mark.asyncio
    async def test_first_failure_leaves_active_untouched(self) -> None:
        """A first failure goes on to pm_diagnose — still in flight."""
        subtask = _make_subtask("t1")
        result = await _run_grind(subtask, {**subtask, "status": "failed"})
        assert "active_grinders" not in result

    @pytest.mark.asyncio
    async def test_repeated_failure_removes_from_active(self) -> None:
        """A failure after the first attempt escalates — the branch ends here."""
        subtask = {**_make_subtask("t1"), "attempt": 1, "review_feedback": "fix it"}
        result = await _run_grind(subtask, {**subtask, "status": "failed"})
        assert result["active_grinders"] == {"remove": ["t1"]}

    @pytest.mark.asyncio
    async def test_rework_without_feedback_removes_from_active(self) -> None:
        """The fail-fast path (rework without feedback) escalates too."""
        subtask = {**_make_subtask("t1"), "attempt": 1, "review_feedback": None}
        result = await _run_grind(subtask, {**subtask, "status": "review"})
        assert result["subtasks"][0]["status"] == "failed"
        assert result["active_grinders"] == {"remove": ["t1"]}

    def test_next_step_after_grind(self) -> None:
        """The shared predicate mirrors the three post-grind destinations."""
        base = _make_subtask("t1")
        assert next_step_after_grind({**base, "status": "review"}) == "pm_review"
        assert next_step_after_grind({**base, "status": "failed"}) == "pm_diagnose"
        assert (
            next_step_after_grind({**base, "status": "failed", "attempt": 1})
            == "escalate"
        )


class TestBranchEndRemovesFromActive:
    """The node that ends a grind → review branch removes it from active_grinders."""

    @staticmethod
    async def _run_review(subtask: SubTask, review: AsyncMock) -> dict:
        github = AsyncMock()
        github.__aenter__ = AsyncMock(return_value=github)
        github.__aexit__ = AsyncMock(return_value=None)
        with (
            patch(
                "factory.nodes.pm_review.MeshWikiClient",
                return_value=_mock_meshwiki_client(),
            ),
            patch("factory.nodes.pm_review.GitHubClient", return_value=github),
            patch("factory.nodes.pm_review.review_with_pm", new=review),
        ):
            return await pm_review_node(_branch_state(subtask))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("decision", ["approved", "changes_requested"])
    async def test_pm_review_removes_whatever_the_decision(self, decision: str) -> None:
        """Approved or sent back, the reviewed subtask has landed at the join."""
        subtask = _make_subtask("t1", "review")
        review = AsyncMock(return_value={"decision": decision, "feedback": "notes"})
        result = await self._run_review(subtask, review)
        assert result["active_grinders"] == {"remove": ["t1"]}

    @pytest.mark.asyncio
    async def test_pm_review_removes_when_the_review_itself_fails(self) -> None:
        """A review that raises still ends the branch."""
        subtask = _make_subtask("t1", "review")
        result = await self._run_review(
            subtask, AsyncMock(side_effect=RuntimeError("PM unavailable"))
        )
        assert result["subtasks"][0]["status"] == "failed"
        assert result["active_grinders"] == {"remove": ["t1"]}

    @staticmethod
    async def _run_diagnose(revised: str) -> dict:
        subtask = _make_subtask("t1", "failed")
        with (
            patch(
                "factory.nodes.pm_diagnose.MeshWikiClient",
                return_value=_mock_meshwiki_client(),
            ),
            patch(
                "factory.nodes.pm_diagnose.diagnose_with_pm",
                new=AsyncMock(
                    return_value={
                        "revised_description": revised,
                        "incremental_cost_usd": 0.0,
                    }
                ),
            ),
        ):
            return await pm_diagnose_node(_branch_state(subtask))

    @pytest.mark.asyncio
    async def test_pm_diagnose_retry_stays_in_flight(self) -> None:
        """A revised subtask is ground again: no change to active_grinders."""
        result = await self._run_diagnose("Try it this way instead.")
        assert result["subtasks"][0]["status"] == "pending"
        assert "active_grinders" not in result

    @pytest.mark.asyncio
    async def test_pm_diagnose_without_revision_removes(self) -> None:
        """No revision means escalation: the branch ends, the entry is removed."""
        result = await self._run_diagnose("")
        assert result["subtasks"][0]["status"] == "failed"
        assert result["active_grinders"] == {"remove": ["t1"]}
