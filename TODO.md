# MeshWiki Development Roadmap

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Rust Foundation (Maturin, PyO3) | ✅ Complete |
| 2 | Graph Core (petgraph, backlinks, link parsing) | ✅ Complete |
| 3 | Query Engine (Filter, query(), metatable()) | ✅ Complete |
| 4 | File Watching (notify crate, live updates) | ✅ Complete |
| 5 | Python Integration (backlinks panel, MetaTable macro, frontmatter) | ✅ Complete |
| 6 | Real-time Visualization (D3.js, WebSocket, live graph) | ✅ Complete |
| — | Infrastructure (Dockerfile, CI, 87% coverage) | ✅ Complete |
| 7 | **Editor Experience** — live preview, toolbar, shortcuts, autocomplete | ✅ Complete |
| 8 | **Navigation & Discovery** — search, TOC sidebar, tags, recent changes | ✅ Complete |
| 9 | **Visual Polish** — dark mode, mobile responsive, notifications, code highlighting | ✅ Complete |
| 10 | **Graph Enhancements** — node search, focus mode, tooltips, sizing | Planned |
| 11 | **Macro System** — PageList, RecentChanges, BackLinks, PageCount macros | Planned |
| 12 | **Authentication** — user accounts, login/logout, access control | Planned |
| 13 | **Observability** — structured logging, metrics endpoint | Planned |

**Priority:** 7 → 8 → 11 → 9 → 10 → 12 → 13

**274 tests passing** (70 graph-core + 204 Python), CI pipeline active.

---

## Milestone Details

### Milestone 7: Editor Experience ✅
Upgrade the editor from a plain textarea to a productive writing environment.

