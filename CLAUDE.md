# Claude Code Guidelines for MeshWiki

## Project Overview

MeshWiki is a modern wiki platform inspired by MoinMoin, Graphingwiki, and Obsidian. It combines:
- File-based Markdown storage
- Wiki links (`[[PageName]]` syntax)
- Kubernetes-native deployment with GitOps

**Tech Stack:** FastAPI, Jinja2, HTMX, Python 3.12+, Rust (graph engine), k3d, Istio, Rancher, Flux

## Key Documentation

Read these for full context:
- `TODO.md` - Current tasks, milestones, and progress
- `CONTRIBUTING.md` - Contributor guide (dev setup, coding standards, PR process)
- `docs/getting-started.md` - Setup and deployment guide
- `docs/architecture.md` - System design and components
- `docs/prd/002-meshwiki-mvp.md` - Application requirements and status

- `docs/prd/001-infrastructure.md` - Infrastructure requirements
- `docs/custom-macros.md` - How to create custom `<<Macro>>` extensions
- `docs/domains/*.md` - Domain-specific design docs (for subagents)

## Project Structure

```
dev.sh                  # Development startup script (build Rust + start server)
graph-core/             # Rust graph engine (Phase 3)
  src/lib.rs            # PyO3 entry point, GraphEngine class
  src/graph.rs          # petgraph WikiGraph
  src/parser.rs         # Frontmatter + wiki link parsing
  src/models.rs         # PageNode, WikiLink structs
  src/query.rs          # Filter enum, query(), metatable()
  src/events.rs         # GraphEvent enum, EventQueue
  src/watcher.rs        # FileWatcher with notify crate
  tests/                # Python integration tests (70 tests)

src/meshwiki/          # Python application
  main.py               # FastAPI routes + WebSocket endpoint
  config.py             # Settings (MESHWIKI_* env vars)
  core/storage.py       # FileStorage implementation (incl. search, tag filter)
  core/parser.py        # Markdown + wiki link + MetaTable + TOC parsing
  core/graph.py         # Graph engine wrapper (optional import)
  core/ws_manager.py    # WebSocket connection manager + event fanout
  templates/            # Jinja2 templates
  templates/partials/   # HTMX partial fragments (preview, search results)
  static/css/           # CSS
  static/js/graph.js    # D3.js graph visualization
  static/js/editor.js   # Editor toolbar, shortcuts, autocomplete, preview toggle
  tests/                # Unit tests (200 tests)
  e2e/                  # Playwright E2E browser tests (49 tests)

Dockerfile              # Multi-stage build (Rust + Python)
.github/workflows/      # CI (ci.yml), lint (lint.yml), stale (stale.yml), dependabot auto-merge
.github/ISSUE_TEMPLATE/ # Bug report + feature request templates
.github/pull_request_template.md
.github/dependabot.yml  # Dependency update config (pip, cargo, actions)
scripts/                # Utility scripts (remove-example-data.sh)
src/data/pages/         # Example wiki pages (11 pages)
docs/domains/           # Domain documentation for subagents

infra/local/            # Terraform for local k8s
  main.tf               # k3d cluster (uses null_resource, not provider)
  istio.tf              # Istio service mesh
  rancher.tf            # Rancher installation

deploy/apps/meshwiki/  # K8s manifests (Flux deploys these)
deploy/flux/            # Flux GitOps configuration
```

## Common Commands

```bash
# Local development with Rust engine (recommended)
./dev.sh                    # Build Rust engine + start server
./dev.sh --skip-build       # Start server without rebuilding Rust
./dev.sh --build-only       # Build Rust engine only

# Local development without Rust engine
cd src && uvicorn meshwiki.main:app --reload

# Build and deploy to k8s (Dockerfile at repo root)
docker build -t meshwiki:latest .
k3d image import meshwiki:latest -c meshwiki
kubectl rollout restart deployment/meshwiki -n meshwiki

# Check deployment
kubectl get pods -n meshwiki
kubectl logs -f deployment/meshwiki -n meshwiki

# Terraform (infrastructure)
cd infra/local && terraform apply

# Flux (force sync)
flux reconcile kustomization apps --with-source
```

