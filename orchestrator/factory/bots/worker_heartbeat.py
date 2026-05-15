"""Worker heartbeat bot — writes worker_id + last_heartbeat to in-flight tasks.

Runs every ``FACTORY_WORKER_HEARTBEAT_INTERVAL_SECONDS`` (default 60s). Scans
``asyncio.all_tasks()`` for ``graph:*`` tasks that are still alive, derives
their MeshWiki page name from the task name, and patches two frontmatter
fields on each page:

- ``worker_id``: orchestrator instance UUID (set in lifespan startup)
- ``last_heartbeat``: ISO-8601 UTC timestamp

These fields let two other consumers detect dead tasks:

- ``BookkeeperBot._fix_stale_heartbeats`` — transitions in_progress tasks with
  a heartbeat older than ``FACTORY_WORKER_HEARTBEAT_STALE_SECONDS`` to failed.
  Tighter signal than the existing 2h "modified" check.
- (Future) Scheduler WIP cap counts heartbeating tasks, not raw in_progress.

Closes the "silent stall" half of the stuck-task fix (LLM hang, sandbox
crash) — the restart half is owned by Layer 1 + Layer 3.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..config import get_settings
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)


_GRAPH_TASK_PREFIX = "graph:"
_GRAPH_TASK_SUFFIXES = (":resume", ":rework")


def _page_name_from_task_name(task_name: str) -> str | None:
    """Extract the MeshWiki page name from a ``graph:<page>[:suffix]`` asyncio
    task name. Returns ``None`` if the name doesn't match the expected shape."""
    if not task_name.startswith(_GRAPH_TASK_PREFIX):
        return None
    rest = task_name[len(_GRAPH_TASK_PREFIX) :]
    for suffix in _GRAPH_TASK_SUFFIXES:
        if rest.endswith(suffix):
            return rest[: -len(suffix)]
    return rest


def _active_graph_pages() -> set[str]:
    """Return the set of MeshWiki page names corresponding to currently-alive
    ``graph:*`` asyncio tasks. De-duplicates by page (multiple variants per
    page — main / resume / rework — collapse to one heartbeat target)."""
    pages: set[str] = set()
    for t in asyncio.all_tasks():
        if t.done():
            continue
        name = t.get_name()
        page = _page_name_from_task_name(name)
        if page:
            pages.add(page)
    return pages


class WorkerHeartbeatBot(BaseBot):
    """Periodically write worker_id+last_heartbeat to each alive task's page."""

    name = "worker-heartbeat"

    def __init__(
        self,
        worker_id: str,
        interval_seconds: int | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.worker_heartbeat_interval_seconds
        )
        self._worker_id = worker_id

    async def run(self) -> BotResult:
        started = time.monotonic()
        actions = 0
        errors: list[str] = []

        pages = _active_graph_pages()
        if not pages:
            return BotResult(
                ran_at=started,
                actions_taken=0,
                errors=[],
                details=f"worker_id={self._worker_id} no_active_tasks",
            )

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        async with MeshWikiClient() as wiki:
            for page_name in pages:
                try:
                    await wiki.update_metadata(
                        page_name,
                        {
                            "worker_id": self._worker_id,
                            "last_heartbeat": now_iso,
                        },
                    )
                    actions += 1
                except Exception as exc:
                    errors.append(f"{page_name}: {exc}")

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=(
                f"worker_id={self._worker_id} pages={len(pages)} "
                f"written={actions} elapsed={elapsed:.2f}s"
            ),
        )
