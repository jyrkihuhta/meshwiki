"""Merge check node: verify all PRs have been merged before finalization."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def merge_check_node(state: FactoryState) -> dict:
    """
    Confirm that all subtask PRs have been merged into the main branch.

    Stub: logs and returns an empty update.
    Full implementation will poll GitHub (or react to ``task.pr_merged``
    webhook events) until all subtask PRs are in 'merged' state.
    """
    logger.info(
        "merge_check: verifying PR merges for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    return {}