## URLs (local k8s)

- http://wiki.localhost:8080 - MeshWiki
- https://rancher.localhost:8443 - Rancher
- http://test.localhost:8080 - Test app

Requires `/etc/hosts` entries for `*.localhost` domains.

## Development Standards

### Python Style
- **PEP 8** - Follow standard Python style guide
- **Type hints** - Required for all function signatures
- **Docstrings** - Google style for public functions/classes
- **Line length** - 88 characters (Black default)
- **Imports** - Use `isort` ordering (stdlib, third-party, local)
- **Formatting** - Use `black` for consistent formatting
- **Linting** - Use `ruff` for fast linting

### Naming Conventions
- `snake_case` for functions, variables, modules
- `PascalCase` for classes
- `UPPER_CASE` for constants
- Prefix private methods/attributes with `_`

### Error Handling
- Use specific exception types, not bare `except:`
- Raise `HTTPException` with appropriate status codes in routes
- Log errors with context before re-raising

### Testing Requirements
- **Write tests for all new features** - No feature is complete without tests
- **Test file naming** - `test_<module>.py` in `tests/` directory
- **Use pytest** - With pytest-asyncio for async code
- **Test coverage** - Aim for >80% on new code
- **Test types:**
  - Unit tests for core logic (storage, parser)
  - Integration tests for API routes (use httpx TestClient)
  - Edge cases and error conditions

Example test structure:
```python
# tests/test_storage.py
import pytest
from meshwiki.core.storage import FileStorage

@pytest.fixture
def storage(tmp_path):
    return FileStorage(tmp_path)

@pytest.mark.asyncio
async def test_save_and_get_page(storage):
    await storage.save_page("TestPage", "# Hello")
    page = await storage.get_page("TestPage")
    assert page is not None
    assert page.content == "# Hello"
```

### Code Quality Checklist
Before committing:
- [ ] Code follows PEP 8 style
- [ ] Type hints added
- [ ] Tests written and passing
- [ ] No hardcoded secrets or credentials
- [ ] Error handling is appropriate
- [ ] Docstrings for public APIs

### Git Commits
- Write clear, concise commit messages
- Use imperative mood ("Add feature" not "Added feature")
- Reference issues if applicable
- Keep commits focused and atomic

### Dependencies
- Add to `pyproject.toml` under `[project.dependencies]`
- Pin minimum versions (`>=X.Y`), not exact versions
- Dev dependencies go in `[project.optional-dependencies.dev]`

## Code Conventions

### Python Application
- Use async/await for all storage operations
- Settings via pydantic-settings with `MESHWIKI_` prefix
- Storage layer is abstract - `FileStorage` implements `Storage` ABC

### Markdown Parser
- Extensions configured in `core/parser.py`
- Wiki links: `WikiLinkExtension` (custom)
- Task lists: `pymdownx.tasklist`
- Strikethrough: `StrikethroughExtension` (custom)
- MetaTable: `MetaTableExtension` (custom preprocessor, queries graph engine, uses `htmlStash` for clean rendering, skips fenced code blocks)

### Templates
- Base template: `templates/base.html` (search box, theme toggle, hamburger nav, toast container, loading bar, highlight.js, `{% block extra_scripts %}`)
- Partials in `templates/partials/` for HTMX fragment responses
- HTMX for dynamic updates (check `HX-Request` header)
- Dark mode via `[data-theme="dark"]` CSS custom properties + localStorage (`meshwiki-theme`)
- highlight.js for syntax highlighting (CDN, light/dark theme switching)
- Minimal custom CSS, no framework

### Routes (new in M7/M8)
- `POST /api/preview` — Markdown preview for editor (HTMX)
- `GET /api/autocomplete?q=` — Wiki link autocomplete (max 10 results)
- `GET /search?q=&tag=` — Full search + tag filter (HTMX partial or full page)
- `GET /tags` — Tag index with counts
- `POST /page/{name}/delete` — Delete page (with browser `confirm()` dialog)

