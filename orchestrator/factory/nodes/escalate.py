"""Escalate node: handle unrecoverable failures and decide next action."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def escalate_node(state: FactoryState) -> dict:
    """
    Handle failed or stuck subtasks by deciding to retry, redecompose, or abandon.

    The routing function ``route_after_escalation`` reads ``escalation_decision``
    from state to choose the next step:
      - ``"retry"``       → re-dispatch the failed subtasks via assign_grinders
      - ``"redecompose"`` → return to the decompose node for a fresh plan
      - ``"abandon"``     → end the graph run (default)

    Stub: logs, sets graph_status to 'escalated', and defaults to 'abandon'.
    Full implementation will notify the PM agent and potentially a human
    operator before deciding.
    """
    logger.warning(
        "escalate: escalating task %s (failed subtasks: %s)",
        state.get("task_wiki_page", "<unknown>"),
        state.get("failed_subtask_ids", []),
    )
    return {"graph_status": "escalated", "escalation_decision": "abandon"}
