"""PM review node: PM agent reviews grinder-produced code."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..agents.pm_agent import review_with_pm
from ..config import get_settings
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


async def pm_review_node(state: FactoryState) -> dict:
    """Call the PM agent to review code produced by grinders.

    Iterates over all subtasks in 'review' status, calls the PM agent to
    approve or request changes, and updates each subtask's status accordingly.

    Args:
        state: Current FactoryState.

    Returns:
        Partial state update with the updated ``subtasks`` list.
    """
    logger.info(
        "pm_review: reviewing results for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )

    meshwiki_client = MeshWikiClient()
    github_client = GitHubClient()

    subtasks: list[SubTask] = list(state.get("subtasks", []))
    review_subtasks = [s for s in subtasks if s["status"] == "review"]

    logger.info("pm_review: %d subtask(s) in review status", len(review_subtasks))

    updated_subtasks: list[SubTask] = []
    for subtask in subtasks:
        if subtask["status"] != "review":
            updated_subtasks.append(subtask)
            continue

        logger.info(
            "pm_review: reviewing subtask %s (%s)", subtask["id"], subtask["title"]
        )
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
            updated_subtasks.append(updated_subtask)
            try:
                await meshwiki_client.transition_task(subtask["wiki_page"], "failed")
            except Exception as transition_exc:
                logger.warning(
                    "pm_review: failed to transition %s to failed: %s",
                    subtask["wiki_page"],
                    transition_exc,
                )
            continue

        decision: str = result.get("decision", "changes_requested")
        feedback: str | None = result.get("feedback")

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
                }
            )
            logger.info(
                "pm_review: subtask %s changes_requested: %s", subtask["id"], feedback
            )

        updated_subtasks.append(updated_subtask)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")

        if decision == "approved":
            pm_section = (
                f"## PM Review — {timestamp}\n\n"
                f"✅ **Approved**\n\n"
                f"{feedback or 'Looks good.'}\n"
            )
        else:
            pm_section = (
                f"## PM Review — {timestamp}\n\n"
                f"❌ **Changes requested**\n\n"
                f"{feedback or 'See comments above.'}\n"
            )

        try:
            await meshwiki_client.append_to_page(subtask["wiki_page"], pm_section)
        except Exception as exc:
            logger.warning("pm_review: failed to append review to wiki: %s", exc)

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

    return {"subtasks": updated_subtasks}
