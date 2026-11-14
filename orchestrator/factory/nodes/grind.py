"""Grind node: grinder agent implements a single subtask."""

from __future__ import annotations

import logging
from typing import Literal

from ..agents.grinder_agent import grind_subtask
from ..config import get_settings
from ..hbr import get_hbr
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


def next_step_after_grind(
    subtask: SubTask,
) -> Literal["pm_review", "pm_diagnose", "escalate"]:
    """Decide where a subtask goes once its grinder has returned.

    Single source of truth shared by ``grind_node`` (which has to know whether
    the subtask's branch ends here) and ``route_after_grinding`` in
    ``factory.graph`` (which does the actual routing).

    Args:
        subtask: The subtask as updated by the grinder.

    Returns:
        ``"pm_review"`` when the grinder produced something to review,
        ``"pm_diagnose"`` on a first failure (the PM rewrites the task and the
        subtask is ground again), ``"escalate"`` on any later failure.
    """
    if subtask["status"] != "failed":
        return "pm_review"
    if subtask.get("attempt", 0) == 0:
        return "pm_diagnose"
    return "escalate"


def _grind_result(
    subtask_id: str, updated: SubTask, incremental_cost: float | None = None
) -> dict:
    """Build ``grind_node``'s partial state update for the ground subtask.

    A subtask stays in ``active_grinders`` while its branch continues to
    ``pm_review`` or ``pm_diagnose``.  When the branch is handed to
    ``escalate`` it ends here, so the ID is removed.
    """
    result: dict = {"subtasks": [updated], "_current_subtask_id": subtask_id}
    if incremental_cost is not None:
        result["incremental_costs_usd"] = [incremental_cost]
    if next_step_after_grind(updated) == "escalate":
        result["active_grinders"] = {"remove": [subtask_id]}
    return result


async def grind_node(state: FactoryState) -> dict:
    """Run the grinder agent for the current subtask.

    Looks up the subtask identified by ``_current_subtask_id`` in state,
    invokes the grinder agentic loop, and returns a partial state update
    with the updated subtask.

    ``active_grinders`` is not touched while the subtask's branch continues
    (to ``pm_review``, or to ``pm_diagnose`` on a first failure): the subtask
    was marked in flight when ``assign_grinders_node`` dispatched it and stays
    so until its branch lands.  Only when the branch ends here — a failure that
    goes straight to ``escalate`` — is the ID removed.  ``_current_subtask_id``
    is echoed back so the routing function can read it after the state merge;
    its Annotated reducer allows parallel branches to write concurrently
    without raising INVALID_CONCURRENT_GRAPH_UPDATE.

    Args:
        state: Current FactoryState, must contain ``_current_subtask_id``.

    Returns:
        Partial state update with a single-element ``subtasks`` list holding
        the updated subtask, plus an ``active_grinders`` removal when the
        subtask is being escalated.
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
            return _grind_result(subtask_id, updated)

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
    return _grind_result(subtask_id, updated, incremental_cost)