### Kubernetes
- All apps deployed via Flux from `deploy/apps/`
- Use Kustomize structure
- Istio VirtualService for routing

## Security Practices

- **No secrets in code** - Use environment variables or k8s secrets
- **Validate user input** - Sanitize page names, prevent path traversal
- **No SQL injection** - Use parameterized queries (when DB is added)
- **XSS prevention** - Jinja2 auto-escapes; be careful with `| safe`
- **CSRF protection** - Use tokens for state-changing operations (future)
- **Dependencies** - Keep updated, check for vulnerabilities

## Documentation Practices

- **Update docs with code** - If behavior changes, update relevant docs
- **ADRs for decisions** - Document significant technical decisions in `docs/adr/`
- **PRDs for features** - New features should have requirements in `docs/prd/`
- **Code comments** - Explain "why", not "what" (code shows what)
- **README updates** - Keep root README current with project status

## Architecture Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| k3d Terraform | `null_resource` + CLI | Provider unreliable (ADR-001) |
| Istio CRDs | `kubectl apply` via null_resource | Terraform CRD validation issues |
| Storage | Abstract class + FileStorage | Prepare for future DB backend |
| Frontend | HTMX + Jinja2 + D3.js | Server-rendered, D3 for graph viz |
| Real-time | WebSocket + asyncio fanout | Per-client queue, 0.5s poll interval |

## Gotchas

