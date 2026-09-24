"""Collect results node: gather outcomes from all grinder instances."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


async def collect_results_node(state: FactoryState) -> dict:
    """Collect outcomes from all parallel grinder runs after fan-out.

    Scans ``state["subtasks"]`` and tallies which subtasks completed
    successfully (status ``"review"`` or ``"merged"``) and which failed
    (status ``"failed"``).  Updates ``completed_subtask_ids``,
    ``failed_subtask_ids``, and transitions ``graph_status`` to
    ``"reviewing"``.

    Args:
        state: Current FactoryState after all grind nodes have run.

    Returns:
        Partial state update with completed/failed subtask id lists and
        updated graph_status.
    """
    logger.info(
        "collect_results: collecting results for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )
    completed = [
        s["id"] for s in state["subtasks"] if s["status"] in ("review", "merged")
    ]
    failed = [s["id"] for s in state["subtasks"] if s["status"] == "failed"]
    logger.info(
        "collect_results: completed=%s failed=%s",
        completed,
        failed,
    )
    return {
        "completed_subtask_ids": completed,
        "failed_subtask_ids": failed,
        "graph_status": "reviewing",
        "active_grinders": [],
    }
