"""Finalize node: mark the task as completed and update MeshWiki."""

import logging

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def finalize_node(state: FactoryState) -> dict:
    """Mark the parent task as 'done' and record cost/token metrics.

    Calls ``MeshWikiClient.transition_task()`` to move the task page to
    ``"done"`` state and persists ``cost_usd`` in the page frontmatter.

    Accumulates all incremental costs from ``incremental_costs_usd`` into
    ``cost_usd`` before recording.

    Args:
        state: Current FactoryState after all PRs are confirmed merged.

    Returns:
        Partial state update setting ``graph_status`` to ``"completed"``.
    """
    total_cost = sum(state.get("incremental_costs_usd", []))
    total_cost += state.get("cost_usd", 0.0)

    async with MeshWikiClient() as client:
        logger.info(
            "finalize: completing task %s (cost: $%.4f)",
            state.get("task_wiki_page", "<unknown>"),
            total_cost,
        )

        task_page = state["task_wiki_page"]

        try:
            await client.transition_task(
                task_page,
                "done",
                extra_fields={"cost_usd": str(round(total_cost, 4))},
            )
        except Exception as exc:
            logger.error(
                "finalize: failed to transition %s to done: %s",
                task_page,
                exc,
            )

        return {"graph_status": "completed"}
