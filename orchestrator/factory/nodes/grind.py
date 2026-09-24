"""Grind node: grinder agent implements a single subtask."""

from __future__ import annotations

import logging

from ..agents.grinder_agent import grind_subtask
from ..config import get_settings
from ..hbr import get_hbr
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def grind_node(state: FactoryState) -> dict:
    """Run the grinder agent for the current subtask.

    Looks up the subtask identified by ``_current_subtask_id`` in state,
    invokes the grinder agentic loop, and returns a partial state update
    with the updated subtasks list and ``active_grinders``.

    ``active_grinders`` is updated by appending the current subtask ID.  The
    ``_merge_active_grinders`` reducer unions these single-element additions
    across parallel branches so no concurrent write clobbers another branch's
    update.  ``_current_subtask_id`` is echoed back so the routing function can
    read it after the state merge; its Annotated reducer allows parallel branches
    to write concurrently without raising INVALID_CONCURRENT_GRAPH_UPDATE.

    Args:
        state: Current FactoryState, must contain ``_current_subtask_id``.

    Returns:
        Partial state update with ``subtasks`` list where the current
        subtask is replaced by the updated version from the grinder, and
        ``active_grinders`` updated to include the current subtask ID.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state["subtasks"] if s["id"] == subtask_id),
        None,
    )
    if subtask is None:
        logger.error("grind_node: subtask %r not found in state", subtask_id)
        return {}

    logger.info(
        "grind_node: running grinder for subtask %s (task %s)",
        subtask_id,
        state.get("task_wiki_page", "<unknown>"),
    )

    async with MeshWikiClient() as meshwiki_client:
        if subtask.get("attempt", 0) > 0 and not subtask.get("review_feedback"):
            logger.warning(
                "grind_node: subtask %s is a rework (attempt %d) but review_feedback is empty — failing fast",
                subtask_id,
                subtask.get("attempt", 0),
            )
            error_log = list(subtask.get("error_log") or []) + [
                "PM requested changes but provided no feedback — cannot rework"
            ]
            updated = {**subtask, "status": "failed", "error_log": error_log}
            try:
                await meshwiki_client.transition_task(subtask["wiki_page"], "failed")
            except Exception as exc:
                logger.error(
                    "grind_node: failed to transition %s to failed: %s",
                    subtask["wiki_page"],
                    exc,
                )
            return {
                "subtasks": [updated],
                "active_grinders": [subtask_id],
                "_current_subtask_id": subtask_id,
            }

        result = await grind_subtask(state, subtask, meshwiki_client)
        updated = result["subtask"]
        incremental_cost = result.get("incremental_cost_usd", 0.0)
        if incremental_cost > 0:
            get_hbr().record_cost(get_settings().grinder_model, incremental_cost)

        final_status = updated.get("status", "failed")
        extra_fields: dict = {}
        if updated.get("pr_url"):
            extra_fields["pr_url"] = updated["pr_url"]
        if updated.get("branch_name"):
            extra_fields["branch"] = updated["branch_name"]
        try:
            await meshwiki_client.transition_task(
                updated["wiki_page"], final_status, extra_fields or None
            )
            logger.info(
                "grind_node: transitioned %s to %s (pr=%s)",
                updated["wiki_page"],
                final_status,
                updated.get("pr_url"),
            )
        except Exception as exc:
            logger.error(
                "grind_node: failed to transition %s to %s: %s",
                updated["wiki_page"],
                final_status,
                exc,
            )

    logger.info("grind_node: completed subtask %s", subtask_id)
    return {
        "subtasks": [updated],
        "incremental_costs_usd": [incremental_cost],
        "active_grinders": [subtask_id],
        "_current_subtask_id": subtask_id,
    }
