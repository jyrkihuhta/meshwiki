"""Tests for the LangGraph routing functions in factory/graph.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
# route_after_grinding
# ---------------------------------------------------------------------------


class TestRouteAfterGrinding:
    def test_all_succeeded_no_pending(self) -> None:
        sub = _make_subtask(status="review")
        state = _make_state(subtasks=[sub])
        assert route_after_grinding(state) == "all_succeeded"

    def test_more_pending_when_pending_remain(self) -> None:
        review_sub = _make_subtask(wiki_page="Task_0042_Sub_01", status="review")
        pending_sub = _make_subtask(wiki_page="Task_0042_Sub_02", status="pending")
        state = _make_state(subtasks=[review_sub, pending_sub])
        assert route_after_grinding(state) == "more_pending"

    def test_all_failed_when_none_succeeded(self) -> None:
        sub = _make_subtask(status="failed")
        state = _make_state(subtasks=[sub])
        assert route_after_grinding(state) == "all_failed"

    def test_some_failed_when_mix(self) -> None:
        failed_sub = _make_subtask(wiki_page="Task_0042_Sub_01", status="failed")
        review_sub = _make_subtask(wiki_page="Task_0042_Sub_02", status="review")
        state = _make_state(subtasks=[failed_sub, review_sub])
        assert route_after_grinding(state) == "some_failed"

    def test_changes_requested_treated_as_pending(self) -> None:
        review_sub = _make_subtask(wiki_page="Task_0042_Sub_01", status="review")
        rework_sub = _make_subtask(wiki_page="Task_0042_Sub_02", status="changes_requested")
        state = _make_state(subtasks=[review_sub, rework_sub])
        assert route_after_grinding(state) == "more_pending"


# ---------------------------------------------------------------------------
# route_after_pm_review
# ---------------------------------------------------------------------------


class TestRouteAfterPmReview:
    def test_escalate_when_attempts_exhausted(self) -> None:
        sub = _make_subtask(status="changes_requested", attempt=3, max_attempts=3)
        state = _make_state(subtasks=[sub])
        assert route_after_pm_review(state) == "escalate"

    def test_changes_requested_when_retries_remain(self) -> None:
        sub = _make_subtask(status="changes_requested", attempt=1, max_attempts=3)
        state = _make_state(subtasks=[sub])
        assert route_after_pm_review(state) == "changes_requested"

    def test_skip_human_review_when_auto_merge_enabled(self) -> None:
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub])
        mock_settings = MagicMock()
        mock_settings.auto_merge = True
        with patch("factory.config.get_settings", return_value=mock_settings):
            assert route_after_pm_review(state) == "skip_human_review"

    def test_all_approved_when_auto_merge_disabled(self) -> None:
        sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[sub])
        mock_settings = MagicMock()
        mock_settings.auto_merge = False
        with patch("factory.config.get_settings", return_value=mock_settings):
            assert route_after_pm_review(state) == "all_approved"


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
