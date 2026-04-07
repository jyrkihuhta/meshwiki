"""Finalize node: mark the task as completed and update MeshWiki."""

import logging

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def finalize_node(state: FactoryState) -> dict:
    """Mark the parent task as 'done' and record cost/token metrics.

    Calls ``MeshWikiClient.transition_task()`` to move the task page to
    ``"done"`` state and persists ``cost_usd`` in the page frontmatter.

    Args:
        state: Current FactoryState after all PRs are confirmed merged.

    Returns:
        Partial state update setting ``graph_status`` to ``"completed"``.
    """
    client = MeshWikiClient()

    logger.info(
        "finalize: completing task %s (cost: $%.4f)",
        state.get("task_wiki_page", "<unknown>"),
        state.get("cost_usd", 0.0),
    )

    task_page = state["task_wiki_page"]

    try:
        await client.transition_task(
            task_page,
            "done",
            extra_fields={"cost_usd": str(round(state.get("cost_usd", 0), 4))},
        )
    except Exception as exc:
        logger.error(
            "finalize: failed to transition %s to done: %s",
            task_page,
            exc,
        )

    # Move completed task into a Done/ subfolder so it collapses in the sidebar
    # e.g. Factory/Macros/NewPageMacro → Factory/Macros/Done/NewPageMacro
    if "/Done/" not in task_page:
        parts = task_page.split("/")
        done_page = "/".join(parts[:-1] + ["Done", parts[-1]])
        try:
            await client.rename_page(task_page, done_page)
            logger.info("finalize: moved %s → %s", task_page, done_page)
        except Exception as exc:
            logger.warning("finalize: failed to move task to Done folder: %s", exc)

    return {"graph_status": "completed"}
