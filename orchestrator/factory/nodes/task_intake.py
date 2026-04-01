"""Task intake node: validates and loads the parent task from MeshWiki."""

from __future__ import annotations

import logging

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def task_intake_node(state: FactoryState) -> dict:
    """Validate the incoming task and load full requirements from MeshWiki.

    Fetches the task page via MeshWikiClient, extracts the title from
    metadata and requirements from page content, and transitions graph
    status to 'decomposing'.

    Args:
        state: Current FactoryState; ``task_wiki_page`` must be set.

    Returns:
        Partial state update with ``title``, ``requirements``, and
        ``graph_status`` set to ``"decomposing"``.
    """
    task_wiki_page: str = state.get("task_wiki_page", "")
    logger.info("task_intake: loading task %s", task_wiki_page)

    meshwiki_client = MeshWikiClient()
    page = await meshwiki_client.get_page(task_wiki_page)

    if page is None:
        logger.error("task_intake: task page %s not found", task_wiki_page)
        return {
            "title": task_wiki_page,
            "requirements": "",
            "graph_status": "decomposing",
        }

    metadata: dict = page.get("metadata") or {}
    title: str = metadata.get("title", task_wiki_page)
    requirements: str = page.get("content", "")

    logger.info("task_intake: loaded task '%s'", title)

    return {
        "title": title,
        "requirements": requirements,
        "graph_status": "decomposing",
    }
