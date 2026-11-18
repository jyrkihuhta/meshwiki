"""Tests for the LangGraph routing functions in factory/graph.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langgraph.types import Send

from factory.agents.pm_agent import _build_subtask
from factory.graph import (
    route_after_collect,
    route_after_escalation,
    route_after_grinding,
    route_after_human_code_review,
    route_after_intake,
    route_after_pm_diagnose,
)
from factory.state import FactoryState, SubTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> FactoryState:
    defaults: dict = {
        "thread_id": "task-0042",
        "task_wiki_page": "Task_0042_test",
        "title": "",
        "requirements": "",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": [],
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "incremental_costs_usd": [],
        "graph_status": "intake",
        "error": None,
        "escalation_decision": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


def _make_subtask(**kwargs) -> SubTask:
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
# route_after_intake
# ---------------------------------------------------------------------------


class TestRouteAfterIntake:
    def test_abort_when_failed(self) -> None:
        state = _make_state(graph_status="failed")
        assert route_after_intake(state) == "abort"

    def test_skip_decompose_when_already_grinding(self) -> None:
        state = _make_state(graph_status="grinding")
        assert route_after_intake(state) == "skip_decompose"

    def test_skip_decompose_when_approved(self) -> None:
        state = _make_state(decomposition_approved=True)
        assert route_after_intake(state) == "skip_decompose"

    def test_decompose_by_default(self) -> None:
        state = _make_state(graph_status="decomposing", decomposition_approved=False)
        assert route_after_intake(state) == "decompose"


# ---------------------------------------------------------------------------
# route_after_grinding — per-subtask fan-out via Send()
# ---------------------------------------------------------------------------


class TestRouteAfterGrinding:
    def test_succeeded_returns_send_to_pm_review(self) -> None:
        """A successfully ground subtask is fanned out directly to pm_review."""
        sub = _make_subtask(status="review")
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        result = route_after_grinding(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Send)
        assert result[0].node == "pm_review"
        assert result[0].arg["_current_subtask_id"] == sub["id"]

    def test_failed_subtask_first_attempt_diagnoses(self) -> None:
        """First failure (attempt=0) routes to pm_diagnose for PM-assisted retry."""
        sub = _make_subtask(status="failed", attempt=0)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        result = route_after_grinding(state)
        assert isinstance(result, list) and len(result) == 1
        assert result[0].node == "pm_diagnose"

    def test_failed_subtask_second_attempt_escalates(self) -> None:
        """Subsequent failures (attempt>0) escalate immediately."""
        sub = _make_subtask(status="failed", attempt=1)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        assert route_after_grinding(state) == "escalate"

    def test_missing_subtask_id_escalates(self) -> None:
        """If _current_subtask_id is not found in subtasks, fall back to escalate."""
        sub = _make_subtask(status="review")
        state = _make_state(subtasks=[sub], _current_subtask_id="nonexistent-id")
        assert route_after_grinding(state) == "escalate"

    def test_none_subtask_id_escalates(self) -> None:
        """If _current_subtask_id is None, fall back to escalate."""
        sub = _make_subtask(status="review")
        state = _make_state(subtasks=[sub])
        assert route_after_grinding(state) == "escalate"

    def test_send_payload_contains_full_state(self) -> None:
        """The Send payload carries the full state so pm_review has all context."""
        sub = _make_subtask(status="review")
        state = _make_state(
            subtasks=[sub],
            _current_subtask_id=sub["id"],
            task_wiki_page="Task_0042_test",
        )
        result = route_after_grinding(state)
        assert isinstance(result, list)
        payload = result[0].arg
        assert payload["task_wiki_page"] == "Task_0042_test"
        assert payload["subtasks"] == [sub]


# ---------------------------------------------------------------------------
# route_after_pm_diagnose
# ---------------------------------------------------------------------------


class TestRouteAfterPmDiagnose:
    def test_revised_subtask_is_ground_again(self) -> None:
        """A subtask the PM reset to 'pending' is sent straight back to grind."""
        sub = _make_subtask(status="pending", attempt=1)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        result = route_after_pm_diagnose(state)
        assert isinstance(result, list) and len(result) == 1
        assert isinstance(result[0], Send)
        assert result[0].node == "grind"
        assert result[0].arg["_current_subtask_id"] == sub["id"]

    def test_empty_diagnosis_escalates(self) -> None:
        """A subtask left 'failed' (PM returned no revision) escalates."""
        sub = _make_subtask(status="failed", attempt=0)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        assert route_after_pm_diagnose(state) == "escalate"

    def test_missing_subtask_escalates(self) -> None:
        """An unknown _current_subtask_id escalates instead of crashing."""
        sub = _make_subtask(status="pending")
        state = _make_state(subtasks=[sub], _current_subtask_id="nonexistent")
        assert route_after_pm_diagnose(state) == "escalate"


# ---------------------------------------------------------------------------
# route_after_collect — the fan-in join after per-subtask PM review
# ---------------------------------------------------------------------------


def _collect_route(state: FactoryState, *, auto_merge: bool = False) -> str:
    """Call route_after_collect with ``auto_merge`` pinned."""
    mock_settings = MagicMock()
    mock_settings.auto_merge = auto_merge
    with patch("factory.graph.get_settings", return_value=mock_settings):
        return route_after_collect(state)


class TestRouteAfterCollect:
    """route_after_collect decides on the merged state of all review branches."""

    def test_all_approved_skip_human_review_when_auto_merge(self) -> None:
        """All subtasks terminal + auto_merge → skip_human_review."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub])
        assert _collect_route(state, auto_merge=True) == "skip_human_review"

    def test_all_approved_when_no_auto_merge(self) -> None:
        """All subtasks terminal + no auto_merge → all_approved."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub])
        assert _collect_route(state) == "all_approved"

    def test_parallel_siblings_all_merged_proceeds(self) -> None:
        """Siblings approved in the same superstep are seen together → proceed.

        This is the fan-in the per-branch router could not do: each branch saw
        its siblings still in 'review' and ended the run.
        """
        subs = [
            _make_subtask(wiki_page=f"Task_0042_Sub_0{i}", status="merged")
            for i in (1, 2, 3)
        ]
        state = _make_state(subtasks=subs)
        assert _collect_route(state) == "all_approved"

    def test_sibling_still_in_review_waits(self) -> None:
        """A sibling whose pm_review has not landed yet → wait for it."""
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="review")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "wait"

    def test_sibling_still_grinding_waits(self) -> None:
        """A sibling still listed in active_grinders → wait, whatever its status.

        Covers the re-grind after a PM diagnosis: the sibling is 'pending' (or
        transiently 'failed') but its branch has not landed.
        """
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        for status in ("pending", "failed", "changes_requested"):
            sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status=status)
            state = _make_state(subtasks=[sub1, sub2], active_grinders=[sub2["id"]])
            assert _collect_route(state) == "wait", status

    def test_stale_active_entry_for_merged_subtask_is_ignored(self) -> None:
        """An active_grinders entry pointing at a settled subtask does not block."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub], active_grinders=[sub["id"], "ghost"])
        assert _collect_route(state) == "all_approved"

    def test_pending_sibling_dispatches_next_round(self) -> None:
        """A sibling held back by the cap / a file conflict → back to assign."""
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="pending")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "dispatch"

    def test_changes_requested_dispatches_rework(self) -> None:
        """changes_requested with retries left → back to assign for a re-grind."""
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(
            wiki_page="Task_0042_Sub_02",
            status="changes_requested",
            attempt=1,
            max_attempts=3,
        )
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "dispatch"

    def test_escalate_when_attempts_exhausted(self) -> None:
        """changes_requested at the attempt cap → escalate."""
        sub = _make_subtask(status="changes_requested", attempt=3, max_attempts=3)
        state = _make_state(subtasks=[sub])
        assert _collect_route(state) == "escalate"

    def test_escalation_takes_precedence_over_next_round(self) -> None:
        """An exhausted subtask escalates even when a sibling is still pending."""
        sub1 = _make_subtask(
            wiki_page="Task_0042_Sub_01",
            status="changes_requested",
            attempt=3,
            max_attempts=3,
        )
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="pending")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "escalate"

    def test_in_flight_sibling_defers_escalation(self) -> None:
        """Nothing is decided — not even escalation — while a branch is in flight."""
        sub1 = _make_subtask(
            wiki_page="Task_0042_Sub_01",
            status="changes_requested",
            attempt=3,
            max_attempts=3,
        )
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="review")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "wait"

    def test_failed_sibling_counts_as_terminal(self) -> None:
        """A landed 'failed' sibling does not block the post-review pipeline."""
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="failed")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "all_approved"

    def test_unknown_non_terminal_status_does_not_finalize(self) -> None:
        """A status no node can move on never falls through to finalize."""
        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="in_progress")
        state = _make_state(subtasks=[sub1, sub2])
        assert _collect_route(state) == "wait"


