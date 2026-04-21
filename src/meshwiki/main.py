"""MeshWiki FastAPI application."""

import asyncio
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import (
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from meshwiki.auth import (
    AuthMiddleware,
    is_rate_limited,
    record_failed_attempt,
    reset_attempts,
    verify_password,
)
from meshwiki.config import settings
from meshwiki.core.dependencies import (
    get_revision_store,
    set_revision_store,
    set_storage,
)
from meshwiki.core.graph import get_engine, init_engine, shutdown_engine
from meshwiki.core.logging import configure_logging, get_logger
from meshwiki.core.metrics import (
    http_request_duration_seconds,
    http_requests_total,
    normalize_path,
    page_views_total,
    page_writes_total,
)
from meshwiki.core.models import Page
from meshwiki.core.parser import (
    FRONTMATTER_PATTERN,
    parse_wiki_content,
    parse_wiki_content_with_toc,
    word_count,
)
from meshwiki.core.revision_store import RevisionStore
from meshwiki.core.storage import FileStorage
from meshwiki.core.task_machine import TASK_TRANSITIONS
from meshwiki.core.task_machine import transition_task as _machine_transition
from meshwiki.core.ws_manager import manager

# Configure structured logging before anything else
configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and shutdown graph engine."""
    init_engine(settings.data_dir, watch=settings.graph_watch)
    manager.start_polling()
    if settings.factory_enabled:
        from meshwiki.core.webhooks import dispatcher

        await dispatcher.start()
    yield
    if settings.factory_enabled:
        from meshwiki.core.webhooks import dispatcher

        await dispatcher.stop()
    manager.stop_polling()
    shutdown_engine()


# Initialize app
app = FastAPI(
    title=settings.app_title,
    debug=settings.debug,
    lifespan=lifespan,
)

# Setup templates and static files
templates_path = Path(__file__).parent / "templates"
static_path = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(templates_path))
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


class LoggingMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that logs every request as a structured JSON line.

    Captures: method, path, status_code, duration_ms, request_id (UUID4).
    Also increments Prometheus HTTP counters/histograms.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 2)

        norm_path = normalize_path(request.url.path)
        method = request.method
        status_code = str(response.status_code)

        log.info(
            "http_request",
            request_id=request_id,
            method=method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        # Prometheus HTTP metrics
        http_requests_total.labels(
            method=method, path=norm_path, status_code=status_code
        ).inc()
        http_request_duration_seconds.labels(method=method, path=norm_path).observe(
            (time.monotonic() - start)
        )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self' wss:; "
            "font-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net;"
        )
        return response


# Middleware stack (added in reverse — last added runs outermost):
# LoggingMiddleware → SecurityHeadersMiddleware → SessionMiddleware → AuthMiddleware
if settings.auth_enabled:
    app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware, secret_key=settings.session_secret, https_only=False
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(LoggingMiddleware)


def timeago_filter(dt: datetime | None) -> str:
    """Convert datetime to relative time string."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = int(seconds // 60)
        return f"{m}m ago"
    elif seconds < 86400:
        h = int(seconds // 3600)
        return f"{h}h ago"
    elif seconds < 604800:
        d = int(seconds // 86400)
        return f"{d}d ago"
    else:
        return dt.strftime("%Y-%m-%d")


templates.env.filters["timeago"] = timeago_filter
templates.env.globals["word_count"] = word_count

# Initialize storage
_revision_store = (
    RevisionStore(settings.data_dir / ".revisions.db")
    if settings.history_enabled
    else None
)
storage = FileStorage(settings.data_dir, revision_store=_revision_store)
set_storage(storage)
if _revision_store is not None:
    set_revision_store(_revision_store)

# Mount factory API if enabled
if settings.factory_enabled:
    from meshwiki.api import router as api_v1_router

    app.include_router(api_v1_router)


# Template context helper
async def get_page_tree() -> list[dict]:
    """Get hierarchical page tree for sidebar navigation."""
    pages = await storage.list_pages_with_metadata()
    return build_page_tree_sync(pages)


def _is_hidden_page(page: Page) -> bool:
    """Return True for pages that should not appear in the sidebar."""
    if "/" in page.name:
        return True
    extra = page.metadata.model_extra or {}
    return extra.get("sidebar") is False


def build_page_tree_sync(pages: list[Page]) -> list[dict]:
    """Build a declarative hierarchy from ``children:`` frontmatter and ``parent_task:`` fields.

    Each node: {"name": str, "title": str, "children": list[dict], "level": int,
    "status": str, "stub": bool}

    - Pages with ``children:`` declare explicit child order.
    - Pages with ``parent_task:`` are implicitly appended as children of that parent.
    - Pages never declared as anyone's child are roots.
    - A page can appear under multiple parents (DAG).
    - Cycles are detected per-path; back-edges are dropped with a warning.
    - Missing children render as stub nodes (no page exists yet).
    - ``Home`` is pinned as the first root.
    """
    page_map: dict[str, Page] = {p.name: p for p in pages}

    # storage._path_to_name (storage.py) converts underscores to spaces when
    # loading pages from disk, so stored page names always use spaces.  Frontmatter
    # authors often write children: [Foo_Bar] with underscores.  _ref() normalises
    # both forms to spaces so all comparisons are consistent.
    def _ref(name: str) -> str:
        return name.replace("_", " ")

    # Build children_of from both explicit children: lists and parent_task: fields.
    children_of: dict[str, list[str]] = {}
    for page in pages:
        if _is_hidden_page(page):
            continue
        declared = page.metadata.children
        if declared:
            children_of[page.name] = [_ref(c) for c in declared]

    # Derive implicit children from parent_task: (append after explicitly declared ones).
    # Compare normalised names to prevent duplicates when a page is both in children:
    # (underscore form) and has parent_task: (space form) pointing at the same parent.
    for page in pages:
        if _is_hidden_page(page):
            continue
        extra = page.metadata.model_extra or {}
        parent = extra.get("parent_task")
        if parent:
            bucket = children_of.setdefault(_ref(parent), [])
            if _ref(page.name) not in {_ref(c) for c in bucket}:
                bucket.append(page.name)

    all_declared_children: set[str] = {
        child for kids in children_of.values() for child in kids
    }

    roots = [
        p
        for p in pages
        if not _is_hidden_page(p) and p.name not in all_declared_children
    ]
    roots.sort(key=lambda p: (p.name != "Home", p.name.lower()))

    def _node(page_name: str, level: int) -> dict:
        page = page_map.get(page_name)
        if page is None:
            return {
                "name": page_name,
                "title": page_name.replace("_", " "),
                "children": [],
                "level": level,
                "status": "",
                "stub": True,
            }
        extra = page.metadata.model_extra or {}
        status = extra.get("status", "") or ""
        if isinstance(status, list):
            status = status[0] if status else ""
        return {
            "name": page.name,
            "title": page.title,
            "children": [],
            "level": level,
            "status": status,
            "stub": False,
        }

    def _subtree(page_name: str, level: int, ancestors: frozenset[str]) -> dict | None:
        if page_name in ancestors:
            log.warning("page_tree_cycle_detected", page=page_name)
            return None
        node = _node(page_name, level)
        path = ancestors | {page_name}
        for child_name in children_of.get(page_name, []):
            child = _subtree(child_name, level + 1, path)
            if child is not None:
                node["children"].append(child)
        return node

    # Classify roots into sections: epics → "Factory", standalone factory tasks →
    # "Standalone Tasks", everything else → regular wiki tree.
    epic_roots: list[Page] = []
    standalone_task_roots: list[Page] = []
    wiki_roots: list[Page] = []

    for root in roots:
        page = page_map.get(root.name)
        if page is None:
            wiki_roots.append(root)
            continue
        extra = page.metadata.model_extra or {}
        page_type = extra.get("type")
        is_factory = extra.get("assignee") == "factory"
        has_children = bool(children_of.get(root.name))
        if page_type == "epic" or (is_factory and has_children and not page_type):
            epic_roots.append(root)
        elif (page_type == "task" or is_factory) and not extra.get("parent_task"):
            standalone_task_roots.append(root)
        else:
            wiki_roots.append(root)

    def _section(label: str, slug: str, section_roots: list[Page]) -> dict:
        children = []
        for root in section_roots:
            node = _subtree(root.name, 1, frozenset())
            if node is not None:
                children.append(node)
        return {
            "name": f"__section__{slug}",
            "title": label,
            "children": children,
            "level": 0,
            "status": "",
            "stub": False,
            "section": True,
        }

    tree: list[dict] = []
    for root in wiki_roots:
        node = _subtree(root.name, 0, frozenset())
        if node is not None:
            tree.append(node)

    if epic_roots:
        tree.append(_section("Factory", "factory", epic_roots))
    if standalone_task_roots:
        tree.append(_section("Standalone Tasks", "standalone", standalone_task_roots))

    # Orphan recovery: pages that form pure cycles (or are only referenced by
    # cycle members) are unreachable from normal roots. Surface them at the root
    # level so they don't silently vanish from the sidebar.
    def _mark_reachable(page_name: str, path: frozenset[str], seen: set[str]) -> None:
        if page_name in path or page_name in seen:
            return
        seen.add(page_name)
        for child in children_of.get(page_name, []):
            _mark_reachable(child, path | {page_name}, seen)

    reachable: set[str] = set()
    for root in roots:
        _mark_reachable(root.name, frozenset(), reachable)

    orphans = sorted(
        [p for p in pages if not _is_hidden_page(p) and p.name not in reachable],
        key=lambda p: (p.name != "Home", p.name.lower()),
    )
    for orphan in orphans:
        node = _subtree(orphan.name, 0, frozenset())
        if node is not None:
            tree.append(node)
    return tree


def get_context(**kwargs) -> dict:
    """Create base context for templates."""
    return {
        "app_title": settings.app_title,
        **kwargs,
    }


# ── Page name validation ──────────────────────────────────────────────────────


def _validate_page_name(name: str) -> None:
    """Raise HTTPException 400 for invalid or potentially dangerous page names."""
    if not name:
        raise HTTPException(status_code=400, detail="Invalid page name")
    # Block null bytes and backslashes (Windows path separator)
    if "\x00" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid page name")
    # Block slashes entirely — the MoC system uses flat page names; slash pages
    # are hidden from the sidebar with no warning, so creation should be rejected.
    if "/" in name:
        raise HTTPException(status_code=400, detail="Invalid page name")


# Page existence checker for parser
def page_exists_sync(name: str) -> bool:
    """Synchronous check if page exists (for parser callback).

    Uses graph engine if available, falls back to filesystem.
    """
    engine = get_engine()
    if engine is not None:
        try:
            return engine.page_exists(name)
        except Exception:
            pass
    return storage._get_path(name).exists()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page - list all pages."""
    all_pages = await storage.list_pages_with_metadata()
    recent_pages = sorted(
        [p for p in all_pages if p.metadata.modified],
        key=lambda p: p.metadata.modified,
        reverse=True,
    )[:10]
    page_tree = await get_page_tree()
    return templates.TemplateResponse(
        request,
        "page/list.html",
        get_context(
            all_pages=all_pages, recent_pages=recent_pages, page_tree=page_tree
        ),
    )


@app.get("/page/{name:path}/edit", response_class=HTMLResponse)
async def edit_page(request: Request, name: str, template: str = ""):
    """Edit page form."""
    _validate_page_name(name)
    page = await storage.get_page(name)

    if page is None:
        page = Page(name=name, content="", exists=False)
        if template:
            template_content = await storage.get_raw_content(template)
            if template_content:
                raw_content = FRONTMATTER_PATTERN.sub("", template_content)
            else:
                raw_content = ""
        else:
            raw_content = ""
    else:
        raw_content = await storage.get_raw_content(name) or ""

    return templates.TemplateResponse(
        request,
        "page/edit.html",
        get_context(
            page=page, raw_content=raw_content, page_tree=await get_page_tree()
        ),
    )


@app.get("/page/{name:path}/raw")
async def raw_page(name: str):
    """Get raw markdown content."""
    _validate_page_name(name)
    page = await storage.get_page(name)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"content": page.content}


@app.get("/page/{name:path}/history", response_class=HTMLResponse)
async def page_history(request: Request, name: str, page: int = 1):
    """Show revision history for a page."""
    _validate_page_name(name)
    if not settings.history_enabled:
        raise HTTPException(status_code=404, detail="History is disabled")
    store = get_revision_store()
    per_page = 25
    offset = (page - 1) * per_page
    revisions = store.list_revisions(name, limit=per_page, offset=offset)
    total = store.revision_count(name)
    return templates.TemplateResponse(
        request,
        "page/history.html",
        get_context(
            page_name=name,
            revisions=revisions,
            total=total,
            page=page,
            per_page=per_page,
            page_tree=await get_page_tree(),
        ),
    )


@app.get("/page/{name:path}/history/{rev:int}", response_class=HTMLResponse)
async def page_revision(request: Request, name: str, rev: int):
    """Show a specific past revision of a page."""
    _validate_page_name(name)
    if not settings.history_enabled:
        raise HTTPException(status_code=404, detail="History is disabled")
    store = get_revision_store()
    revision = store.get_revision(name, rev)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")

    html_content = parse_wiki_content(revision.content)

    total = store.revision_count(name)
    prev_rev = rev - 1 if rev > 1 else None
    next_rev = rev + 1 if rev < total else None

    return templates.TemplateResponse(
        request,
        "page/revision.html",
        get_context(
            page_name=name,
            revision=revision,
            html_content=html_content,
            prev_rev=prev_rev,
            next_rev=next_rev,
            page_tree=await get_page_tree(),
        ),
    )


@app.post("/page/{name:path}/restore/{rev:int}")
async def restore_page(name: str, rev: int):
    """Restore a page to a specific revision."""
    _validate_page_name(name)
    if not settings.history_enabled:
        raise HTTPException(status_code=404, detail="History is disabled")
    store = get_revision_store()
    old_revision = store.get_revision(name, rev)
    if old_revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    await storage.save_page(name, old_revision.content)
    latest = store.get_latest_revision(name)
    if latest is not None:
        with store._conn:
            store._conn.execute(
                "UPDATE revisions SET operation = 'restore', message = ? WHERE id = ?",
                (f"Restored from revision {rev}", latest.id),
            )
    log.info("page_restored", page=name, revision=rev)
    return RedirectResponse(
        url=f"/page/{name.replace(' ', '_')}?toast=restored", status_code=302
    )


@app.get("/page/{name:path}/diff/{rev_range}", response_class=HTMLResponse)
async def page_diff(request: Request, name: str, rev_range: str):
    """Show diff between two revisions.  Format: 'A..B' or single rev N (diffs N-1..N)."""
    _validate_page_name(name)
    if not settings.history_enabled:
        raise HTTPException(status_code=404, detail="History is disabled")
    store = get_revision_store()

    if ".." in rev_range:
        parts = rev_range.split("..", 1)
        try:
            rev_a, rev_b = int(parts[0]), int(parts[1])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid revision range")
    else:
        try:
            rev_b = int(rev_range)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid revision number")
        rev_a = rev_b - 1

    if rev_a < 1:
        raise HTTPException(
            status_code=400, detail="No earlier revision to compare against"
        )

    revision_a = store.get_revision(name, rev_a)
    revision_b = store.get_revision(name, rev_b)
    if revision_a is None or revision_b is None:
        raise HTTPException(status_code=404, detail="Revision not found")

    diff = store.diff_revisions(name, rev_a, rev_b)
    return templates.TemplateResponse(
        request,
        "page/diff.html",
        get_context(
            page_name=name,
            rev_a=rev_a,
            rev_b=rev_b,
            revision_a=revision_a,
            revision_b=revision_b,
            diff=diff,
            page_tree=await get_page_tree(),
        ),
    )


@app.get("/page/{name:path}", response_class=HTMLResponse)
async def view_page(request: Request, name: str):
    """View a wiki page."""
    # 301 redirect for legacy slash-path URLs (e.g. /page/Docs/Getting_Started → /page/Getting_Started).
    # Only handles one-level-deep legacy paths; deeper nesting is left to fall through.
    if "/" in name:
        parts = name.split("/")
        if len(parts) == 2:
            slug = parts[1]
            if slug:
                try:
                    _validate_page_name(slug)
                except HTTPException:
                    slug = ""
                if slug and storage._get_path(slug).exists():
                    qs = request.url.query
                    target = f"/page/{slug}" + (f"?{qs}" if qs else "")
                    return RedirectResponse(url=target, status_code=301)
    _validate_page_name(name)
    page = await storage.get_page(name)

    if page is None:
        # Page doesn't exist - redirect to edit to create it
        return RedirectResponse(url=f"/page/{name}/edit", status_code=302)

    log.info("page_viewed", page=name)
    page_views_total.labels(page=name).inc()

    # Get backlinks, outlinks and frontmatter metadata from graph engine first so
    # the TaskStatus macro can use them during parsing.
    backlinks: list[str] = []
    outlinks: list[str] = []
    frontmatter: dict = {}
    engine = get_engine()
    if engine is not None:
        try:
            backlinks = sorted(engine.get_backlinks(name))
        except Exception:
            pass
        try:
            outlinks = sorted(engine.get_outlinks(name))
        except Exception:
            pass
        try:
            frontmatter = engine.get_metadata(name) or {}
        except Exception:
            pass

    # page_metadata is passed to the parser for macro context.  It starts from
    # the engine result but falls back to storage-parsed model_extra when the
    # engine hasn't indexed this page yet (new page, or no graph_core).
    # `frontmatter` is kept engine-only so the Properties card only appears
    # when the engine is available.
    _storage_extra = (
        dict(page.metadata.model_extra)
        if hasattr(page.metadata, "model_extra") and page.metadata.model_extra
        else {}
    )
    page_metadata: dict = frontmatter if frontmatter else _storage_extra

    # For epic pages, fetch child tasks so <<EpicStatus>> can render them.
    page_type = page_metadata.get("type", "")
    if isinstance(page_type, list):
        page_type = page_type[0] if page_type else ""
    if page_type == "epic":
        all_pages_for_epic = await storage.list_pages_with_metadata()
        child_tasks = []
        for p in all_pages_for_epic:
            if p.metadata is None:
                continue
            meta = p.metadata.model_dump()
            extra = p.metadata.model_extra if hasattr(p.metadata, "model_extra") else {}
            # Tasks link to an epic via parent_task (factory standard) or parent_epic.
            parent_ref = (
                meta.get("parent_task")
                or extra.get("parent_task")
                or meta.get("parent_epic")
                or extra.get("parent_epic")
            )
            if isinstance(parent_ref, list):
                parent_ref = parent_ref[0] if parent_ref else None
            if parent_ref == name or p.name.startswith(name + "/"):
                status = meta.get("status") or extra.get("status") or "planned"
                if isinstance(status, list):
                    status = status[0] if status else "planned"
                child_tasks.append({"name": p.name, "title": p.title, "status": status})
        page_metadata["_child_tasks"] = child_tasks

    # Fetch recent pages for <<RecentChanges>> macro.
    all_pages_for_recent = await storage.list_pages_with_metadata()
    recent_pages = sorted(
        [p for p in all_pages_for_recent if p.metadata.modified],
        key=lambda p: p.metadata.modified,
        reverse=True,
    )

    # Fetch all page contents for <<Include>> macro.
    page_contents: dict[str, str] = {}
    if "<<Include(" in page.content:
        all_page_names = await storage.list_pages()
        pages = await asyncio.gather(*(storage.get_page(n) for n in all_page_names))
        page_contents = {
            n: p.content for n, p in zip(all_page_names, pages) if p is not None
        }

    # Parse content with wiki links, TOC, and page context for macros.
    html_content, toc_html = parse_wiki_content_with_toc(
        page.content,
        page_exists=page_exists_sync,
        page_name=name,
        page_metadata=page_metadata,
        recent_pages=recent_pages,
        page_contents=page_contents,
        page_modified=page.metadata.modified,
        pages=all_pages_for_recent,
    )

    return templates.TemplateResponse(
        request,
        "page/view.html",
        get_context(
            page=page,
            html_content=html_content,
            toc_html=toc_html,
            backlinks=backlinks,
            outlinks=outlinks,
            frontmatter=frontmatter,
            page_tree=await get_page_tree(),
        ),
    )


@app.post("/page/{name:path}/delete")
async def delete_page(name: str):
    """Delete a page."""
    _validate_page_name(name)
    deleted = await storage.delete_page(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    log.info("page_deleted", page=name)
    page_writes_total.labels(operation="delete").inc()
    return RedirectResponse(url="/?toast=deleted", status_code=302)


@app.post("/page/{name:path}", response_class=HTMLResponse)
async def save_page(request: Request, name: str, content: str = Form("")):
    """Save page content."""
    _validate_page_name(name)

    # C1: status changes on task/epic pages must go through the state machine so
    # webhooks fire and invalid transitions are rejected.
    pending_transition: tuple[str, str] | None = None
    if settings.factory_enabled:
        old_page = await storage.get_page(name)
        if old_page is not None:
            old_extras = old_page.metadata.model_extra or {}
            old_status = old_extras.get("status")
            old_type = old_extras.get("type")
            if old_status and old_type in {"task", "epic"}:
                new_meta, new_body = storage._parse_frontmatter(content)
                new_status = (new_meta.model_extra or {}).get("status")
                if new_status and new_status != old_status:
                    allowed = TASK_TRANSITIONS.get(old_status, [])
                    if new_status not in allowed:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Cannot transition from '{old_status}' to '{new_status}'. Allowed: {allowed}",
                        )
                    # Revert status in content — transition_task() will write it
                    setattr(new_meta, "status", old_status)
                    content = storage._create_frontmatter(new_meta) + new_body
                    pending_transition = (old_status, new_status)

    page = await storage.save_page(name, content)
    log.info("page_saved", page=name)
    page_writes_total.labels(operation="save").inc()

    if pending_transition:
        _, new_status = pending_transition
        await _machine_transition(storage, name, new_status)
        updated = await storage.get_page(name)
        if updated:
            page = updated

    # Check if this is an HTMX request
    if request.headers.get("HX-Request"):
        # Return just the content area for HTMX swap
        html_content = parse_wiki_content(page.content, page_exists=page_exists_sync)
        backlinks: list[str] = []
        outlinks_htmx: list[str] = []
        frontmatter: dict[str, list[str]] = {}
        engine = get_engine()
        if engine is not None:
            try:
                backlinks = sorted(engine.get_backlinks(name))
            except Exception:
                pass
            try:
                outlinks_htmx = sorted(engine.get_outlinks(name))
            except Exception:
                pass
            try:
                frontmatter = engine.get_metadata(name) or {}
            except Exception:
                pass
        response = templates.TemplateResponse(
            request,
            "page/view.html",
            get_context(
                page=page,
                html_content=html_content,
                backlinks=backlinks,
                outlinks=outlinks_htmx,
                frontmatter=frontmatter,
            ),
        )
        response.headers["HX-Trigger"] = json.dumps(
            {"showToast": {"message": "Page saved", "type": "success"}}
        )
        return response

    # Regular form submit - redirect to view
    return RedirectResponse(url=f"/page/{name}?toast=saved", status_code=302)


# ========== Editor API ==========


@app.get("/api/pages/{name:path}/preview", response_class=HTMLResponse)
async def api_page_preview(name: str):
    """Render a page for hover card preview."""
    page = await storage.get_page(name)
    if page is None:
        return HTMLResponse("")
    return HTMLResponse(page.content)


@app.post("/api/preview", response_class=HTMLResponse)
async def api_preview(content: str = Form("")):
    """Render markdown preview for the editor."""
    recent_pages = await storage.list_pages_with_metadata()
    html = parse_wiki_content(
        content,
        page_exists=page_exists_sync,
        recent_pages=recent_pages,
    )
    return HTMLResponse(html)


# Fields that cannot be edited inline
_PROTECTED_FIELDS = {"created", "modified", "name"}
_VALID_FIELD_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


@app.patch("/api/page/{name:path}/metadata")
async def api_update_metadata(
    name: str,
    field: str = Form(...),
    value: str = Form(""),
):
    """Update a single frontmatter field on a page."""
    _validate_page_name(name)
    if not _VALID_FIELD_RE.match(field):
        raise HTTPException(status_code=400, detail="Invalid field name")
    if field in _PROTECTED_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Field '{field}' cannot be edited inline",
        )

    page = await storage.update_frontmatter_field(name, field, value)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    return {"page": name, "field": field, "value": value, "success": True}


@app.get("/api/autocomplete", response_class=HTMLResponse)
async def api_autocomplete(request: Request, q: str = ""):
    """Return matching page names for wiki link autocomplete."""
    if not q:
        return HTMLResponse("")
    pages = await storage.list_pages()
    q_lower = q.lower()
    matches = [p for p in pages if q_lower in p.lower()][:10]
    items = "".join(
        f'<li class="autocomplete-item" data-value="{name}">{name}</li>'
        for name in matches
    )
    return HTMLResponse(f'<ul class="autocomplete-list">{items}</ul>' if items else "")


# ========== Search & Navigation ==========


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", tag: str = ""):
    """Search pages by query or tag."""
    if q or tag:
        log.info("search_queried", query=q, tag=tag)
    results = []
    if tag:
        pages = await storage.search_by_tag(tag)
        results = [
            {
                "name": p.name,
                "title": p.title,
                "snippet": p.content[:150].replace("\n", " "),
                "match_type": "tag",
            }
            for p in pages
        ]
    elif q:
        results = await storage.search_pages(q)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "partials/search_results.html",
            {"results": results, "query": q, "tag": tag},
        )
    return templates.TemplateResponse(
        request,
        "search.html",
        get_context(results=results, query=q, tag=tag, page_tree=await get_page_tree()),
    )


@app.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request):
    """Tag index page with counts."""
    pages = await storage.list_pages_with_metadata()
    tag_counts: dict[str, int] = {}
    for page in pages:
        for tag in page.metadata.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    tags_sorted = sorted(tag_counts.items(), key=lambda x: x[0].lower())
    return templates.TemplateResponse(
        request,
        "tags.html",
        get_context(tags=tags_sorted, page_tree=await get_page_tree()),
    )


# ========== Graph visualization ==========


@app.get("/graph", response_class=HTMLResponse)
async def graph_view(request: Request):
    """Graph visualization page."""
    return templates.TemplateResponse(
        request,
        "graph.html",
        get_context(page_tree=await get_page_tree()),
    )


@app.get("/api/graph")
async def api_graph():
    """Return full graph as JSON for visualization."""
    engine = get_engine()
    if engine is None:
        return {"nodes": [], "links": []}

    pages = engine.list_pages()
    nodes = []
    for p in pages:
        backlinks = engine.get_backlinks(p.name)
        tags = p.metadata.get("tags", [])
        nodes.append(
            {
                "id": p.name,
                "tags": tags,
                "backlinks_count": len(backlinks),
            }
        )

    links = []
    for page in pages:
        for target in engine.get_outlinks(page.name):
            links.append({"source": page.name, "target": target})

    # Add parent→child edges from declared children: frontmatter.
    # Normalise underscore→space so children: [Foo_Bar] matches stored page 'Foo Bar'.
    def _ref_graph(name: str) -> str:
        return name.replace("_", " ")

    page_ids = {p.name for p in pages}
    page_ids_norm = {_ref_graph(n): n for n in page_ids}  # normalised → canonical
    for page in pages:
        meta = page.metadata
        children = (
            meta.children if hasattr(meta, "children") else meta.get("children", [])
        )
        for child_name in children:
            canonical = page_ids_norm.get(_ref_graph(child_name))
            if canonical:
                links.append(
                    {"source": page.name, "target": canonical, "type": "parent"}
                )

    return {"nodes": nodes, "links": links}


@app.websocket("/ws/graph")
async def ws_graph(websocket: WebSocket):
    """WebSocket endpoint for real-time graph events."""
    await websocket.accept()
    client_id, queue = manager.connect()
    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(client_id)


@app.websocket("/ws/terminal/{name:path}")
async def ws_terminal(websocket: WebSocket, name: str):
    """WebSocket endpoint that streams live PTY output for a running task.

    Replays the full buffer to late-joining clients, then streams new chunks
    as they arrive.  Multiple concurrent connections are supported.

    When ``auth_enabled`` is True the client must hold a valid session cookie
    (obtained via the normal /login flow).  Unauthenticated connections are
    rejected with close code 1008 (policy violation) before any output is sent.
    """
    from meshwiki.core.terminal_sessions import (
        get_session,
        resolve_session_name,
        subscribe,
        unsubscribe,
    )

    if settings.auth_enabled:
        session = websocket.scope.get("session", {})
        if not session.get("authenticated"):
            await websocket.close(code=1008)
            return

    await websocket.accept()
    name = resolve_session_name(name)
    session = get_session(name)
    if session is None:
        await websocket.send_text(
            "\r\n\x1b[2m[no active terminal session for this task]\x1b[0m\r\n"
        )
        await websocket.close()
        return

    # Subscribe BEFORE snapshotting the buffer — no await between these two
    # lines so no chunk can slip through the gap (asyncio is cooperative).
    sub_q = subscribe(name)  # None if session already closed
    buffer_snapshot = list(session.buffer)

    sub_q_ref = sub_q  # keep reference for finally block
    try:
        # Replay full history
        for chunk in buffer_snapshot:
            await websocket.send_text(chunk)

        # If session was already closed when we connected, we're done
        if sub_q is None:
            return

        # Stream live chunks
        while True:
            chunk = await sub_q.get()
            if chunk is None:  # sentinel — task finished
                break
            await websocket.send_text(chunk)
    except WebSocketDisconnect:
        pass
    finally:
        if sub_q_ref is not None:
            unsubscribe(name, sub_q_ref)
        await websocket.close()


# ========== Auth ==========


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        request, "login.html", get_context(page_tree=await get_page_tree())
    )


@app.post("/login")
async def login(request: Request, password: str = Form("")):
    """Verify password and create session."""
    ip = request.client.host if request.client else "unknown"

    if is_rate_limited(ip):
        return templates.TemplateResponse(
            request,
            "login.html",
            get_context(error="Too many failed attempts. Try again later."),
            status_code=429,
        )

    if settings.auth_enabled and verify_password(password, settings.auth_password):
        reset_attempts(ip)
        request.session["authenticated"] = True
        log.info("login_success", ip=ip)
        return RedirectResponse(url="/", status_code=302)

    record_failed_attempt(ip)
    log.warning("login_failed", ip=ip)
    return templates.TemplateResponse(
        request,
        "login.html",
        get_context(error="Incorrect password."),
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ========== Health checks ==========

_start_time = time.monotonic()


@app.get("/health/live")
async def health_live():
    """Liveness probe — process is running."""
    return {"status": "ok", "uptime_seconds": round(time.monotonic() - _start_time, 1)}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe — app can serve traffic.

    Returns 503 if the data directory is unreadable. Graph engine absence
    degrades but does not fail readiness (it's optional).
    """
    checks: dict[str, str] = {}
    ok = True

    try:
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        list(data_dir.iterdir())
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e}"
        ok = False

    engine = get_engine()
    checks["graph_engine"] = "ok" if engine else "not_loaded"

    status = "ready" if ok else "degraded"
    return JSONResponse(
        {"status": status, "checks": checks}, status_code=200 if ok else 503
    )


# ========== Observability ==========


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint — exempt from auth."""
    engine = get_engine()
    if engine is not None:
        try:
            from meshwiki.core.metrics import graph_links_total, graph_pages_total

            graph_pages_total.set(engine.page_count())
            graph_links_total.set(engine.link_count())
        except Exception:
            pass

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