- [x] Split-pane editor with live Markdown preview (HTMX `hx-trigger="keyup changed delay:300ms"` to render server-side)
- [x] Markdown toolbar (bold, italic, heading, link, wiki link, code, strikethrough buttons)
- [x] Keyboard shortcuts in editor (Ctrl+B bold, Ctrl+I italic, Ctrl+K link, Ctrl+S save, Ctrl+P toggle preview)
- [x] Auto-growing textarea (fit content height)
- [x] Wiki link autocomplete (type `[[` to get page name suggestions via HTMX)
- [x] Unsaved changes warning (beforeunload)
- [x] Optional live preview toggle (button + Ctrl+P, persisted in localStorage)
- [x] Frontmatter preservation in editor (raw content with frontmatter shown in textarea)
- [x] MetaTable rendering fix (htmlStash to prevent extra `<p>` tags, proper CSS styling)
- [x] MetaTable skips fenced code blocks (not rendered inside `` ``` `` or `~~~`)

**Key files:** `templates/page/edit.html`, `static/js/editor.js`, `main.py` (preview + autocomplete endpoints)

### Milestone 8: Navigation & Discovery ✅
Help users find and move between pages efficiently.

- [x] Search box in header with instant results (HTMX, searches page names + content)
- [x] Search results page (`/search?q=...`)
- [x] Table of contents sidebar on page view (leverage existing `toc` Markdown extension)
- [x] Breadcrumb-style page path in view header
- [x] "Recently modified" section on home page
- [x] Clickable tags in page view that filter to `/search?tag=...`
- [x] Tag index page (`/tags`) showing all tags with page counts

**Key files:** `templates/base.html` (search), `main.py` (search/tags routes), `templates/page/view.html` (TOC), `templates/tags.html`, `templates/search.html`

### Milestone 9: Visual Polish & Responsiveness ✅
Elevate the visual design and make it work on all screen sizes.

- [x] Dark mode toggle with CSS custom properties (persist choice in localStorage)
- [x] Responsive breakpoints (mobile nav hamburger, stacked layouts below 768px)
- [x] Toast notifications for save/delete/error feedback (HTMX `HX-Trigger` + query params)
- [x] Delete confirmation dialog (`confirm()` on delete button in page view)
- [x] Improved page list with metadata columns (modified date, tag pills, word count)
- [x] Syntax highlighting for fenced code blocks (highlight.js with dark/light themes)
- [x] Smooth page transitions and loading states (loading bar, spinner, fade-in)

**Key files:** `static/css/style.css` (dark theme, responsive, toast, loading), `templates/base.html` (theme toggle, hamburger, toast, loading bar, highlight.js), `main.py` (timeago filter, toast redirects)

### Milestone 10: Graph Visualization Enhancements
Make the graph view more useful for navigation and exploration.

- [ ] Search/filter box on graph page (highlight matching nodes, fade others)
- [ ] Legend explaining node colors
- [ ] Node sizing by connection count (more links = larger node)
- [ ] Selected node detail panel (shows metadata, backlinks, outlinks)
- [ ] Hover tooltip on nodes (page name + tag preview)
- [ ] "Focus mode" — click a node to show only its neighborhood (n-hop subgraph)

**Key files:** `static/js/graph.js`, `templates/graph.html`, `static/css/style.css`

### Milestone 11: Macro System & Documentation
Document the extension system and add useful built-in macros.

- [x] Write developer guide: `docs/custom-macros.md`
- [x] Add macro examples to sample wiki content (11 example pages with MetaTable usage)
- [ ] `<<PageList(tag=value)>>` macro — embed a filtered list of pages
- [ ] `<<RecentChanges(n=10)>>` macro — show recently modified pages
- [ ] `<<BackLinks>>` macro — inline backlinks (alternative to sidebar panel)
- [ ] `<<PageCount>>` macro — total page count for dashboards
**Key files:** `core/parser.py` (new extensions), `docs/custom-macros.md`

### Milestone 12: Authentication
Add user accounts and access control.

- [ ] Design auth approach (session vs JWT vs OAuth)
- [ ] Implement user model and storage
- [ ] Add login/logout routes
- [ ] Protect edit/delete routes
- [ ] Add user info to templates

**Key files:** `core/auth.py` (new), `templates/auth/` (new), `main.py`

### Milestone 13: Observability
Add structured logging and metrics for production readiness.

- [ ] Add structured logging (structlog)
- [ ] Add basic metrics endpoint (`/metrics`)
- [ ] Document logging conventions

**Key files:** `main.py`, `core/logging.py` (new)

---

## Success Criteria

- [x] Editor has live preview and toolbar
- [x] Users can search pages by name and content
- [x] Dark mode works with one click
- [x] Mobile layout is usable
- [ ] Developer docs explain how to create custom macros
- [ ] At least 3 new built-in macros available
- [ ] Users can log in and edits are attributed
- [ ] Structured logs with request context

---

## Development Approach

This project uses a **domain-based subagent architecture**:

- **This level (main conversation):** Architecture decisions, coordination, progress tracking
- **Subagents:** Focused implementation work on specific domains

### Domain Documentation

Each domain has a dedicated doc in `docs/domains/` that subagents read for context:

| Domain | Doc | Description |
|--------|-----|-------------|
| Graph Engine | `graph-engine.md` | Rust core, petgraph, PyO3 bindings |
| Business Logic | `business-logic.md` | Python wiki functionality |
| Authentication | `authentication.md` | User auth (Milestone 12) |
| Infrastructure | `infrastructure.md` | k8s, deployment |
| Observability | `observability.md` | Logging, metrics (Milestone 13) |
| Testing | `testing.md` | Test strategy, CI/CD |

### Spawning Subagents

```
Task(subagent_type="general-purpose", prompt="Read docs/domains/<domain>.md and implement <task>")
```

---

## Future: Kubernetes Deployment

The current production setup is Docker Compose + Caddy on a single VPS — simple, cheap, and sufficient for one service. The K8s scaffolding (`deploy/apps/`, `deploy/flux/`, `infra/local/`) is kept for when it becomes worth the complexity.

**When to revisit:** The natural trigger is the agent factory. Once a second service exists (the LangGraph orchestrator), K8s starts paying for itself.

**Benefits K8s brings that VPS lacks:**

| Benefit | Why it matters |
|---------|----------------|
| Multi-service orchestration | K8s manages MeshWiki + orchestrator + workers + DB as a single system with shared networking and secrets |
| Horizontal scaling | Multiple replicas behind a load balancer; auto-scale on traffic |
| Zero-downtime deploys | Rolling updates are native; current VPS has a brief restart gap even with health checks |
| Self-healing | Automatic restart and rescheduling on node failure |
| Service mesh (Istio) | mTLS between services, traffic management, observability — all without application changes |
| GitOps (Flux) | Declarative state in git, drift detection, automatic reconciliation — already scaffolded |
| Observability stack | Prometheus + Grafana + Loki deploy as Helm charts; integrates with the `/metrics` endpoint |

**Current VPS advantages to keep in mind:**
- ~5 minute deploy vs K8s cluster setup overhead
- No control plane cost
- Caddy handles HTTPS with zero config

## Notes

- Start with in-memory graph, add persistence later
- Focus on correctness over performance initially
- Keep Python as the primary interface; Rust is an implementation detail
- Python 3.14 requires ABI3 forward compatibility flag for PyO3
