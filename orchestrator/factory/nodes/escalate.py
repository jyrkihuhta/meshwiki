"""Escalate node: handle unrecoverable failures and decide next action."""

import logging

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def escalate_node(state: FactoryState) -> dict:
    """Handle failed or stuck subtasks by deciding to retry, redecompose, or abandon.

    Checks whether any failed subtask has retries remaining.  If so, the
    decision is ``"retry"`` and the attempt counters are incremented so the
    subtasks are re-dispatched by ``assign_grinders``.  If all retries are
    exhausted the decision is ``"abandon"`` and the graph run ends.

    Also appends an escalation note to the parent task wiki page.

    The routing function ``route_after_escalation`` reads
    ``escalation_decision`` from state to choose the next step:
      - ``"retry"``       → re-dispatch the failed subtasks via assign_grinders
      - ``"redecompose"`` → return to the decompose node for a fresh plan
      - ``"abandon"``     → end the graph run (default)

    Args:
        state: Current FactoryState after collect_results detected failures.

    Returns:
        Partial state update with updated ``subtasks``, ``graph_status``, and
        ``escalation_decision``.
    """
    client = MeshWikiClient()

    failed_ids = state.get("failed_subtask_ids", [])
    failed_subtasks = [s for s in state["subtasks"] if s["id"] in failed_ids]

    logger.warning(
        "escalate: escalating task %s (failed subtasks: %s)",
        state.get("task_wiki_page", "<unknown>"),
        failed_ids,
    )

    # Check which failed subtasks still have retries remaining
    retriable = [s for s in failed_subtasks if s["attempt"] < s["max_attempts"] - 1]

    # Append escalation note to the task wiki page
    try:
        page = await client.get_page(state["task_wiki_page"])
        if page:
            content = page.get("content", "")
            note = f"\n\n## Escalation\n\nFailed subtasks: {', '.join(failed_ids)}\n"
            if retriable:
                note += f"Retrying: {', '.join(s['id'] for s in retriable)}\n"
            await client.create_page(state["task_wiki_page"], content + note)
    except Exception as exc:
        logger.error("escalate: failed to update task page: %s", exc)

    # Transition retriable subtask wiki pages back to in_progress so that
    # grind_node can later transition them to review (failed->review is invalid).
    for s in retriable:
        try:
            await client.transition_task(s["wiki_page"], "in_progress")
            logger.info("escalate: transitioned %s back to in_progress for retry", s["id"])
        except Exception as exc:
            logger.warning(
                "escalate: could not transition %s to in_progress: %s", s["id"], exc
            )

    # Increment attempt count on retriable failed subtasks and reset status
    retriable_ids = {s["id"] for s in retriable}
    subtasks = []
    for s in state["subtasks"]:
        if s["id"] in retriable_ids:
            subtasks.append({**s, "attempt": s["attempt"] + 1, "status": "pending"})
        else:
            subtasks.append(s)

    decision = "retry" if retriable else "abandon"
    logger.info("escalate: decision=%s", decision)

    return {
        "subtasks": subtasks,
        "graph_status": "escalated",
        "escalation_decision": decision,
    }