# ---------------------------------------------------------------------------
# route_after_human_code_review
# ---------------------------------------------------------------------------


class TestRouteAfterHumanCodeReview:
    def test_approved(self) -> None:
        state = _make_state(human_approval_response="approve")
        assert route_after_human_code_review(state) == "approved"

    def test_rejected_when_response_is_reject(self) -> None:
        state = _make_state(human_approval_response="reject")
        assert route_after_human_code_review(state) == "rejected"

    def test_rejected_when_response_is_none(self) -> None:
        state = _make_state(human_approval_response=None)
        assert route_after_human_code_review(state) == "rejected"


# ---------------------------------------------------------------------------
# route_after_escalation
# ---------------------------------------------------------------------------


class TestRouteAfterEscalation:
    def test_retry(self) -> None:
        state = _make_state(escalation_decision="retry")
        assert route_after_escalation(state) == "retry"

    def test_redecompose(self) -> None:
        state = _make_state(escalation_decision="redecompose")
        assert route_after_escalation(state) == "redecompose"

    def test_abandon_when_decision_is_abandon(self) -> None:
        state = _make_state(escalation_decision="abandon")
        assert route_after_escalation(state) == "abandon"

    def test_abandon_when_decision_is_none(self) -> None:
        state = _make_state(escalation_decision=None)
        assert route_after_escalation(state) == "abandon"
