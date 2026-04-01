"""Decompose node: PM agent breaks the task into subtasks."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def decompose_node(state: FactoryState) -> dict:
    """
    Call the PM agent to decompose the parent task into subtasks.

    Stub: logs and transitions to 'awaiting_approval'.
    Full implementation will invoke pm_agent.decompose_with_pm() and write
    subtask pages to MeshWiki via MeshWikiClient.
    """
    logger.info(
        "decompose: decomposing task %s", state.get("task_wiki_page", "<unknown>")
    )
    return {"graph_status": "awaiting_approval", "subtasks": state.get("subtasks", [])}
