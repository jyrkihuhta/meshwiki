"""FastAPI webhook server receiving MeshWiki outbound events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .bots.bookkeeper import BookkeeperBot
from .bots.ci_fixer import CIFixerBot
from .bots.class_gap_researcher import ClassGapResearcherBot
from .bots.insight import InsightBot
from .bots.registry import BotRegistry
from .bots.scheduler import SchedulerBot
from .bots.stale_pr_bot import StalePRBot
from .bots.terminal_review import TerminalReviewBot
from .config import FACTORY_MAX_CONCURRENT_SANDBOXES, get_settings, validate_settings
from .graph import build_graph
from .hbr import get_hbr
from .integrations.meshwiki_client import MeshWikiClient
from .state import FactoryState

logger = logging.getLogger(__name__)


async def _clear_stuck_grinders(graph, config: dict, page_name: str) -> None:
    """Clear active_grinders entries whose subtasks never completed before a crash.

    When the orchestrator dies mid-fan-out, grinder IDs remain in
    ``active_grinders`` even though their subtasks are still ``pending``.
    ``route_grinders`` skips those IDs, so the graph would stall forever.
    This function removes the stale entries so the next ``ainvoke`` re-dispatches
    the affected subtasks.
    """
    try:
        snapshot = await graph.aget_state(config)
        if snapshot is None:
            return
        active: list[str] = list(snapshot.values.get("active_grinders") or [])
        subtasks: list[dict] = list(snapshot.values.get("subtasks") or [])
        stuck = {
            s["id"]
            for s in subtasks
            if s["id"] in active and s.get("status") in ("pending", "changes_requested")
        }
        if not stuck:
            return
        logger.info(
            "factory: clearing %d stuck grinder(s) for %s: %s",
            len(stuck),
            page_name,
            stuck,
        )
        await graph.aupdate_state(
            config,
            {"active_grinders": [gid for gid in active if gid not in stuck]},
        )
    except Exception as exc:
        logger.warning(
            "factory: could not clear stuck grinders for %s: %s", page_name, exc
        )


async def _resume_interrupted_tasks(graph, saver, settings) -> None:
    """On startup, resume any tasks that were active when the orchestrator died.

    Strategy:
    1. Ask MeshWiki for all tasks with status=in_progress or status=review
       that are assigned to factory (both statuses represent active graph runs).
    2. For each, check if the SQLite checkpointer has a saved state.
    3. Clear any stale active_grinders entries left over from a mid-fan-out crash
       so route_grinders can re-dispatch those subtasks.
    4. Call ainvoke(None) with the same thread_id — LangGraph resumes from the
       last node boundary rather than restarting from scratch.
    """
    async with MeshWikiClient(
        settings.meshwiki_url, settings.meshwiki_api_key
    ) as client:
        all_factory_tasks: list[dict] = []
        for status in ("in_progress", "review"):
            try:
                tasks = await client.list_tasks(status=status)
            except Exception as exc:
                logger.warning(
                    "factory: could not fetch %s tasks on startup: %s", status, exc
                )
                continue
            all_factory_tasks.extend(
                t
                for t in tasks
                if t.get("metadata", {}).get("assignee") == "factory"
                or t.get("assignee") == "factory"  # flat format (defensive)
            )

        if not all_factory_tasks:
            return

        factory_tasks = all_factory_tasks
        logger.info(
            "factory: found %d active factory task(s) on startup", len(factory_tasks)
        )
        for task in factory_tasks:
            page_name = task.get("name", "")
            if not page_name:
                continue

            # Prefer UUID as thread_id; fall back to page name for legacy tasks.
            task_uuid: str | None = (task.get("metadata") or {}).get("uuid")
            thread_id = task_uuid or page_name
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint_tuple = await saver.aget_tuple(config)
            if checkpoint_tuple is None and task_uuid:
                # Old checkpoint may be keyed by page name — try that too.
                config = {"configurable": {"thread_id": page_name}}
                checkpoint_tuple = await saver.aget_tuple(config)
            if checkpoint_tuple is None:
                logger.info(
                    "factory: no checkpoint for %s — skipping resume", page_name
                )
                continue

            await _clear_stuck_grinders(graph, config, page_name)

            # Register the page→thread_id mapping so /status can resolve it.
            thread_id = config["configurable"]["thread_id"]
            if hasattr(app, "state") and hasattr(app.state, "page_thread_map"):
                app.state.page_thread_map[page_name] = thread_id

            logger.info(
                "factory: resuming interrupted task %s from checkpoint (thread_id=%s)",
                page_name,
                thread_id,
            )

            def _log_exc(t: asyncio.Task, name: str = page_name) -> None:
                if not t.cancelled() and (exc := t.exception()):
                    logger.error("graph task %s failed: %s", name, exc, exc_info=exc)

            resume_task = asyncio.create_task(
                graph.ainvoke(None, config=config),
                name=f"graph:{page_name}:resume",
            )
            resume_task.add_done_callback(_log_exc)


async def _drain_graph_tasks(timeout_seconds: float) -> None:
    """Wait for in-flight ``graph:*`` asyncio tasks to finish their current node.

    Called from the lifespan shutdown path so a SIGTERM (e.g. ``docker restart``)
    doesn't kill graph runs mid-LLM-call. LangGraph writes a checkpoint after
    every node, so tasks that don't finish in time are cancelled but resume
    cleanly from the last completed node on the next startup via
    ``_resume_interrupted_tasks``.
    """
    pending = [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("graph:") and not t.done()
    ]
    if not pending:
        logger.info("factory: shutdown — no in-flight graph tasks to drain")
        return

    logger.info(
        "factory: shutdown — draining %d in-flight graph task(s) "
        "with timeout=%.1fs",
        len(pending),
        timeout_seconds,
    )
    done, still_running = await asyncio.wait(pending, timeout=timeout_seconds)
    logger.info(
        "factory: shutdown — %d graph task(s) completed gracefully, "
        "%d cancelled (will resume on next startup)",
        len(done),
        len(still_running),
    )
    # Cancel anything that didn't finish so the event loop can exit cleanly.
    for t in still_running:
        t.cancel()
    # Give cancelled tasks a brief window to unwind before lifespan returns.
    if still_running:
        try:
            await asyncio.wait(still_running, timeout=2.0)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the SQLite checkpoint DB, build the graph, start bots, close on shutdown."""
    settings = get_settings()
    validate_settings(settings)

    # Build bot registry
    bot_registry = BotRegistry()
    bot_registry.register(BookkeeperBot())
    bot_registry.register(TerminalReviewBot())
    if settings.scheduler_enabled:
        bot_registry.register(SchedulerBot())
        logger.info(
            "factory: scheduler bot enabled (interval=%ds)",
            settings.scheduler_interval_seconds,
        )
    if settings.ci_fixer_enabled:
        bot_registry.register(CIFixerBot())
        logger.info(
            "factory: ci-fixer bot enabled (interval=%ds)",
            settings.ci_fixer_interval_seconds,
        )
    if settings.insight_enabled:
        bot_registry.register(InsightBot())
        logger.info(
            "factory: insight bot enabled (interval=%ds)",
            settings.insight_interval_seconds,
        )
    if settings.stale_pr_enabled:
        bot_registry.register(StalePRBot())
        logger.info(
            "factory: stale-pr bot enabled (interval=%ds, failure_minutes=%d)",
            settings.stale_pr_interval_seconds,
            settings.stale_pr_failure_minutes,
        )
    if settings.class_gap_researcher_enabled:
        bot_registry.register(ClassGapResearcherBot())
        logger.info(
            "factory: class-gap-researcher bot enabled (interval=%ds, model=%s)",
            settings.class_gap_researcher_interval_seconds,
            settings.class_gap_researcher_model,
        )
    app.state.bot_registry = bot_registry

    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db) as saver:
        app.state.graph = build_graph(saver)
        app.state.saver = saver
        app.state.settings = settings
        # Maps page_name → LangGraph thread_id (UUID); allows /status to look up
        # the correct checkpoint key even though asyncio task names use page_name.
        app.state.page_thread_map: dict[str, str] = {}
        logger.info(
            "factory: graph initialised with SQLite checkpointer at %s",
            settings.checkpoint_db,
        )
        await _resume_interrupted_tasks(app.state.graph, saver, settings)
        await bot_registry.start_all()
        yield
        # Shutdown order:
        # 1. Stop bots first so they don't dispatch *new* work mid-drain.
        # 2. Drain in-flight graph tasks (wait for checkpoint-boundary completion).
        # 3. Close the SQLite saver via the async-with exit.
        await bot_registry.stop_all()
        await _drain_graph_tasks(settings.graph_shutdown_timeout_seconds)
    logger.info("factory: SQLite checkpointer closed")


