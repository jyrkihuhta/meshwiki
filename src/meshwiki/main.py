"""MeshWiki FastAPI application."""

import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    FastAPI,
    Form,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from meshwiki.auth import (
    AuthMiddleware,
    is_rate_limited,
    record_failed_attempt,
    reset_attempts,
    verify_password,
)
from meshwiki.config import settings
from meshwiki.core.graph import get_engine, init_engine, shutdown_engine
from meshwiki.core.models import Page
from meshwiki.core.parser import parse_wiki_content, parse_wiki_content_with_toc
from meshwiki.core.storage import FileStorage
from meshwiki.core.ws_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and shutdown graph engine."""
    init_engine(settings.data_dir, watch=settings.graph_watch)
    manager.start_polling()
    yield
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

# SessionMiddleware must be added last so it runs first (outermost)
if settings.auth_enabled:
    app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware, secret_key=settings.session_secret, https_only=False
)


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

# Initialize storage
storage = FileStorage(settings.data_dir)


# Template context helper
def get_context(**kwargs) -> dict:
    """Create base context for templates."""
    return {
        "app_title": settings.app_title,
        **kwargs,
    }


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
    return templates.TemplateResponse(
        request,
        "page/list.html",
        get_context(all_pages=all_pages, recent_pages=recent_pages),
    )


@app.get("/page/{name}", response_class=HTMLResponse)
async def view_page(request: Request, name: str):
    """View a wiki page."""
    page = await storage.get_page(name)

    if page is None:
        # Page doesn't exist - redirect to edit to create it
        return RedirectResponse(url=f"/page/{name}/edit", status_code=302)

    # Parse content with wiki links and TOC
    html_content, toc_html = parse_wiki_content_with_toc(
        page.content, page_exists=page_exists_sync
    )

    # Get backlinks and frontmatter metadata from graph engine
    backlinks: list[str] = []
    frontmatter: dict[str, list[str]] = {}
    engine = get_engine()
    if engine is not None:
        try:
            backlinks = sorted(engine.get_backlinks(name))
        except Exception:
            pass
        try:
            frontmatter = engine.get_metadata(name) or {}
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "page/view.html",
        get_context(
            page=page,
            html_content=html_content,
            toc_html=toc_html,
            backlinks=backlinks,
            frontmatter=frontmatter,
        ),
    )


@app.get("/page/{name}/edit", response_class=HTMLResponse)
async def edit_page(request: Request, name: str):
    """Edit page form."""
    page = await storage.get_page(name)

    if page is None:
        # New page
        page = Page(name=name, content="", exists=False)
        raw_content = ""
    else:
        raw_content = await storage.get_raw_content(name) or ""

    return templates.TemplateResponse(
        request,
        "page/edit.html",
        get_context(page=page, raw_content=raw_content),
    )


@app.post("/page/{name}", response_class=HTMLResponse)
async def save_page(request: Request, name: str, content: str = Form("")):
    """Save page content."""
    page = await storage.save_page(name, content)

    # Check if this is an HTMX request
    if request.headers.get("HX-Request"):
        # Return just the content area for HTMX swap
        html_content = parse_wiki_content(page.content, page_exists=page_exists_sync)
        backlinks: list[str] = []
        frontmatter: dict[str, list[str]] = {}
        engine = get_engine()
        if engine is not None:
            try:
                backlinks = sorted(engine.get_backlinks(name))
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
                frontmatter=frontmatter,
            ),
        )
        response.headers["HX-Trigger"] = json.dumps(
            {"showToast": {"message": "Page saved", "type": "success"}}
        )
        return response

    # Regular form submit - redirect to view
    return RedirectResponse(url=f"/page/{name}?toast=saved", status_code=302)


@app.get("/page/{name}/raw")
async def raw_page(name: str):
    """Get raw markdown content."""
    page = await storage.get_page(name)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"content": page.content}


@app.post("/page/{name}/delete")
async def delete_page(name: str):
    """Delete a page."""
    deleted = await storage.delete_page(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    return RedirectResponse(url="/?toast=deleted", status_code=302)


# ========== Editor API ==========


@app.post("/api/preview", response_class=HTMLResponse)
async def api_preview(content: str = Form("")):
    """Render markdown preview for the editor."""
    html = parse_wiki_content(content, page_exists=page_exists_sync)
    return HTMLResponse(html)


# Fields that cannot be edited inline
_PROTECTED_FIELDS = {"created", "modified", "name"}
_VALID_FIELD_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


@app.patch("/api/page/{name}/metadata")
async def api_update_metadata(
    name: str,
    field: str = Form(...),
    value: str = Form(""),
):
    """Update a single frontmatter field on a page."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid page name")
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
        get_context(results=results, query=q, tag=tag),
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
        get_context(tags=tags_sorted),
    )


# ========== Graph visualization ==========


@app.get("/graph", response_class=HTMLResponse)
async def graph_view(request: Request):
    """Graph visualization page."""
    return templates.TemplateResponse(
        request,
        "graph.html",
        get_context(),
    )


@app.get("/api/graph")
async def api_graph():
    """Return full graph as JSON for visualization."""
    engine = get_engine()
    if engine is None:
        return {"nodes": [], "links": []}

    pages = engine.list_pages()
    nodes = [{"id": p.name} for p in pages]

    links = []
    for page in pages:
        for target in engine.get_outlinks(page.name):
            links.append({"source": page.name, "target": target})

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


# ========== Auth ==========


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html", get_context())


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
        return RedirectResponse(url="/", status_code=302)

    record_failed_attempt(ip)
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
