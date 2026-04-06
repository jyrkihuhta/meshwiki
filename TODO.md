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
| 10 | **Graph Enhancements** — node search, focus mode, tooltips, sizing | ✅ Complete |
| 11 | **Macro System** — PageList, RecentChanges, BackLinks, PageCount macros | Planned |
| 12 | **Authentication** — user accounts, login/logout, access control | Planned |
| 13 | **Observability** — structured logging, metrics endpoint | Planned |
| S1 | **Staging Integration** — staging.wiki.penni.fi, `staging` branch, CI deploy job | 🔲 In Progress |
| F8 | **Factory v2: Gap Fixes** — cost tracking, concurrency control, httpx pooling, configurable PM model | Planned |
| F9 | **Factory v2: HBR + Scheduler** — resource tracking, budget enforcement, 24/7 heartbeat | Planned |
| F10 | **Factory v2: Live Visualization** — D3.js factory view, WebSocket task events, activity feed | Planned |
| F11 | **Factory v2: Stale PR Bot** — CI fixer bot that auto-creates fix tasks for failing factory PRs | Planned |

**Priority:** S1 → F8 → F9 → F10 → F11 → 11 → 12 → 13

**~390 tests passing** (70 graph-core + ~320 Python), CI pipeline active.

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

### Milestone 10: Graph Visualization Enhancements ✅
Make the graph view more useful for navigation and exploration.

- [x] Search/filter box on graph page (highlight matching nodes, fade others)
- [x] Legend explaining node colors and size scale (draggable)
- [x] Node sizing by backlink count (logarithmic scale, MIN_RADIUS=5, MAX_RADIUS=24)
- [x] Hover tooltip on nodes (page name, tags, backlink count)
- [x] "Focus mode" — double-click a node to show only its neighborhood; Escape/double-click bg to exit
- [x] Subpage edges — implicit dashed parent→child edges for pages with `/` in name; subpages cluster near parent
- [x] Short node labels — last path segment only; full name in tooltip
- [x] Flash cooldown — page_updated WebSocket events throttled to once per 2s per node

**Key files:** `static/js/graph.js`, `static/css/graph.css`, `templates/graph.html`, `main.py` (`/api/graph`)

### Milestone 11: Macro System & Documentation
Document the extension system and add useful built-in macros.

- [x] Write developer guide: `docs/custom-macros.md`
- [x] Add macro examples to sample wiki content (11 example pages with MetaTable usage)
- [ ] `<<PageList(tag=value)>>` macro — embed a filtered list of pages
- [ ] `<<RecentChanges(n=10)>>` macro — show recently modified pages
- [ ] `<<BackLinks>>` macro — inline backlinks (alternative to sidebar panel)
- [ ] `<<PageCount>>` macro — total page count for dashboards
- [ ] Live MetaTable refresh — wire WebSocket `page_updated` events to trigger HTMX re-fetch of MetaTable sections without full page reload
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

## CI/CD Improvements

### Local PR Testing (Implemented)
Run E2E tests locally against a PR branch before approving.

```bash
./scripts/test-pr.sh <PR_NUMBER>
```

**What it does:**
1. Fetches the PR branch
2. Spins up a local MeshWiki server
3. Runs Playwright E2E tests against localhost
4. Returns to original branch

**Why it helps:** Catches CSS/layout bugs before they reach production.

---

### Milestone S1: Staging Integration 🔲 In Progress

Fast-iteration environment at `staging.wiki.penni.fi`. Same production image, source code volume-mounted + `--reload`. Push to `staging` branch → VPS git pull → uvicorn auto-reloads in ~2 seconds. No Docker rebuild needed.

**Integration Branch Pattern:** Grinders clone from and PR into `staging`. Git is the communication channel — grinder B inherits grinder A's merged work. Human gate only at `staging → main`.

- [x] DNS: `staging.wiki.penni.fi` → 135.181.38.57 (confirmed)
- [x] `staging` branch created and pushed to origin
- [x] `deploy/vps/Caddyfile` — `staging.${VPS_DOMAIN}` block added
- [x] `deploy/vps/docker-compose.prod.yml` — `meshwiki-staging` service + `orchestrator_data` volume
- [x] `deploy/vps/staging.env.example` — staging env template
- [x] `.github/workflows/ci.yml` — `staging` branch trigger, `deploy-staging` job
- [ ] First actual push to `staging` branch to trigger CI deploy
- [ ] Verify `staging.wiki.penni.fi` is live and healthy
- [ ] Grinder changes: clone from `staging`, PR targets `staging`, auto-merge after PM approval
- [ ] E2B Tier 1 template: pre-baked Node.js 20 + Kilo CLI (saves ~2 min per run)
- [ ] E2B Tier 2 (when private beta access granted): volume repo mirror for ~5s bootstrap

