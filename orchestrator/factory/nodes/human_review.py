"""Human review nodes: interrupt the graph to wait for human input."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def human_review_plan_node(state: FactoryState) -> dict:
    """
    Interrupt point: wait for human to approve or reject the decomposition plan.

    The graph is compiled with ``interrupt_before=["human_review_plan"]``, so
    LangGraph pauses *before* this node runs. The webhook server resumes the
    thread once a ``task.approved`` or ``task.rejected`` event arrives with
    the human's decision stored in ``human_approval_response``.

    Stub: logs and transitions status to 'awaiting_approval'.
    """
    logger.info(
        "human_review_plan: awaiting human approval for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    return {"graph_status": "awaiting_approval"}


def human_review_code_node(state: FactoryState) -> dict:
    """
    Interrupt point: wait for human to approve or reject the final code.

    Same interrupt mechanism as human_review_plan_node.

    Stub: logs and returns an empty update.
    """
    logger.info(
        "human_review_code: awaiting human code review for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    return {}
