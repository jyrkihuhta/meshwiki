"""Stale PR bot: creates fix tasks for factory PRs with long-running CI failures.

On each tick the bot:

1. Lists all open PRs whose head branch starts with ``factory/``.
2. For each PR, fetches GitHub check runs on the head SHA.
3. Skips if no checks are failing or the oldest failure is younger than
   ``FACTORY_STALE_PR_FAILURE_MINUTES`` (default 30).
4. Looks up the corresponding wiki task (matched via ``pr_url`` in ``review`` tasks).
5. Skips if ``stale_fix_attempts`` on the task page has already reached the cap.
6. Creates a new planned task page at ``Factory/Fixes/PR-{number}-{attempt}``
   with failure details so the scheduler can dispatch a grinder to fix it.
7. Increments ``stale_fix_attempts`` on the original task page.
8. Respects the HBR daily budget before creating any new tasks.

Safety guards:
- Only processes ``factory/*`` branches.
- Max ``FACTORY_STALE_PR_MAX_ATTEMPTS`` fix tasks per PR (default 2).
- Does not create a fix page if one with the same name already exists.
- Skips entirely when the daily budget is exhausted.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import get_settings
from ..hbr import get_hbr
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_MAX_FAILURE_TEXT_CHARS = 3000


def _parse_github_ts(ts: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (``Z``-suffixed) into a UTC datetime."""
    if not ts:
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class StalePRBot(BaseBot):
    """Periodic bot that creates fix tasks for factory PRs with stale CI failures.

    Reads ``FACTORY_STALE_PR_INTERVAL_SECONDS``, ``FACTORY_STALE_PR_FAILURE_MINUTES``,
    and ``FACTORY_STALE_PR_MAX_ATTEMPTS`` from settings.
    """

    name = "stale-pr"

    def __init__(self, interval_seconds: int | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.stale_pr_interval_seconds
        )
        self._failure_minutes = settings.stale_pr_failure_minutes
        self._max_attempts = settings.stale_pr_max_attempts

    async def run(self) -> BotResult:
        """Execute one stale-PR scan."""
        started = time.monotonic()

        if not get_hbr().can_allocate_sandbox():
            elapsed = time.monotonic() - started
            return BotResult(
                ran_at=started,
                actions_taken=0,
                errors=[],
                details=f"daily_budget_exhausted elapsed={elapsed:.2f}s",
            )

        actions = 0
        errors: list[str] = []
        open_prs: list[dict] = []
        review_tasks: list[dict] = []

        async with MeshWikiClient() as wiki, GitHubClient() as gh:
            try:
                review_tasks = await wiki.list_tasks(status="review", assignee="factory")
            except Exception as exc:
                errors.append(f"list_tasks(review) failed: {exc}")
                return BotResult(ran_at=started, actions_taken=0, errors=errors)

            # Build pr_number → task dict for fast lookup
            pr_task_map: dict[int, dict[str, Any]] = {}
            for task in review_tasks:
                meta = task.get("metadata") or {}
                num = _extract_pr_number(meta.get("pr_url", "") or "")
                if num:
                    pr_task_map[num] = task

            try:
                open_prs = await gh.list_open_prs(head_prefix="factory/")
            except Exception as exc:
                errors.append(f"list_open_prs failed: {exc}")
                return BotResult(ran_at=started, actions_taken=0, errors=errors)

            for pr in open_prs:
                try:
                    acted = await self._process_pr(pr, pr_task_map, wiki, gh)
                    if acted:
                        actions += 1
                except Exception as exc:
                    err = f"error processing PR #{pr.get('number', '?')}: {exc}"
                    logger.warning("stale-pr: %s", err)
                    errors.append(err)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=(
                f"open_prs={len(open_prs)} "
                f"review_tasks={len(review_tasks)} "
                f"elapsed={elapsed:.2f}s"
            ),
        )

    # ------------------------------------------------------------------
    # Per-PR logic
    # ------------------------------------------------------------------

    async def _process_pr(
        self,
        pr: dict[str, Any],
        pr_task_map: dict[int, dict[str, Any]],
        wiki: MeshWikiClient,
        gh: GitHubClient,
    ) -> bool:
        pr_number: int = pr["number"]
        branch: str = pr.get("head", {}).get("ref", "")
        pr_url: str = pr.get("html_url", "")
        sha: str = pr.get("head", {}).get("sha", "")
        if not sha:
            return False

        check_runs = await gh.get_check_runs(sha)
        failed = [
            cr for cr in check_runs
            if cr.get("conclusion") in ("failure", "timed_out", "action_required")
        ]
        if not failed:
            return False

        # Find the longest-failing check and its age
        now = datetime.now(tz=timezone.utc)
        threshold_seconds = self._failure_minutes * 60
        oldest_check: dict[str, Any] | None = None
        oldest_age: float = 0.0

        for cr in failed:
            completed_at = _parse_github_ts(cr.get("completed_at"))
            if completed_at:
                age = (now - completed_at).total_seconds()
                if age > oldest_age:
                    oldest_age = age
                    oldest_check = cr

        if oldest_check is None or oldest_age < threshold_seconds:
            logger.debug(
                "stale-pr: PR #%d has %d failing check(s) but oldest is only %.0fs old — skip",
                pr_number,
                len(failed),
                oldest_age,
            )
            return False

        # Match to a wiki review task
        task = pr_task_map.get(pr_number)
        if task is None:
            logger.debug("stale-pr: PR #%d has no matching review task — skip", pr_number)
            return False

        task_name: str = task.get("name", "")
        meta = task.get("metadata") or {}
        attempts = int(meta.get("stale_fix_attempts", 0) or 0)

        if attempts >= self._max_attempts:
            logger.debug(
                "stale-pr: PR #%d task %r already at stale_fix_attempts=%d (max=%d) — skip",
                pr_number,
                task_name,
                attempts,
                self._max_attempts,
            )
            return False

        check_name: str = oldest_check.get("name", "unknown")
        failure_text = self._extract_failure_text(oldest_check)
        fix_page_name = f"Factory/Fixes/PR-{pr_number}-{attempts + 1}"

        # Idempotency: skip if the fix page was already created
        if await wiki.get_page(fix_page_name) is not None:
            logger.debug("stale-pr: fix page %r already exists — skip", fix_page_name)
            return False

        # Create the fix task page
        content = self._build_fix_page(
            fix_page_name,
            pr_number,
            pr_url,
            branch,
            task_name,
            check_name,
            oldest_age,
            failure_text,
        )
        await wiki.create_page(fix_page_name, content)
        logger.info(
            "stale-pr: created fix task %r for PR #%d (check=%r failing_for=%.0fm attempt=%d/%d)",
            fix_page_name,
            pr_number,
            check_name,
            oldest_age / 60,
            attempts + 1,
            self._max_attempts,
        )

        # Annotate the original task page (non-fatal)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        note = (
            f"\n\n## Stale CI Fix\n\n"
            f"- **{ts} UTC** — Stale PR bot created fix task "
            f"[[{fix_page_name}]] for failing check `{check_name}` "
            f"(failing for {oldest_age / 60:.0f}m, attempt {attempts + 1}/{self._max_attempts})"
        )
        try:
            await wiki.append_to_page(
                task_name,
                note.strip(),
                frontmatter_updates={"stale_fix_attempts": attempts + 1},
            )
        except Exception as exc:
            logger.warning(
                "stale-pr: could not update original task %r: %s (non-fatal)", task_name, exc
            )

        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_failure_text(self, check_run: dict[str, Any]) -> str:
        output = check_run.get("output") or {}
        parts = []
        if output.get("title"):
            parts.append(output["title"])
        if output.get("summary"):
            parts.append(output["summary"])
        if output.get("text"):
            parts.append(output["text"])
        return "\n".join(parts).strip()[:_MAX_FAILURE_TEXT_CHARS]

    def _build_fix_page(
        self,
        page_name: str,
        pr_number: int,
        pr_url: str,
        branch: str,
        original_task: str,
        check_name: str,
        failure_age_seconds: float,
        failure_text: str,
    ) -> str:
        failure_section = (
            f"\n```\n{failure_text}\n```\n" if failure_text else "\n*(no output captured)*\n"
        )
        age_minutes = int(failure_age_seconds / 60)
        return (
            f"---\n"
            f"type: task\n"
            f"assignee: factory\n"
            f"status: planned\n"
            f"priority: high\n"
            f"stale_fix_pr_url: {pr_url}\n"
            f"stale_fix_pr_number: {pr_number}\n"
            f"stale_fix_branch: {branch}\n"
            f"stale_fix_original_task: {original_task}\n"
            f"---\n\n"
            f"# Fix CI: {branch} (#{pr_number})\n\n"
            f"The PR #{pr_number} on branch `{branch}` has been failing "
            f"CI for {age_minutes} minutes and requires intervention.\n\n"
            f"## Objective\n\n"
            f"Fix the failing CI check `{check_name}` so the PR can be reviewed "
            f"and merged. Push the fix directly to branch `{branch}` — "
            f"do **not** create a new branch or PR.\n\n"
            f"**PR:** {pr_url}  \n"
            f"**Branch:** `{branch}`  \n"
            f"**Original task:** [[{original_task}]]  \n"
            f"**Failing check:** `{check_name}`\n\n"
            f"## Steps\n\n"
            f"1. Clone the repository and check out branch `{branch}`\n"
            f"2. Reproduce the failure locally: run the failing check\n"
            f"3. Fix the root cause\n"
            f"4. Run tests to confirm the fix\n"
            f"5. Push directly to `{branch}` (no new PR needed)\n\n"
            f"## CI Failure Output\n"
            f"{failure_section}"
        )