1. **k3d image import required** - Local images must be imported to k3d cluster
2. **Traefik disabled** - Using Istio ingress instead
3. **Flux deploys from Git** - Local manifest changes need commit+push (or manual kubectl apply)
4. **Markdown parser groups** - `SimpleTagInlineProcessor` expects text in group(2), not group(1)
5. **poll_events() drains queue** - Only the `ConnectionManager` singleton should call `poll_events()` to avoid losing events
6. **ASGITransport skips lifespan** - In tests, manually call `init_engine()`/`shutdown_engine()` instead of relying on FastAPI lifespan
7. **graph_core is optional** - `core/graph.py` uses `try/except ImportError`; app works without it
8. **Form fields default** - Use `Form("")` not `Form(...)` for optional/empty-allowed form fields; `Form(...)` rejects empty strings with 422
9. **Test fixture after reload** - `test_app_smoke.py` uses `importlib.reload(meshwiki.main)` which replaces `storage`. New test files must access `meshwiki.main.storage` dynamically (not via `from meshwiki.main import storage`) to survive reloads
10. **TOC guard** - Use `{% if toc_html and '<li>' in toc_html %}` to avoid showing empty TOC sidebar
11. **PageMetadata allows extras** - `model_config = ConfigDict(extra="allow")` so custom frontmatter fields (status, author, priority, etc.) survive the parse/save round-trip
12. **Editor shows raw content** - The edit template uses `{{ raw_content }}` (includes frontmatter) not `{{ page.content }}` (body only). The `get_raw_content()` method returns the full file.
13. **Preview toggle is JS-driven** - The editor textarea has no `hx-*` attributes in the template; `editor.js` adds them dynamically based on localStorage preference. Tests should not assert `hx-post` in server-rendered HTML.
14. **MetaTable uses htmlStash** - The `MetaTablePreprocessor` stashes rendered HTML via `self.md.htmlStash.store()` to prevent the Markdown block parser from wrapping tables in `<p>` tags
15. **MetaTable skips code blocks** - The preprocessor strips out fenced code blocks (`` ``` `` and `~~~`) before replacing macros, then restores them. MetaTable syntax inside code blocks renders as literal text.
16. **Dark mode flash prevention** - An inline `<script>` in `<head>` reads `localStorage('meshwiki-theme')` and sets `data-theme` on `<html>` before the stylesheet loads. This prevents a flash of wrong theme.
17. **Toast two-path approach** - Redirect flows use `?toast=saved`/`?toast=deleted` query params (JS reads and removes). HTMX flows use `HX-Trigger` response header with `showToast` event.
18. **highlight.js re-highlight on HTMX swap** - After HTMX content swaps (editor preview), code blocks must be re-highlighted via `htmx:afterSwap` event listener calling `hljs.highlightElement()`.
19. **Page list uses all_pages not pages** - The index route passes `all_pages` (list of `Page` objects from `list_pages_with_metadata()`) to the template for the metadata table. The old `pages` (list of names) is no longer used.
20. **timeago filter** - Custom Jinja2 filter registered on `templates.env.filters["timeago"]` for relative date display. Handles both naive and timezone-aware datetimes.
21. **E2E fixture scoping** - `base_url` and `data_dir` fixtures in `e2e/conftest.py` must be `scope="session"` to work with pytest-playwright's session-scoped browser fixtures. Function-scoped `clean_wiki` must access `e2e_server` dict directly (not the session-scoped `data_dir` fixture).
22. **Playwright fill() doesn't trigger keyup** - HTMX triggers like `hx-trigger="keyup changed delay:300ms"` won't fire from Playwright `fill()`. Use `dispatch_event("keyup")` or `type()` after filling to trigger HTMX requests in E2E tests.
23. **Dependabot auto-merge** - `.github/workflows/dependabot-auto-merge.yml` auto-merges non-major Dependabot PRs via `gh pr merge --auto --squash`.

## Completed Milestones (1–9)

Milestones 1–9 cover infrastructure, wiki MVP, Rust graph engine, editor experience, navigation/discovery, and visual polish. All complete.

**319 total tests passing** (70 graph-core + 200 Python unit + 49 Playwright E2E), CI pipeline active.

**See:** `docs/domains/graph-engine.md` for Rust engine design.

### Graph Engine Commands
```bash
cd graph-core
source ~/.cargo/env
source .venv/bin/activate
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
python -m pytest tests/ -v       # 70 tests

cd src
python -m pytest tests/ -v       # 204 tests
```

## Subagent Workflow

This project uses domain-based subagents for focused implementation work.

### Domain Documentation
Each domain has a dedicated doc in `docs/domains/`:

| Domain | Doc | Description |
|--------|-----|-------------|
| Graph Engine | `graph-engine.md` | Rust core, petgraph, PyO3 |
| Business Logic | `business-logic.md` | Python wiki functionality |
| Authentication | `authentication.md` | User auth (planned) |
| Infrastructure | `infrastructure.md` | k8s, deployment |
| Observability | `observability.md` | Logging, metrics |
| Testing | `testing.md` | Test strategy, CI/CD |

### Spawning Subagents
To work on a domain, spawn a subagent that reads the domain doc:
```
Task(subagent_type="general-purpose",
     prompt="Read docs/domains/<domain>.md and implement <task>")
```

The agent reads the domain doc for context, works autonomously, and reports back.

## Current Milestones (9–13)

**See:** `TODO.md` for full details and `docs/custom-macros.md` for the macro developer guide.

- Milestone 7: Editor Experience (live preview, toolbar, shortcuts) ✅
- Milestone 8: Navigation & Discovery (search, TOC sidebar, tags) ✅
- Milestone 9: Visual Polish & Responsiveness (dark mode, mobile, notifications) ✅
- Milestone 10: Graph Visualization Enhancements (search, focus mode, node sizing)
- Milestone 11: Macro System & Documentation (developer guide, built-in macros)
- Milestone 12: Authentication (user accounts, access control)
- Milestone 13: Observability (structured logging, metrics)

## Future Work

- Version history - Track page changes
- Graph persistence - Serialize graph to disk for fast startup

## Testing

```bash
cd src
pip install -e ".[dev]"
pytest
pytest --cov=meshwiki          # With coverage
pytest -x                        # Stop on first failure
pytest -k "test_storage"         # Run specific tests
```

Tests use pytest + pytest-asyncio + httpx for async API testing.

## Recommended Tools

```bash
# Install dev tools
pip install black ruff isort pytest pytest-asyncio pytest-cov httpx

# Format code
black src/
isort src/

# Lint
ruff check src/

# Type checking (optional)
pip install mypy
mypy src/meshwiki/
```

Add to `pyproject.toml` for consistent configuration:
```toml
[tool.black]
line-length = 88

[tool.isort]
profile = "black"

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "W"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```
