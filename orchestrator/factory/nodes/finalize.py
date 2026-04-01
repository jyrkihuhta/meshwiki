"""Finalize node: mark the task as completed and update MeshWiki."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def finalize_node(state: FactoryState) -> dict:
    """
    Mark the parent task as 'done' and record cost/token metrics.

    Stub: logs and transitions graph_status to 'completed'.
    Full implementation will call MeshWikiClient.transition_task() to move
    the task page to 'done' state and persist cost_usd.
    """
    logger.info(
        "finalize: completing task %s (cost: $%.4f)",
        state.get("task_wiki_page", "<unknown>"),
        state.get("cost_usd", 0.0),
    )
    return {"graph_status": "completed"}
