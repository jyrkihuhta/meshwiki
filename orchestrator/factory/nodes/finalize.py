"""Finalize node: mark the task as completed and update MeshWiki."""

import logging

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)

# Ordered steps required to reach "done" from any active parent state.
# The wiki state machine enforces: in_progress → review → merged → done.
_PATH_TO_DONE = ["review", "merged", "done"]


async def finalize_node(state: FactoryState) -> dict:
    """Mark the parent task as 'done' and record cost/token metrics.

    Calls ``MeshWikiClient.transition_task()`` to move the task page to
    ``"done"`` state and persists ``cost_usd`` in the page frontmatter.

    Accumulates all incremental costs from ``incremental_costs_usd`` into
    ``cost_usd`` before recording.

    C7: Requires all subtask PRs to be merged or failed before transitioning
    the parent.  Walks the parent through the full required path
    (review → merged → done) starting from its current status.

    Args:
        state: Current FactoryState after all PRs are confirmed merged.

    Returns:
        Partial state update setting ``graph_status`` to ``"completed"``.
    """
    total_cost = sum(state.get("incremental_costs_usd", []))
    total_cost += state.get("cost_usd", 0.0)

    async with MeshWikiClient() as client:
        task_page = state["task_wiki_page"]

        # C7: guard — don't finalize if any subtask is still pending/in_progress
        subtasks = state.get("subtasks", [])
        pending = [
            s for s in subtasks if s.get("status") not in ("merged", "done", "failed")
        ]
        if pending:
            logger.warning(
                "finalize: %d subtask(s) not yet at a terminal state: %s — skipping",
                len(pending),
                [s["id"] for s in pending],
            )
            return {"graph_status": "completed", "cost_usd": total_cost}

        logger.info(
            "finalize: completing task %s (cost: $%.4f)",
            task_page,
            total_cost,
        )

        # Determine current parent status to know where to start the walk.
        page = await client.get_page(task_page)
        current_status = (page or {}).get("metadata", {}).get("status", "in_progress")
        start = 0
        if current_status in _PATH_TO_DONE:
            start = _PATH_TO_DONE.index(current_status) + 1

        extra_fields = {"cost_usd": str(round(total_cost, 4))}
        for step in _PATH_TO_DONE[start:]:
            try:
                await client.transition_task(
                    task_page,
                    step,
                    extra_fields=extra_fields if step == "done" else None,
                )
                logger.info("finalize: transitioned %s to %s", task_page, step)
            except Exception as exc:
                logger.error(
                    "finalize: failed to transition %s to %s: %s",
                    task_page,
                    step,
                    exc,
                )
                break

        return {"graph_status": "completed", "cost_usd": total_cost}