app = FastAPI(title="Factory Orchestrator", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """
    Verify the HMAC-SHA256 signature sent in ``X-MeshWiki-Signature``.

    Raises ``HTTPException(403)`` when the secret is configured but the
    signature is missing or invalid.  When the secret is empty (dev mode),
    verification is skipped entirely.
    """
    settings = get_settings()
    if not settings.webhook_secret:
        return  # Dev mode: skip verification

    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing X-MeshWiki-Signature-256")

    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    # Support "sha256=<hex>" prefix (same convention as GitHub webhooks)
    provided = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Invalid signature")


async def _resolve_thread_id(page_name: str, data: dict[str, Any]) -> str:
    """Return the stable graph thread_id for *page_name*.

    Prefers the ``uuid`` field from the webhook data payload (MeshWiki embeds
    page metadata there).  Falls back to fetching the page via the API, then
    ultimately to the page name itself for legacy pages that have no UUID yet.
    """
    if task_uuid := data.get("uuid"):
        return task_uuid
    try:
        settings = get_settings()
        async with MeshWikiClient(
            settings.meshwiki_url, settings.meshwiki_api_key
        ) as mc:
            page = await mc.get_page(page_name)
        if page:
            return page.get("metadata", {}).get("uuid") or page_name
    except Exception as exc:
        logger.debug("factory: could not fetch UUID for %s: %s", page_name, exc)
    return page_name


def _build_initial_state(
    page_name: str, thread_id: str, data: dict[str, Any]
) -> FactoryState:
    """Build the initial FactoryState for a new graph thread.

    ``skip_decomposition`` is handled by ``task_intake_node`` which reads the
    full page from MeshWiki — no need to inspect the webhook payload here.
    """
    task_uuid = thread_id if thread_id != page_name else None
    return FactoryState(
        thread_id=thread_id,
        task_wiki_page=page_name,
        task_uuid=task_uuid,
        title=data.get("title", page_name),
        requirements=data.get("requirements", ""),
        subtasks=[],
        decomposition_approved=False,
        active_grinders=[],
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        graph_status="intake",
        error=None,
        escalation_decision=None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/status")
async def status(request: Request) -> dict:
    """Dashboard status: bots, active graph threads, resource usage."""
    bot_registry: BotRegistry = request.app.state.bot_registry
    graph = request.app.state.graph
    settings = request.app.state.settings

    # ── Active asyncio graph tasks ────────────────────────────────────────────
    running_tasks = {
        t.get_name(): t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("graph:") and not t.done()
    }
    # Collect unique thread IDs — "graph:<page>" and "graph:<page>:resume" etc.
    active_thread_ids: set[str] = set()
    for task_name in running_tasks:
        parts = task_name.split(":", 2)
        if len(parts) >= 2:
            active_thread_ids.add(parts[1])

    # ── Read LangGraph state for each active thread ───────────────────────────
    active_graphs: list[dict] = []
    total_cost_usd: float = 0.0
    total_grinders: int = 0

    page_thread_map: dict[str, str] = getattr(request.app.state, "page_thread_map", {})
    for page_id in sorted(active_thread_ids):
        # active_thread_ids contains page names (from asyncio task names).
        # Resolve to the actual LangGraph thread_id (UUID) when available.
        thread_id = page_thread_map.get(page_id, page_id)
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)
            if snapshot is None:
                continue
            v = snapshot.values
            subtasks: list[dict] = list(v.get("subtasks") or [])
            completed = list(v.get("completed_subtask_ids") or [])
            failed = list(v.get("failed_subtask_ids") or [])
            active_grinders: list[str] = list(v.get("active_grinders") or [])
            cost: float = float(v.get("cost_usd") or 0.0)
            total_cost_usd += cost
            total_grinders += len(active_grinders)
            active_graphs.append(
                {
                    "thread_id": page_id,  # page name for display
                    "title": v.get("title", page_id),
                    "graph_status": v.get("graph_status", "unknown"),
                    "subtasks_total": len(subtasks),
                    "subtasks_completed": len(completed),
                    "subtasks_failed": len(failed),
                    "active_grinders": len(active_grinders),
                    "cost_usd": cost,
                }
            )
        except Exception as exc:
            logger.debug("status: could not read state for %s: %s", page_id, exc)

    # ── Resources ─────────────────────────────────────────────────────────────
    resources = {
        "max_concurrent_parent_tasks": settings.max_concurrent_parent_tasks,
        "active_parent_tasks": len(active_thread_ids),
        "active_grinders": total_grinders,
        "max_concurrent_sandboxes": FACTORY_MAX_CONCURRENT_SANDBOXES,
        "total_cost_usd": round(total_cost_usd, 4),
    }

    return {
        "bots": bot_registry.get_status(),
        "active_graphs": active_graphs,
        "resources": resources,
        "generated_at": time.time(),
    }


@app.get("/hbr/status")
async def hbr_status(request: Request) -> dict:
    """HBR resource manager: daily cost vs budget, per-model usage, active sandboxes."""
    hbr = get_hbr()
    result = hbr.status()

    # Count active sandboxes from LangGraph state across all running threads.
    graph = request.app.state.graph
    page_thread_map: dict[str, str] = getattr(request.app.state, "page_thread_map", {})
    running_tasks = {
        t.get_name(): t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("graph:") and not t.done()
    }
    active_thread_ids: set[str] = set()
    for task_name in running_tasks:
        parts = task_name.split(":", 2)
        if len(parts) >= 2:
            active_thread_ids.add(parts[1])

    active_sandboxes = 0
    for page_id in active_thread_ids:
        thread_id = page_thread_map.get(page_id, page_id)
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)
            if snapshot:
                active_sandboxes += len(snapshot.values.get("active_grinders") or [])
        except Exception as exc:
            logger.debug("hbr_status: could not read state for %s: %s", page_id, exc)

    result["active_sandboxes"] = active_sandboxes
    result["max_sandboxes"] = FACTORY_MAX_CONCURRENT_SANDBOXES
    return result


