"""Tests for the LangGraph routing functions in factory/graph.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langgraph.types import Send

from factory.agents.pm_agent import _build_subtask
from factory.graph import (
    route_after_escalation,
    route_after_grinding,
    route_after_human_code_review,
    route_after_intake,
    route_after_pm_review,
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
# route_after_pm_review — per-subtask fan-out routing
# ---------------------------------------------------------------------------


class TestRouteAfterPmReview:
    def test_escalate_when_attempts_exhausted(self) -> None:
        sub = _make_subtask(status="changes_requested", attempt=3, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        assert route_after_pm_review(state) == "escalate"

    def test_rework_sends_to_grind_when_retries_remain(self) -> None:
        """changes_requested with retries left returns Send to grind."""
        sub = _make_subtask(status="changes_requested", attempt=1, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        result = route_after_pm_review(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Send)
        assert result[0].node == "grind"
        assert result[0].arg["_current_subtask_id"] == sub["id"]

    def test_approved_all_done_skip_human_review_when_auto_merge(self) -> None:
        """All subtasks terminal + auto_merge → skip_human_review."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        mock_settings = MagicMock()
        mock_settings.auto_merge = True
        with patch("factory.graph.get_settings", return_value=mock_settings):
            assert route_after_pm_review(state) == "skip_human_review"

    def test_approved_all_done_all_approved_when_no_auto_merge(self) -> None:
        """All subtasks terminal + no auto_merge → all_approved."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            assert route_after_pm_review(state) == "all_approved"

    def test_approved_but_siblings_still_running_returns_end(self) -> None:
        """Approved subtask but sibling still in 'review' — branch ends (END)."""
        from langgraph.graph import END

        sub1 = _make_subtask(wiki_page="Task_0042_Sub_01", status="merged")
        sub2 = _make_subtask(wiki_page="Task_0042_Sub_02", status="review")
        state = _make_state(subtasks=[sub1, sub2], _current_subtask_id=sub1["id"])
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.graph.get_settings", return_value=mock_settings):
            result = route_after_pm_review(state)
        assert result == END

    def test_missing_subtask_id_escalates(self) -> None:
        """Missing _current_subtask_id falls back to escalate."""
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub], _current_subtask_id="nonexistent")
        assert route_after_pm_review(state) == "escalate"

    def test_rework_send_payload_has_correct_subtask_id(self) -> None:
        """The Send payload for rework carries the correct subtask id."""
        sub = _make_subtask(status="changes_requested", attempt=0, max_attempts=3)
        state = _make_state(subtasks=[sub], _current_subtask_id=sub["id"])
        result = route_after_pm_review(state)
        assert isinstance(result, list)
        payload = result[0].arg
        assert payload["_current_subtask_id"] == sub["id"]


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
