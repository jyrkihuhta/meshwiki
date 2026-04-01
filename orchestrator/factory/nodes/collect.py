"""Collect results node: gather outcomes from all grinder instances."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def collect_results_node(state: FactoryState) -> dict:
    """
    Collect outcomes from all parallel grinder runs after fan-out.

    Stub: logs and transitions status to 'reviewing'.
    Full implementation will aggregate subtask statuses from MeshWiki pages,
    update completed_subtask_ids and failed_subtask_ids accordingly.
    """
    logger.info(
        "collect_results: collecting results for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    return {"graph_status": "reviewing"}
