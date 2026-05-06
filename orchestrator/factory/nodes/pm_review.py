"""PM review node: PM agent reviews grinder-produced code."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..agents.pm_agent import review_with_pm
from ..config import get_settings
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState, SubTask
from .validate_armory import ARMORY_TYPES, validate_armory_pr_files

logger = logging.getLogger(__name__)


async def pm_review_node(state: FactoryState) -> dict:
    """Call the PM agent to review a single grinder-produced subtask.

    Reads ``_current_subtask_id`` from state (injected by ``Send()`` in
    ``route_after_grinding``) to identify which subtask to review.  Reviews
    only that one subtask and returns a delta update so parallel branches do
    not clobber each other.

    Args:
        state: Current FactoryState, must contain ``_current_subtask_id``.

    Returns:
        Partial state update with the updated single-element ``subtasks``
        list and the incremental cost delta.
    """
    subtask_id = state.get("_current_subtask_id")
    subtask = next(
        (s for s in state.get("subtasks", []) if s["id"] == subtask_id),
        None,
    )

    if subtask is None:
        logger.error("pm_review: _current_subtask_id %r not found in state", subtask_id)
        return {}

    logger.info(
        "pm_review: reviewing subtask %s (%s) for task %s",
        subtask["id"],
        subtask["title"],
        state.get("task_wiki_page", "<unknown>"),
    )

    settings = get_settings()
    if settings.dry_run:
        return await _pm_review_dry_run(state, subtask, settings.dry_run_step_delay_seconds)

    task_repo: str | None = state.get("task_repo") or None
    async with MeshWikiClient() as meshwiki_client, GitHubClient(repo=task_repo) as github_client:
        incremental_cost: float = 0.0

        try:
            result = await review_with_pm(
                state, subtask, meshwiki_client, github_client
            )
        except Exception as exc:
            logger.error(
                "pm_review: review failed for subtask %s: %s", subtask["id"], exc
            )
            # Mark as failed so route_after_pm_review escalates rather than
            # silently auto-approving unreviewed code.
            updated_subtask = SubTask(
                **{**subtask, "status": "failed", "review_feedback": str(exc)}
            )
            try:
                await meshwiki_client.transition_task(subtask["wiki_page"], "failed")
            except Exception as transition_exc:
                logger.warning(
                    "pm_review: failed to transition %s to failed: %s",
                    subtask["wiki_page"],
                    transition_exc,
                )
            return {
                "subtasks": [updated_subtask],
                "incremental_costs_usd": [0.0],
                "_current_subtask_id": subtask_id,
            }

        decision: str = result.get("decision", "changes_requested")
        feedback: str | None = result.get("feedback")
        incremental_cost = result.get("incremental_cost_usd", 0.0)

        # ── Pre-merge armory gate ────────────────────────────────────────
        # When the PM approves an armory-artifact PR, run the deterministic
        # validator against the PR's changed files BEFORE auto-merging.
        # If the artifact is structurally broken (wrong mode vocabulary,
        # bare-string mutations, missing required fields, forbidden imports
        # in tools), downgrade the decision to changes_requested so the
        # broken file never lands in the armory. The post-merge
        # validate_armory_node remains as defense in depth.
        if decision == "approved":
            artifact_type: str | None = state.get("artifact_type")
            if artifact_type in ARMORY_TYPES:
                pre_pr_number = subtask.get("pr_number") or _extract_pr_number(
                    subtask.get("pr_url") or ""
                )
                if pre_pr_number:
                    try:
                        pr_files = await github_client.get_pr_files(pre_pr_number)
                        gate_errors = validate_armory_pr_files(pr_files, artifact_type)
                    except Exception as exc:
                        logger.warning(
                            "pm_review: pre-merge validation fetch failed for PR "
                            "#%d (%s) — letting post-merge gate handle it",
                            pre_pr_number,
                            exc,
                        )
                        gate_errors = []
                    if gate_errors:
                        gate_feedback = (
                            "Armory pre-merge validation failed — "
                            "PR will not be merged:\n"
                            + "\n".join(f"- {e}" for e in gate_errors)
                            + "\n\nFix the schema violations above and re-push."
                        )
                        logger.info(
                            "pm_review: pre-merge gate REJECTED PR #%d for subtask "
                            "%s — overriding approved → changes_requested",
                            pre_pr_number,
                            subtask["id"],
                        )
                        try:
                            await github_client.request_changes(
                                pre_pr_number, gate_feedback
                            )
                        except Exception as exc:
                            logger.warning(
                                "pm_review: failed to post pre-merge review on PR "
                                "#%d: %s",
                                pre_pr_number,
                                exc,
                            )
                        decision = "changes_requested"
                        feedback = gate_feedback

        if decision == "approved":
            if get_settings().auto_merge and subtask.get("pr_url"):
                pr_number = subtask.get("pr_number") or _extract_pr_number(
                    subtask["pr_url"]
                )
                if pr_number:
                    try:
                        await github_client.merge_pr(pr_number)
                        logger.info(
                            "pm_review: auto-merged PR #%d for subtask %s",
                            pr_number,
                            subtask["id"],
                        )
                        try:
                            await meshwiki_client.transition_task(
                                subtask["wiki_page"], "merged"
                            )
                        except Exception as exc:
                            logger.warning(
                                "pm_review: failed to transition %s to merged: %s",
                                subtask["wiki_page"],
                                exc,
                            )
                    except Exception as exc:
                        logger.warning(
                            "pm_review: auto-merge failed for PR #%d: %s",
                            pr_number,
                            exc,
                        )
            updated_subtask = SubTask(
                **{**subtask, "status": "merged", "review_feedback": feedback}
            )
            logger.info("pm_review: subtask %s approved", subtask["id"])
        else:
            updated_subtask = SubTask(
                **{
                    **subtask,
                    "status": "changes_requested",
                    "review_feedback": feedback,
                    "attempt": subtask.get("attempt", 0) + 1,
                }
            )
            logger.info(
                "pm_review: subtask %s changes_requested: %s",
                subtask["id"],
                feedback,
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        subtask_title: str = subtask.get("title") or subtask["id"]

        if decision == "approved":
            pm_section = (
                f"## PM Review — {timestamp}\n\n"
                f"✅ **Approved**\n\n"
                f"{feedback or 'Looks good.'}\n"
            )
            parent_log_entry = (
                f"### PM Review — {subtask_title} ({timestamp})\n\n"
                f"**Decision:** ✅ Approved\n\n"
                f"**Feedback:** {feedback or 'Looks good.'}\n"
            )
        else:
            attempt_num: int = updated_subtask.get("attempt", 1)
            pm_section = (
                f"## PM Review — {timestamp}\n\n"
                f"❌ **Changes requested**\n\n"
                f"{feedback or 'See comments above.'}\n"
            )
            parent_log_entry = (
                f"### PM Review — {subtask_title} ({timestamp})\n\n"
                f"**Decision:** ❌ Changes requested\n\n"
                f"**Feedback:** {feedback or 'See comments above.'}\n\n"
                f"**Attempt:** {attempt_num}\n"
            )

        fm_updates: dict | None = None
        if decision != "approved":
            fm_updates = {"rework_count": updated_subtask.get("attempt", 1)}
        try:
            await meshwiki_client.append_to_page(
                subtask["wiki_page"], pm_section, frontmatter_updates=fm_updates
            )
        except Exception as exc:
            logger.warning("pm_review: failed to append review to wiki: %s", exc)

        task_wiki_page: str | None = state.get("task_wiki_page")
        if task_wiki_page:
            try:
                await meshwiki_client.append_to_page(task_wiki_page, parent_log_entry)
            except Exception as exc:
                logger.warning(
                    "pm_review: failed to append review log to parent task %s: %s",
                    task_wiki_page,
                    exc,
                )

        if decision != "approved":
            try:
                await meshwiki_client.transition_task(
                    subtask["wiki_page"], "in_progress"
                )
            except Exception as exc:
                logger.warning(
                    "pm_review: failed to transition %s back to in_progress: %s",
                    subtask["wiki_page"],
                    exc,
                )

    return {
        "subtasks": [updated_subtask],
        "incremental_costs_usd": [incremental_cost],
        "_current_subtask_id": subtask_id,
    }


async def _pm_review_dry_run(
    state: FactoryState,
    subtask: SubTask,
    delay: float,
) -> dict:
    """Auto-approve a subtask without calling the PM agent."""
    subtask_id = subtask["id"]
    logger.info(
        "pm_review: DRY RUN — auto-approving subtask %s (delay=%.1fs)",
        subtask_id,
        delay,
    )
    await asyncio.sleep(delay)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pm_section = (
        f"## PM Review — {timestamp}\n\n"
        f"✅ **Approved** _(dry-run — no actual review performed)_\n"
    )

    updated_subtask = SubTask(**{**subtask, "status": "merged", "review_feedback": None})

    async with MeshWikiClient() as meshwiki_client:
        try:
            await meshwiki_client.append_to_page(subtask["wiki_page"], pm_section)
        except Exception as exc:
            logger.warning("pm_review dry-run: failed to append review note: %s", exc)

    return {
        "subtasks": [updated_subtask],
        "incremental_costs_usd": [0.0],
        "_current_subtask_id": subtask_id,
    }
