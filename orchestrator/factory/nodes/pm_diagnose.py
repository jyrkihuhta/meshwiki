"""PM diagnose node: PM agent diagnoses a grinder failure and rewrites the task."""

from __future__ import annotations

import logging

from ..agents.pm_agent import diagnose_with_pm
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def pm_diagnose_node(state: FactoryState) -> dict:
    """Call the PM to diagnose a failed subtask and rewrite its description.

    Reads the terminal log from the subtask wiki page, asks the PM to diagnose
    the failure and produce a revised description, then updates the subtask in
    state so the next grind attempt uses the improved instructions.

    Only called on the first failure (attempt == 0).  Subsequent failures route
    directly to escalate.

    Args:
        state: Current FactoryState, must contain ``_current_subtask_id``.

    Returns:
        Partial state update with the revised subtask and incremental cost.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state["subtasks"] if s["id"] == subtask_id),
        None,
    )

    if subtask is None:
        logger.error("pm_diagnose: subtask %r not found in state", subtask_id)
        return {}

    logger.info(
        "pm_diagnose: diagnosing failure for subtask %s (attempt %d)",
        subtask_id,
        subtask.get("attempt", 0),
    )

    async with MeshWikiClient() as meshwiki_client:
        # Read terminal log from wiki page content
        page = await meshwiki_client.get_page(subtask["wiki_page"])
        terminal_log = ""
        if page:
            content = page.get("content", "")
            # Extract text inside the terminal log details block if present
            marker = "## Terminal Log"
            if marker in content:
                terminal_log = content[content.index(marker):]
            else:
                terminal_log = content

        result = await diagnose_with_pm(subtask, terminal_log, meshwiki_client)

    revised_description = result["revised_description"]
    incremental_cost = result["incremental_cost_usd"]

    if revised_description:
        updated = {
            **subtask,
            "description": revised_description,
            "status": "pending",
            "attempt": subtask.get("attempt", 0) + 1,
        }
        logger.info(
            "pm_diagnose: revised description for %s — retrying (attempt %d)",
            subtask_id,
            updated["attempt"],
        )
    else:
        logger.warning(
            "pm_diagnose: PM returned empty diagnosis for %s — escalating", subtask_id
        )
        updated = {**subtask}

    return {
        "subtasks": [updated],
        "incremental_costs_usd": [incremental_cost],
        "_current_subtask_id": subtask_id,
    }