@app.get("/tasks")
async def tasks(
    request: Request,
    status: str | None = None,
    assignee: str | None = None,
    repo: str | None = None,
    parent_task: str | None = None,
    limit: int = 20,
) -> dict:
    """Factory task inventory: counts by status + most recently modified.

    Query params:
      - status / assignee / repo / parent_task: filter the underlying
        MeshWiki task query (forwarded to ``MeshWikiClient.list_tasks``).
      - limit: how many recently-modified tasks to include in the
        ``recent`` list (default 20). The full ``items`` list is always
        returned so callers can paginate client-side.

    Response shape::

        {
          "total": 73,
          "by_status": {"planned": 8, "in_progress": 2, ...},
          "recent": [
            {"name": "...", "status": "...", "title": "...", "modified": "..."},
            ...
          ],
          "items": [<full task dicts as returned by MeshWiki>]
        }
    """
    settings = get_settings()
    async with MeshWikiClient(
        settings.meshwiki_url, settings.meshwiki_api_key
    ) as client:
        items = await client.list_tasks(
            status=status, assignee=assignee, repo=repo, parent_task=parent_task,
        )

    by_status: dict[str, int] = {}
    for t in items:
        s = (t.get("metadata") or {}).get("status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1

    def _modified(t: dict) -> str:
        return (t.get("metadata") or {}).get("modified") or ""

    recent = sorted(items, key=_modified, reverse=True)[: max(0, limit)]
    recent_view = [
        {
            "name": t.get("name"),
            "status": (t.get("metadata") or {}).get("status"),
            "title": (t.get("metadata") or {}).get("title"),
            "repo": (t.get("metadata") or {}).get("repository")
            or (t.get("metadata") or {}).get("repo"),
            "modified": _modified(t),
        }
        for t in recent
    ]

    return {
        "total": len(items),
        "by_status": by_status,
        "recent": recent_view,
        "items": items,
    }


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_meshwiki_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    """
    Receive an outbound webhook event from MeshWiki.

    Expected JSON body::

        {
            "event": "task.assigned",
            "page":  "Task_0001_implement_feature",
            "data":  { ... extra fields ... }
        }

    Events handled:
    - ``task.assigned``  — start a new graph thread for the task
    - ``task.approved``  — resume a paused thread (human approved decomposition)
    - ``task.pr_merged`` — log the merge (GitHub→MeshWiki→orchestrator loop completes)
    - everything else   — log and return ``{"status": "ignored"}``
    """
    body = await request.body()
    _verify_signature(body, x_meshwiki_signature_256)

    payload = await request.json()
    # MeshWiki sends a raw event like "task.planned_to_in_progress" in "event"
    # and the semantic name (e.g. "task.assigned") in "canonical_event".
    raw_event: str = payload.get("event", "")
    event: str = payload.get("canonical_event") or raw_event
    page_name: str = payload.get("page", "")
    data: dict[str, Any] = payload.get("data", {})

    logger.info(
        "webhook: received event=%s (raw=%s) page=%s", event, raw_event, page_name
    )

    if event == "task.assigned":
        # Skip subtask pages — they are driven by their parent graph thread.
        # MeshWiki includes full page metadata in `data`, so parent_task is
        # available here without an extra HTTP round-trip.
        if data.get("parent_task"):
            logger.debug(
                "webhook: ignoring task.assigned for subtask %s (parent_task=%s)",
                page_name,
                data["parent_task"],
            )
            return {"status": "ignored", "reason": "subtask managed by parent graph"}

        # Guardrail: don't start a duplicate graph thread if one is already running.
        # Assignee/type checks are done in task_intake (which reads the actual page).
        running = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
        if f"graph:{page_name}" in running:
            logger.info(
                "webhook: ignoring task.assigned for %s (graph already running)",
                page_name,
            )
            return {"status": "ignored", "reason": "graph already running"}

        graph = request.app.state.graph
        thread_id = await _resolve_thread_id(page_name, data)
        initial_state = _build_initial_state(page_name, thread_id, data)
        config = {"configurable": {"thread_id": thread_id}}

        def _log_exc(t: asyncio.Task, name: str = page_name) -> None:
            if not t.cancelled() and (exc := t.exception()):
                logger.error("graph task %s failed: %s", name, exc, exc_info=exc)

        request.app.state.page_thread_map[page_name] = thread_id
        task = asyncio.create_task(
            graph.ainvoke(initial_state, config=config),
            name=f"graph:{page_name}",
        )
        task.add_done_callback(_log_exc)
        logger.info(
            "webhook: started graph task for %s (thread_id=%s)", page_name, thread_id
        )
        return {"status": "started"}

    if event == "task.approved":
        graph = request.app.state.graph
        thread_id = await _resolve_thread_id(page_name, data)
        config = {"configurable": {"thread_id": thread_id}}
        approval = data.get("approval", "approve")
        feedback = data.get("feedback")
        asyncio.create_task(
            graph.ainvoke(
                {"human_approval_response": approval, "human_feedback": feedback},
                config=config,
            ),
            name=f"graph:{page_name}:resume",
        )
        logger.info(
            "webhook: resumed graph task for %s (approval=%s)", page_name, approval
        )
        return {"status": "resumed"}

    if event == "task.rework":
        # CI fixer bot detected a failure and transitioned the task back to
        # in_progress. Resume the graph so the PM re-dispatches the grinder
        # with the CI failure context from the wiki page.
        if data.get("parent_task"):
            logger.debug(
                "webhook: ignoring task.rework for subtask %s (parent_task=%s)",
                page_name,
                data["parent_task"],
            )
            return {"status": "ignored", "reason": "subtask managed by parent graph"}
        graph = request.app.state.graph
        thread_id = await _resolve_thread_id(page_name, data)
        config = {"configurable": {"thread_id": thread_id}}
        asyncio.create_task(
            graph.ainvoke(
                {
                    "human_approval_response": "changes_requested",
                    "human_feedback": (
                        "CI failure detected on the PR. "
                        "See the ## CI Failure section on the wiki task page for "
                        "root cause and suggested fix, then push a corrected commit."
                    ),
                },
                config=config,
            ),
            name=f"graph:{page_name}:rework",
        )
        logger.info("webhook: resuming graph for CI rework on %s", page_name)
        return {"status": "rework"}

    if event == "task.pr_merged":
        # The GitHub→MeshWiki→orchestrator loop completes here.
        # The grind node will have already been notified; this event is
        # informational — no graph action required at this stage.
        logger.info(
            "webhook: PR merged for %s — GitHub loop complete",
            page_name,
        )
        return {"status": "acknowledged"}

    logger.debug("webhook: ignoring event=%s page=%s", event, page_name)
    return {"status": "ignored"}
