"""PM review node: PM agent reviews grinder-produced code."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def pm_review_node(state: FactoryState) -> dict:
    """
    Call the PM agent to review code produced by grinders.

    Stub: logs and returns an empty update.
    Full implementation will fetch PR diffs via GitHubClient and invoke
    pm_agent.review_prs() to approve or request changes on each subtask.
    """
    logger.info(
        "pm_review: reviewing results for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    return {}
