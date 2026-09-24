"""Escalate node: handle unrecoverable failures and decide next action."""

import logging
import math

from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)

# Maximum number of redecompose rounds before giving up.
MAX_REDECOMPOSE_ATTEMPTS = 2


async def escalate_node(state: FactoryState) -> dict:
    """Handle failed or stuck subtasks by deciding to retry, redecompose, or abandon.

    Decision logic:
    - ``"retry"``       — at least one failed subtask still has attempts left.
    - ``"redecompose"`` — majority failed with no retries, and we haven't hit
                          ``MAX_REDECOMPOSE_ATTEMPTS`` yet; routes back to
                          ``decompose_node`` with failure context for the PM.
    - ``"abandon"``     — all retries exhausted OR redecompose cap reached.

    On ``"redecompose"``:
    - All non-skipped subtasks are marked ``"skipped"`` so they are excluded
      from the next majority count and don't block ``finalize_node``.
    - ``redecompose_context`` is set so the PM knows what went wrong.
    - ``redecompose_attempt`` is incremented.

    Args:
        state: Current FactoryState after one or more grinders have failed.

    Returns:
        Partial state update with updated subtasks, graph_status, and
        escalation_decision.
    """
    async with MeshWikiClient() as client:
        failed_id_set = set(state.get("failed_subtask_ids") or [])
        all_subtasks = state.get("subtasks") or []

        # Only consider subtasks that are CURRENTLY failed.  Subtasks marked
        # "skipped" by a prior redecompose round are excluded so they don't
        # distort the majority count or retryability check.
        failed_subtasks = [
            s for s in all_subtasks
            if s["id"] in failed_id_set and s["status"] == "failed"
        ]
        active_subtasks = [s for s in all_subtasks if s["status"] != "skipped"]

        logger.warning(
            "escalate: task=%s failed=%d active=%d",
            state.get("task_wiki_page", "<unknown>"),
            len(failed_subtasks),
            len(active_subtasks),
        )

        retriable = [s for s in failed_subtasks if s["attempt"] < s["max_attempts"] - 1]
        redecompose_attempt: int = state.get("redecompose_attempt") or 0

        # ── Compute decision ─────────────────────────────────────────────────
        error_context: str | None = None
        redecompose_context: str | None = None
        new_redecompose_attempt = redecompose_attempt

        if retriable:
            decision = "retry"
        elif len(failed_subtasks) >= math.ceil(max(1, len(active_subtasks)) / 2):
            if redecompose_attempt >= MAX_REDECOMPOSE_ATTEMPTS:
                decision = "abandon"
                error_context = (
                    f"Majority of subtasks failed and redecompose cap "
                    f"({MAX_REDECOMPOSE_ATTEMPTS}) reached — abandoning task."
                )
            else:
                decision = "redecompose"
                failed_summary = ", ".join(s["id"] for s in failed_subtasks)
                error_details = "\n".join(
                    f"- {s['id']}: {(s.get('error_log') or ['(no log)'])[-1][:300]}"
                    for s in failed_subtasks
                )
                error_context = (
                    f"Majority of subtasks failed "
                    f"({len(failed_subtasks)}/{len(active_subtasks)}): "
                    f"{failed_summary}. Requesting fresh decomposition."
                )
                redecompose_context = (
                    f"The previous decomposition failed. Failure summary:\n"
                    f"{error_details}\n\n"
                    f"Please produce a significantly different decomposition that "
                    f"addresses the root cause of these failures. Consider smaller, "
                    f"more atomic subtasks or a different technical approach."
                )
                new_redecompose_attempt = redecompose_attempt + 1
        else:
            decision = "abandon"

        logger.info(
            "escalate: decision=%s (redecompose_attempt=%d)", decision, redecompose_attempt
        )

        # ── Write escalation note to wiki ────────────────────────────────────
        try:
            note = (
                f"## Escalation (round {redecompose_attempt + 1})\n\n"
                f"Failed: {', '.join(s['id'] for s in failed_subtasks)}\n"
                f"Decision: **{decision}**\n"
            )
            if decision == "retry":
                note += f"Retrying: {', '.join(s['id'] for s in retriable)}\n"
            elif decision == "redecompose":
                note += "Requesting a fresh decomposition from the PM.\n"
            await client.append_to_page(state["task_wiki_page"], note)
        except Exception as exc:
            logger.error("escalate: failed to update task page: %s", exc)

        # ── Transition retriable subtasks back to in_progress ────────────────
        for s in retriable:
            try:
                await client.transition_task(s["wiki_page"], "in_progress")
                logger.info("escalate: transitioned %s to in_progress for retry", s["id"])
            except Exception as exc:
                logger.warning("escalate: could not transition %s: %s", s["id"], exc)

        # ── Build updated subtask list ───────────────────────────────────────
        retriable_ids = {s["id"] for s in retriable}

        if decision == "redecompose":
            # Mark every non-skipped subtask as skipped — fresh slate for the
            # new decomposition.  The new subtasks will have different IDs.
            updated_subtasks = [
                {**s, "status": "skipped"} if s["status"] != "skipped" else s
                for s in all_subtasks
            ]
        else:
            updated_subtasks = [
                {**s, "attempt": s["attempt"] + 1, "status": "pending"}
                if s["id"] in retriable_ids
                else s
                for s in all_subtasks
            ]

        result: dict = {
            "subtasks": updated_subtasks,
            "graph_status": "escalated",
            "escalation_decision": decision,
            "redecompose_attempt": new_redecompose_attempt,
        }
        if error_context is not None:
            result["error"] = error_context
        if redecompose_context is not None:
            result["redecompose_context"] = redecompose_context
        return result
