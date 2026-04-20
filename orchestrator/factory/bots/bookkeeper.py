"""Bookkeeper bot: reconciles stale task states against reality.

Two reconciliation rules run on every tick:

1. **Stuck in_progress → failed**
   Tasks with ``status: in_progress`` whose ``updated_at`` is older than
   ``FACTORY_BOOKKEEPER_STALE_HOURS`` are transitioned to ``failed``.

2. **Merged PRs → merged**
   Tasks with ``status: in_review`` that have a ``pr_url`` frontmatter field
   are checked against GitHub.  If the PR is merged the task is transitioned
   to ``merged``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_AGENT_LOG_HEADER = "\n\n## Agent Log\n"
_STALE_NOTE_TEMPLATE = (
    "\n- **{ts} UTC** — Bookkeeper: transitioned to `failed` "
    "(stuck in `in_progress` for >{stale_hours}h with no update)"
)
_MERGED_NOTE_TEMPLATE = (
    "\n- **{ts} UTC** — Bookkeeper: transitioned to `merged` "
    "(PR #{pr_number} detected as merged on GitHub)"
)


def _now_utc_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _parse_updated_at(value: str | None) -> datetime | None:
    """Parse an ISO-8601 ``updated_at`` frontmatter value into a UTC datetime.

    Returns ``None`` when the value is absent or cannot be parsed.
    """
    if not value:
        return None
    # Strip trailing Z and replace with +00:00 for fromisoformat compat
    normalized = value.rstrip("Z").replace("T", " ")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    # Assume UTC when timezone info is missing
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class BookkeeperBot(BaseBot):
    """Periodic bot that reconciles stale task states.

    Reads ``FACTORY_BOOKKEEPER_INTERVAL_SECONDS`` and
    ``FACTORY_BOOKKEEPER_STALE_HOURS`` from the environment (via pydantic
    settings) at construction time.
    """

    name = "bookkeeper"

    def __init__(
        self,
        interval_seconds: int | None = None,
        stale_hours: float | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.bookkeeper_interval_seconds
        )
        self._stale_hours = (
            stale_hours if stale_hours is not None else settings.bookkeeper_stale_hours
        )

    async def run(self) -> BotResult:
        """Execute one bookkeeper reconciliation cycle."""
        started = time.monotonic()
        actions = 0
        errors: list[str] = []

        async with MeshWikiClient() as wiki, GitHubClient() as github:
            stuck_actions, stuck_errors = await self._fix_stuck_in_progress(
                wiki, self._stale_hours
            )
            actions += stuck_actions
            errors.extend(stuck_errors)

            merged_actions, merged_errors = await self._fix_merged_prs(wiki, github)
            actions += merged_actions
            errors.extend(merged_errors)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=f"stale_hours={self._stale_hours} elapsed={elapsed:.2f}s",
        )

    # ------------------------------------------------------------------
    # Rule 1: stuck in_progress → failed
    # ------------------------------------------------------------------

    async def _fix_stuck_in_progress(
        self,
        wiki: MeshWikiClient,
        stale_hours: float,
    ) -> tuple[int, list[str]]:
        """Transition tasks that are stuck in ``in_progress`` to ``failed``.

        Args:
            wiki: Open :class:`MeshWikiClient` instance.
            stale_hours: Age threshold in hours.

        Returns:
            Tuple of (actions_taken, error_messages).
        """
        actions = 0
        errors: list[str] = []

        try:
            tasks = await wiki.list_tasks(status="in_progress")
        except Exception as exc:
            errors.append(f"list_tasks(in_progress) failed: {exc}")
            return actions, errors

        stale_threshold_seconds = stale_hours * 3600
        now = datetime.now(tz=timezone.utc)

        for task in tasks:
            page_name: str = task.get("name", "")
            if not page_name:
                continue

            metadata: dict = task.get("metadata", {})
            updated_at_raw: str | None = metadata.get("updated_at")
            updated_at = _parse_updated_at(updated_at_raw)

            if updated_at is None:
                logger.debug(
                    "bookkeeper: %s has no parseable updated_at — skipping", page_name
                )
                continue

            age_seconds = (now - updated_at).total_seconds()
            if age_seconds < stale_threshold_seconds:
                continue

            logger.info(
                "bookkeeper: %s stuck in_progress for %.1fh (threshold=%.1fh) — failing",
                page_name,
                age_seconds / 3600,
                stale_hours,
            )

            note = _STALE_NOTE_TEMPLATE.format(
                ts=_now_utc_str(), stale_hours=stale_hours
            )
            try:
                await self._transition_with_log(wiki, page_name, "failed", note)
                actions += 1
            except Exception as exc:
                err = f"failed to transition {page_name} to failed: {exc}"
                logger.error("bookkeeper: %s", err)
                errors.append(err)

        return actions, errors

    # ------------------------------------------------------------------
    # Rule 2: merged PRs → merged
    # ------------------------------------------------------------------

    async def _fix_merged_prs(
        self,
        wiki: MeshWikiClient,
        github: GitHubClient,
    ) -> tuple[int, list[str]]:
        """Transition tasks in ``in_review`` whose PR is merged on GitHub.

        Args:
            wiki: Open :class:`MeshWikiClient` instance.
            github: Open :class:`GitHubClient` instance.

        Returns:
            Tuple of (actions_taken, error_messages).
        """
        actions = 0
        errors: list[str] = []

        try:
            tasks = await wiki.list_tasks(status="in_review")
        except Exception as exc:
            errors.append(f"list_tasks(in_review) failed: {exc}")
            return actions, errors

        for task in tasks:
            page_name: str = task.get("name", "")
            if not page_name:
                continue

            metadata: dict = task.get("metadata", {})
            pr_url: str | None = metadata.get("pr_url")
            if not pr_url:
                continue

            pr_number = _extract_pr_number(pr_url)
            if pr_number is None:
                logger.debug(
                    "bookkeeper: %s has pr_url %r but could not extract PR number",
                    page_name,
                    pr_url,
                )
                continue

            try:
                pr = await github.get_pr(pr_number)
            except httpx.HTTPStatusError as exc:
                err = f"GitHub PR #{pr_number} fetch failed for {page_name}: {exc}"
                logger.warning("bookkeeper: %s", err)
                errors.append(err)
                continue
            except Exception as exc:
                err = (
                    f"unexpected error fetching PR #{pr_number} for {page_name}: {exc}"
                )
                logger.error("bookkeeper: %s", err)
                errors.append(err)
                continue

            if not pr.get("merged"):
                logger.debug(
                    "bookkeeper: %s PR #%d not yet merged — skipping",
                    page_name,
                    pr_number,
                )
                continue

            logger.info(
                "bookkeeper: %s PR #%d is merged — transitioning to merged",
                page_name,
                pr_number,
            )
            note = _MERGED_NOTE_TEMPLATE.format(ts=_now_utc_str(), pr_number=pr_number)
            try:
                await self._transition_with_log(wiki, page_name, "merged", note)
                actions += 1
            except Exception as exc:
                err = f"failed to transition {page_name} to merged: {exc}"
                logger.error("bookkeeper: %s", err)
                errors.append(err)

        return actions, errors

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    async def _transition_with_log(
        self, wiki: MeshWikiClient, page_name: str, new_status: str, note: str
    ) -> None:
        """Transition a task and append a note to its Agent Log section.

        Args:
            wiki: Open :class:`MeshWikiClient` instance.
            page_name: Wiki page name of the task.
            new_status: Target status string.
            note: Markdown line to append to the Agent Log.
        """
        await wiki.transition_task(page_name, new_status)
        try:
            page = await wiki.get_page(page_name)
            if page is None:
                return
            content: str = page.get("content", "")
            if _AGENT_LOG_HEADER.strip() in content:
                new_content = content.rstrip() + note
            else:
                new_content = content.rstrip() + _AGENT_LOG_HEADER + note
            await wiki.create_page(page_name, new_content)
        except Exception as exc:
            # Non-fatal: the transition already succeeded; only the log append failed.
            logger.warning(
                "bookkeeper: could not append agent log to %s: %s", page_name, exc
            )
