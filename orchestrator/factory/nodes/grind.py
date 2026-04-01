"""Grind node: grinder agent implements a single subtask."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def grind_node(state: FactoryState) -> dict:
    """
    Run the grinder agent for the current subtask.

    Stub: logs the subtask ID and returns an empty update.
    Full implementation will invoke grinder_agent.run_grinder_session() which
    creates a branch, writes code, commits, and opens a PR.
    """
    subtask_id = state.get("_current_subtask_id", "<unknown>")
    logger.info(
        "grind: running grinder for subtask %s (task %s)",
        subtask_id,
        state.get("task_wiki_page", "<unknown>"),
    )
    return {}
