"""Human review nodes: interrupt the graph to wait for human input."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


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
