"""Merge check node: verify all PRs have been merged before finalization."""

from __future__ import annotations

import logging

import httpx

from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..state import FactoryState

logger = logging.getLogger(__name__)


async def merge_check_node(state: FactoryState) -> dict:
    """Confirm that all subtask PRs have been merged into the main branch.

    Uses the GitHub REST API (via :class:`~factory.integrations.github_client.GitHubClient`)
    to check the state of each subtask PR that is currently in ``"review"``
    status.

    - If the PR is merged (``pr["merged"]`` is ``True``), the subtask status is
      updated to ``"merged"``.
    - If the PR is closed but not merged, the subtask status is updated to
      ``"failed"``.
    - If the PR is still open the subtask status is left unchanged.

    Args:
        state: Current FactoryState after pm_review / human_review_code.

    Returns:
        Partial state update with the updated ``subtasks`` list.
    """
    logger.info(
        "merge_check: verifying PR merges for task %s",
        state.get("task_wiki_page", "<unknown>"),
    )

    async with GitHubClient() as github_client:
        subtasks = list(state["subtasks"])

        for i, subtask in enumerate(subtasks):
            if subtask["status"] != "review":
                continue

            pr_number: int | None = subtask.get("pr_number")

            if pr_number is None:
                pr_url = subtask.get("pr_url")
                if pr_url:
                    pr_number = _extract_pr_number(pr_url)

            if pr_number is None:
                logger.warning(
                    "merge_check: subtask %s in 'review' has no pr_number or pr_url — skipping",
                    subtask["id"],
                )
                continue

            try:
                pr = await github_client.get_pr(pr_number)
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "merge_check: failed to fetch PR #%s for subtask %s: %s",
                    pr_number,
                    subtask["id"],
                    exc,
                )
                continue

            if pr.get("merged"):
                logger.info(
                    "merge_check: PR #%s is merged for subtask %s",
                    pr_number,
                    subtask["id"],
                )
                subtasks[i] = {**subtask, "status": "merged"}
            elif pr.get("state") == "closed":
                logger.info(
                    "merge_check: PR #%s is closed (not merged) for subtask %s — marking failed",
                    pr_number,
                    subtask["id"],
                )
                subtasks[i] = {**subtask, "status": "failed"}
            else:
                logger.debug(
                    "merge_check: PR #%s is still open for subtask %s",
                    pr_number,
                    subtask["id"],
                )

        return {"subtasks": subtasks}