**E2B Speed Tiers:**
- Tier 1 (available now): `e2b template build` with Node.js + Kilo pre-installed, `FACTORY_E2B_TEMPLATE_ID` config
- Tier 2 (private beta): volume API to mirror the repo into sandboxes (read-only, ~5s)
- Tier 3 (future): pause/resume warm sandbox pool (<2s)

**Key files:** `.github/workflows/ci.yml`, `deploy/vps/docker-compose.prod.yml`, `deploy/vps/Caddyfile`

---

## Agent Factory v2 Plan

### Milestone F8: Fix v1 Gaps

- [ ] **Cost tracking** — `factory/cost.py`: `estimate_cost(model, input_tokens, output_tokens)` price table + `CostAccumulator`. PM node reads `response.usage`, grinder tracks sandbox wall-clock × `e2b_cost_per_hour` (default $0.20). Write `cost_usd` back in `grind.py` and `pm_review.py`.
- [ ] **Concurrency control** — `factory/config.py`: `max_concurrent_sandboxes: int = 3`. `assign.py`: cap dispatches at limit. `grind_node`: populate/clear `active_grinders`.
- [ ] **httpx client pooling** — `meshwiki_client.py` + `github_client.py`: accept optional shared `httpx.AsyncClient`; webhook server lifespan creates shared clients.
- [ ] **Configurable PM model** — `factory/config.py`: `pm_model: str = "claude-sonnet-4-6"`. `pm_agent.py`: use `settings.pm_model`.
- [ ] **Grinder uses review feedback** — `grinder_agent.py`: append `subtask["review_feedback"]` to `/tmp/task.md` on rework iterations.

### Milestone F9: HBR + Scheduler

- [ ] **`factory/hbr.py`** — `HbrManager`: tracks `active_sandboxes`, `daily_cost_usd`, enforces `daily_budget_usd`. Config: `daily_budget_usd: float = 50.0`.
- [ ] **`factory/scheduler.py`** — heartbeat task (60s tick): resets daily budget at midnight UTC, scans for `status: approved + assignee: factory` tasks not yet picked up, triggers graph if resources available.
- [ ] **`GET /hbr/status`** endpoint — exposes utilization data.
- [ ] **`assign.py`** checks `hbr.can_allocate_sandbox()` before dispatching.

### Milestone F10: Live D3.js Factory Visualization

All changes in `src/meshwiki/`.

- [ ] **`core/factory_ws_manager.py`** — push-based WebSocket manager + activity ring buffer (maxlen=500).
- [ ] **`core/task_machine.py`** — broadcast `task_transition` events to factory WS after each transition.
- [ ] **`api/tasks.py`** — `GET /api/factory/graph` (nodes + links snapshot) + `GET /api/factory/activity` (ring buffer).
- [ ] **`templates/factory_live.html`** — D3 force graph + 350px detail panel + 120px activity feed.
- [ ] **`static/js/factory.js`** (~500 lines) — WebSocket events, force simulation, node color by status, click→detail panel.
- [ ] **`static/css/factory.css`** (~200 lines) — layout, slide-in panel, animations, dark mode.
- [ ] **`templates/base.html`** — conditional "Factory" nav link when `factory_enabled`.

### Milestone F11: Stale PR Bot

- [ ] **`factory/bots/stale_pr_bot.py`** — scans open `factory/*` PRs with failing CI >30 min; creates fix-it task pages via MeshWikiClient; max 2 attempts per PR.
- [ ] **`factory/config.py`** — `stale_pr_bot_enabled: bool = False`, `stale_pr_bot_interval_seconds: int = 300`.
- [ ] **`factory/scheduler.py`** — call `stale_pr_bot.scan()` on tick interval.
- [ ] Safety guards: only touches `factory/*` branches, checks for existing fix tasks, respects HBR budget.

---

## Agent Factory Backlog (v1 Leftovers)

- [x] **PM uses Sonnet** — switched from Opus 4 to Sonnet 4.6 (~$3 → much cheaper per job)
- [x] **Grinder auto-transitions task** — grinder node calls `transition_task()` after grind, records `pr_url` and `branch`
- [x] **SQLite checkpointer** — `AsyncSqliteSaver` in orchestrator, persistent across restarts
- [x] **Webhook handler decoupled** — `task.assigned` uses `asyncio.create_task`, returns immediately
- [ ] **Signed grinder commits** — factory commits show as "unverified". Fix: GitHub App installation token (commits attributed to app + signed), or GPG key in E2B sandbox.
- [ ] **Bookkeeper bot** — periodic job reconciling stale task states: `in_progress` with no active PTY → `failed`; `review` with merged PR → `merged`; `review` with closed PR → `failed`.

---

## Notes

- Start with in-memory graph, add persistence later
- Focus on correctness over performance initially
- Keep Python as the primary interface; Rust is an implementation detail
- Python 3.14 requires ABI3 forward compatibility flag for PyO3
