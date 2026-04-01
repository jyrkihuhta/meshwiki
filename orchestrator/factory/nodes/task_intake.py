"""Task intake node: validates and loads the parent task from MeshWiki."""

import logging

from ..state import FactoryState

logger = logging.getLogger(__name__)


def task_intake_node(state: FactoryState) -> dict:
    """
    Validate the incoming task and load full requirements from MeshWiki.

    Stub: logs the task page name and transitions status to 'decomposing'.
    Full implementation will call MeshWikiClient.get_page() and populate
    `requirements` from the page content.
    """
    logger.info(
        "task_intake: loading task %s", state.get("task_wiki_page", "<unknown>")
    )
    return {"graph_status": "decomposing"}
