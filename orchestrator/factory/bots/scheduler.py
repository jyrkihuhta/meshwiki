"""Scheduler bot: autonomously dispatches planned tasks to the factory.

On every tick the bot:

1. Counts in-progress parent tasks — if at the global cap, skip.
2. Checks MiniMax token-plan quota — if below threshold and threshold is set,
   skip (let in-flight grinders run; don't start new ones).
3. Queries planned tasks assigned to ``factory``.  Subtask pages (those with
   a ``parent_task`` field) are skipped — they are driven by their parent graph.
4. Sorts candidates by priority DESC (urgent > high > normal > low) then by
   ``modified`` ASC (oldest-waiting first as a tiebreaker).
5. Transitions the top-K tasks to ``in_progress`` via
   :meth:`MeshWikiClient.transition_task`.  The MeshWiki task-machine rule C13
   (``assignee: factory`` required) acts as a guardrail — the transition is
   rejected for tasks that are not yet assigned to the factory, so the
   scheduler never hijacks human-owned tasks.
"""

from __future__ import annotations

import logging
import time

from ..agents.pm_agent import anthropic_blocked_seconds_remaining
from ..config import get_settings
from ..hbr import get_hbr
from ..integrations.meshwiki_client import MeshWikiClient
from ..integrations.minimax_client import MiniMaxUsageClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_PRIORITY_RANK: dict[str, int] = {
    "urgent": 4,
    "high": 3,
    "normal": 2,
    "low": 1,
}


def _priority_rank(task: dict) -> int:
    metadata = task.get("metadata") or {}
    raw = str(metadata.get("priority") or "normal").lower()
    return _PRIORITY_RANK.get(raw, 2)


def _modified_ts(task: dict) -> str:
    metadata = task.get("metadata") or {}
    return str(metadata.get("modified") or "")


class SchedulerBot(BaseBot):
    """Autonomous backlog scheduler.

    Reads ``FACTORY_SCHEDULER_INTERVAL_SECONDS``,
    ``FACTORY_MAX_CONCURRENT_PARENT_TASKS``, and
    ``FACTORY_MINIMAX_TOKEN_THRESHOLD`` from settings at construction time.
    """

    name = "scheduler"

    def __init__(self, interval_seconds: int | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.scheduler_interval_seconds
        )
        self._settings = settings

    async def run(self) -> BotResult:
        """Execute one scheduler tick."""
        started = time.monotonic()
        errors: list[str] = []
        settings = self._settings

        async with MeshWikiClient() as wiki:
            # ── 1. Count active parent tasks ──────────────────────────────
            try:
                in_progress = await wiki.list_tasks(
                    status="in_progress", assignee="factory"
                )
            except Exception as exc:
                errors.append(f"list_tasks(in_progress) failed: {exc}")
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=errors,
                    details="could not count in-progress tasks",
                )

            active_parents = [
                t
                for t in in_progress
                if not (t.get("metadata") or {}).get("parent_task")
            ]
            n_active = len(active_parents)
            cap = settings.max_concurrent_parent_tasks
            slots = cap - n_active

            if slots <= 0:
                elapsed = time.monotonic() - started
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=[],
                    details=f"at cap ({n_active}/{cap}) elapsed={elapsed:.2f}s",
                )

            # ── 2. Check MiniMax quota ────────────────────────────────────
            if settings.minimax_token_threshold > 0:
                async with MiniMaxUsageClient() as minimax:
                    quota = await minimax.get_token_plan_remaining()
                if (
                    quota.remaining is not None
                    and quota.remaining < settings.minimax_token_threshold
                ):
                    elapsed = time.monotonic() - started
                    logger.info(
                        "scheduler: MiniMax quota low (%s remaining, threshold=%d) — pausing",
                        quota.remaining,
                        settings.minimax_token_threshold,
                    )
                    return BotResult(
                        ran_at=started,
                        actions_taken=0,
                        errors=[],
                        details=(
                            f"minimax quota low ({quota.remaining} remaining, "
                            f"reset_at={quota.reset_at}) elapsed={elapsed:.2f}s"
                        ),
                    )

            # ── 2b. Check Anthropic availability ─────────────────────────
            blocked_secs = anthropic_blocked_seconds_remaining()
            if blocked_secs > 0:
                if not settings.openrouter_api_key and not settings.minimax_api_key:
                    elapsed = time.monotonic() - started
                    logger.warning(
                        "scheduler: Anthropic circuit breaker active (%.0fs remaining), "
                        "no fallback configured — pausing dispatch",
                        blocked_secs,
                    )
                    return BotResult(
                        ran_at=started,
                        actions_taken=0,
                        errors=[],
                        details=(
                            f"anthropic_blocked={blocked_secs:.0f}s no_fallback "
                            f"elapsed={elapsed:.2f}s"
                        ),
                    )
                logger.info(
                    "scheduler: Anthropic circuit breaker active (%.0fs remaining), "
                    "using fallback provider",
                    blocked_secs,
                )

            # ── 2c. Check daily budget ────────────────────────────────────
            if not get_hbr().can_allocate_sandbox():
                elapsed = time.monotonic() - started
                logger.info("scheduler: daily budget exhausted — pausing dispatch")
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=[],
                    details=f"daily_budget_exhausted elapsed={elapsed:.2f}s",
                )

            # ── 3. Fetch planned tasks ────────────────────────────────────
            try:
                planned = await wiki.list_tasks(status="planned", assignee="factory")
            except Exception as exc:
                errors.append(f"list_tasks(planned) failed: {exc}")
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=errors,
                    details="could not fetch planned tasks",
                )

            # ── 4. Filter subtasks + sort ─────────────────────────────────
            candidates = [
                t for t in planned if not (t.get("metadata") or {}).get("parent_task")
            ]
            candidates.sort(key=lambda t: (-_priority_rank(t), _modified_ts(t)))

            # ── 5. Dispatch top-K ─────────────────────────────────────────
            to_dispatch = candidates[:slots]
            actions = 0
            for task in to_dispatch:
                page_name: str = task.get("name", "")
                if not page_name:
                    continue
                priority_val = (task.get("metadata") or {}).get("priority", "normal")
                try:
                    await wiki.transition_task(page_name, "in_progress")
                    logger.info(
                        "scheduler: dispatched %s (priority=%s)",
                        page_name,
                        priority_val,
                    )
                    actions += 1
                except Exception as exc:
                    err = f"failed to dispatch {page_name}: {exc}"
                    logger.error("scheduler: %s", err)
                    errors.append(err)

        elapsed = time.monotonic() - started
        blocked_secs = anthropic_blocked_seconds_remaining()
        details = f"active={n_active}/{cap} dispatched={actions} elapsed={elapsed:.2f}s"
        if blocked_secs > 0:
            provider = "openrouter" if settings.openrouter_api_key else "minimax"
            details += f" anthropic_blocked={blocked_secs:.0f}s fallback={provider}"
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=details,
        )
