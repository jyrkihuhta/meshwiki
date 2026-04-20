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
| 11 | **Macro System** — PageList, RecentChanges, BackLinks, PageCount, Include, NewPage macros | 🔄 In Progress |
| 12 | **Authentication** — user accounts, login/logout, access control | Planned |
| 13 | **Observability** — structured logging, metrics endpoint | Planned |
| 14 | **Version History** — SQLite revisions, diff view, restore | ✅ Complete |
| S1 | **Staging Integration** — `staging` branch, grinders → staging, auto-merge, E2B template | ✅ Complete |
| F8 | **Factory v2: Gap Fixes** — cost tracking, concurrency control, bookkeeper bot | 🔲 Planned |
| F9 | **Factory v2: HBR Manager** — resource tracking, daily budget, 24/7 scheduler | 🔲 Planned |
| F10 | **Factory v2: Live Visualization** — D3.js factory graph, `/factory/live`, WebSocket | 🔲 Planned |
| F11 | **Factory v2: Stale PR Bot** — autonomous CI failure fixer | 🔲 Planned |

**Priority:** S1 → F8 → F9 → F10 → F11 → 11 → 12 → 13

**~390 tests passing** (70 graph-core + ~321 Python), CI pipeline active.

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

### Milestone S1: Staging Integration ✅ COMPLETE

Staging factory is fully operational. Multiple successful grinder tasks merged.

**Completed:**
- [x] Staging container + Caddy routing at `staging.wiki.penni.fi`
- [x] `staging` branch in repo; CI triggers on push to `staging` (separate deploy job)
- [x] Grinder clones from `staging`, PRs target `staging` (`FACTORY_PR_BASE_BRANCH=staging`)
- [x] Auto-merge after PM approval (`FACTORY_AUTO_MERGE=true`)
- [x] E2B template `meshwiki-grinder` pre-baked (Node.js 20 + Kilo + gh + Python tools) — ~5s bootstrap
- [x] FACTORY_ANTHROPIC_API_KEY in GitHub secrets (survives CI redeploys)
- [x] Rebase before PR creation (avoids merge conflicts from concurrent grinders)
- [x] CLAUDE.md gotcha #28: asyncio.run() in preprocessors is fatal
- [x] TASK001 (PageCount macro) — grinder implemented, merged ✅
- [x] TASK002 (BackLinks macro) — grinder implemented, merged ✅
- [x] PageList macro fix — asyncio.run() replaced with Pattern B (constructor injection) ✅
- [x] PM retry logic — 30s exponential backoff on Anthropic 529, MiniMax M2.7 fallback ✅
- [x] PM review resilience — fail-fast on empty feedback, exception marks subtask failed ✅
- [x] `merged → done` transition fix — pm_review_node transitions wiki page after auto-merge ✅
- [x] Staging orchestrator source mount — `orchestrator-staging` mounts live source code (no Docker rebuild needed)
- [x] Include macro (<<Include(PageName)>>) — full circular detection, E2E verified ✅
- [x] NewPage macro (<<NewPage(Template, Label, Parent)>>) — E2E verified with full pipeline ✅

**Key files:** `orchestrator/factory/agents/grinder_agent.py`, `orchestrator/e2b.Dockerfile`, `.github/workflows/ci.yml`, `orchestrator/factory/config.py`, `deploy/vps/docker-compose.prod.yml`

### Milestone F8: Factory v2 — Gap Fixes 🔲
Fix correctness and reliability issues in the v1 orchestrator.

**Cost & resource control (highest impact)**
- [x] **F8.1** Concurrency control — cap `route_grinders` at `FACTORY_MAX_CONCURRENT_SANDBOXES` (3), populate `active_grinders` in `grind_node`
- [x] **F8.2** Cost tracking — `cost_usd` never incremented; add `factory/cost.py`, read `response.usage` from PM agent calls, track E2B wall-clock time

**Correctness**
- [x] **F8.3** Fan-in state merge bug — when parallel grinders finish at different times, `collect_results` sees a merged subtasks list that drops the failed status of earlier-finishing subtasks; investigate LangGraph reducer annotation on `subtasks` / `failed_subtask_ids` fields in `FactoryState` and add an `Annotated` reducer so all branch updates are correctly combined
- [x] **F8.4** Per-subtask PM review — currently PM review waits for ALL grinders to finish before reviewing any; restructure so each grind instance fans out to its own `pm_review` via `Send()`, unblocking fast subtasks from slow rework cycles
- [x] **F8.5** PM review feedback visible on wiki — `append_to_page` in `pm_review_node` isn't writing feedback to the task page; PM review decisions should be visible in the Agent Log section

**Reliability**
- [x] **F8.6** Bookkeeper bot — periodic job reconciling stale task states (stuck in_progress → failed, merged PRs → merged)
- [ ] **F8.7** Unit tests for routing functions — `route_after_grinding`, `route_grinders` file-overlap, `route_after_pm_review`

**Performance**
- [ ] **F8.8** Pre-bake Python deps into E2B template — `pip install -e '.[dev]'` runs from scratch every grind session; baking deps into the `meshwiki-grinder` E2B template snapshot would make this near-instant and eliminate a large chunk of per-run disk/time overhead (requires rebuilding the template via `e2b template build`)

**Security**
- [ ] **F8.9** GitHub webhook secret on staging — `MESHWIKI_GITHUB_WEBHOOK_SECRET` is empty in staging env, so HMAC verification is skipped; anyone can POST fake "PR merged" events to trigger task page transitions
- [ ] **F8.10** Auth-gate `/ws/terminal/{name}` WebSocket — currently unauthenticated; anyone who knows (or guesses) a task page name can read live grinder terminal output, which may include API keys, tokens, or repo contents streamed via Kilo

**Efficiency**
- [ ] **F8.11** httpx clients — share a single `httpx.AsyncClient` per session in `MeshWikiClient` and `GitHubClient`

**Larger refactors / lower priority**
- [ ] **F8.12** Stable page identity via UUID — terminal session keys, WebSocket lookups, and graph thread IDs all use the fragile human-readable page name (spaces/underscores/special chars cause mismatch bugs). Root cause: wiki URLs encode spaces as underscores, but page names can also contain real underscores (e.g. `get_engine()`), making the two indistinguishable in a URL. Add a `uuid` frontmatter field generated on page creation; use it as the canonical key everywhere internally, keeping the page name only for display/URL routing
- [ ] **F8.13** Redecompose escalation — implement `"redecompose"` decision in `escalate_node`
- [ ] **F8.14** Signed grinder commits — GitHub App token or GPG key in E2B sandbox
- [ ] **F8.15** Persist grinder terminal output and run a review bot — terminal chunks are currently streamed to the browser and discarded; storing them (e.g. appended to the task wiki page or a sidecar log file) would enable a post-run bot to analyze patterns across sessions: recurring lint failures, commands that always fail first try, slow steps, Kilo confusion about tool use. Bot output could feed back into improved task prompts, better bootstrap steps, or a "known issues" section in CLAUDE.md
- [ ] **F8.16** `/api/graph`, `/ws/graph`, `/metrics` are unauthenticated — exposes all page names, links, and per-page view counts to anonymous users; acceptable for now but worth locking down before any public exposure

**Completed**
- [x] Configurable PM model — `FACTORY_PM_DECOMPOSE_MODEL`, `FACTORY_PM_REVIEW_MODEL`, `FACTORY_PM_TRIAGE_MODEL` env vars
- [x] Review feedback in rework — `subtask["review_feedback"]` appended to Kilo task prompt on rework iterations
- [x] PM review token cost — diff capped at `FACTORY_PM_REVIEW_MAX_DIFF_LINES` (default 500), two-pass triage via `FACTORY_PM_TRIAGE_MODEL` (Haiku fast-path; escalates to Sonnet only when triage requests changes)
- [x] Deferred subtask routing bug — `route_after_grinding` now loops back to `assign_grinders` when pending subtasks remain
- [x] Spurious `task.assigned` restart from subtask transitions — webhook_server checks `data["parent_task"]` and ignores `task.assigned` for subtask pages
- [x] E2B sandbox disk optimisation — `git clone --depth 1` + `pip install --no-cache-dir` in grinder_agent.py
- [x] Orchestrator dep install in grinder — `pip install -e '.[dev]'` added to bootstrap (GrinderBootstrap PR #126)

**Key files:** `orchestrator/factory/cost.py` (new), `orchestrator/factory/nodes/assign.py`, `orchestrator/factory/agents/pm_agent.py`, `orchestrator/factory/agents/grinder_agent.py`, `orchestrator/factory/integrations/`

### Milestone F9: Factory v2 — HBR Resource Manager 🔲
Internal resource tracking and 24/7 heartbeat scheduler.

- [ ] `factory/hbr.py` — track active sandboxes, daily cost vs budget, per-model API usage
- [ ] `assign.py` checks `hbr.can_allocate_sandbox()` before dispatching
- [ ] `factory/scheduler.py` — 60s heartbeat: reset budget at midnight, scan approved tasks that weren't webhook-triggered, dispatch if resources available
- [ ] `GET /hbr/status` endpoint — active sandboxes, daily cost, budget remaining

**Key files:** `orchestrator/factory/hbr.py` (new), `orchestrator/factory/scheduler.py` (new), `orchestrator/factory/webhook_server.py`

### Milestone F10: Factory v2 — Live D3.js Visualization 🔲
Real-time factory activity view using D3.js, same visual language as the wiki graph.

- [ ] `core/factory_ws_manager.py` — push-based WebSocket manager + 500-entry activity ring buffer
- [ ] `core/task_machine.py` — broadcast task transitions to factory WS after webhook emit
- [ ] `GET /ws/factory` WebSocket endpoint
- [ ] `GET /api/factory/graph` and `GET /api/factory/activity` REST endpoints
- [ ] `/factory/live` page: D3 force graph (task circles colored by status, agent diamonds, dashed parent edges), detail panel (slide-in right, terminal embed for in_progress), activity feed strip at bottom
- [ ] `base.html` — add conditional "Factory" nav link when `factory_enabled`

**Key files:** `core/factory_ws_manager.py` (new), `static/js/factory.js` (new), `static/css/factory.css` (new), `templates/factory_live.html` (new), `core/task_machine.py`, `main.py`, `api/tasks.py`

### Milestone F11: Factory v2 — Stale PR Bot 🔲
First autonomous bot. Monitors CI failures on factory PRs and creates fix tasks.

- [ ] `factory/bots/stale_pr_bot.py` — scan open `factory/*` PRs for check failures > 30min, create fix task pages
- [ ] Integrate with scheduler (phase F9)
- [ ] Safety guards: only `factory/*` branches, max 2 fix attempts per PR, respects budget

**Key files:** `orchestrator/factory/bots/` (new), `orchestrator/factory/scheduler.py`

### Milestone 11: Macro System & Documentation
Document the extension system and add useful built-in macros.

- [x] Write developer guide: `docs/custom-macros.md`
- [x] Add macro examples to sample wiki content (11 example pages with MetaTable usage)
- [x] `<<PageList(tag=value)>>` macro — embed a filtered list of pages (Pattern B)
- [x] `<<RecentChanges(n=10)>>` macro — show recently modified pages
- [x] `<<BackLinks>>` macro — inline backlinks (alternative to sidebar panel)
- [x] `<<PageCount>>` macro — total page count for dashboards
- [x] `<<Include(PageName)>>` macro — transcludes another page, circular detection
- [x] `<<NewPage(Template, "Label", Parent)>>` macro — inline form to create page from template
- [x] `<<LastModified>>` macro — inline relative time of page's last modification, falls back to `—`
- [x] `<<TagList>>` macro — inline tag list with counts, links to `/search?tag=X`, sorted by count descending
- [ ] Live MetaTable refresh — wire WebSocket `page_updated` events to trigger HTMX re-fetch of MetaTable sections without full page reload
- [ ] Macro escape syntax — preprocessors skip inline backtick spans and honor `\<<MacroName>>` as a literal `<<MacroName>>` (currently any bare `<<Macro>>` in prose triggers the preprocessor)
- [ ] `<<TaskStatus>>` rework edge — if a task has been returned from `review` back to `in_progress` at least once, show the back-edge in the Mermaid state diagram with the retry count as an edge label (e.g. `review -->|×2| in_progress`); read attempt count from `subtask["attempt"]` or a dedicated `rework_count` field in frontmatter
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

### Milestone 14: Version History ✅

SQLite-backed revision tracking for every page save. Zero new dependencies (stdlib `sqlite3` + `difflib`).

- [x] `RevisionStore` SQLite store — per-page sequential revision numbering, WAL mode
- [x] `Revision` Pydantic model added to `models.py`
- [x] `FileStorage` wired up — all 4 write paths (save, delete, rename, frontmatter) record/clean revisions
- [x] `GET /page/{name}/history` — paginated revision list
- [x] `GET /page/{name}/history/{rev}` — read-only rendered past revision with restore button
- [x] `POST /page/{name}/restore/{rev}` — restores content, records new revision
- [x] `GET /page/{name}/diff/{a..b}` — unified diff view (also `diff/{n}` shorthand)
- [x] JSON API: `GET /api/v1/pages/{name}/history[/{rev}]`
- [x] Config: `MESHWIKI_HISTORY_ENABLED` (default `true`)
- [x] 60 new tests (28 unit + 13 storage + 19 integration)

**Key files:** `core/revision_store.py` (new), `core/storage.py`, `core/models.py`, `core/dependencies.py`, `main.py`, `api/pages.py`, `templates/page/history.html`, `templates/page/revision.html`, `templates/page/diff.html`, `static/css/style.css`

---

## Success Criteria

- [x] Editor has live preview and toolbar
- [x] Users can search pages by name and content
- [x] Dark mode works with one click
- [x] Mobile layout is usable
- [x] Developer docs explain how to create custom macros
- [x] At least 3 new built-in macros available (PageList, BackLinks, PageCount, Include, NewPage)
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

### Remote Staging (Future)
Deploy PR branches to a staging server for automated browser testing.

**Approach:**
1. Create a staging VPS (e.g., `staging.wiki.penni.fi`)
2. Modify CI to deploy PR branches to staging on pull request
3. Run E2E tests against staging before allowing merge
4. Production deploy only happens after PR is merged to main

**Files to create/modify:**
- `deploy/vps/docker-compose.staging.yml` (new)
- `deploy/vps/staging.Caddyfile` (new)
- `.github/workflows/staging-deploy.yml` (new)
- Update `ci.yml` to require staging E2E pass before merge

---

## Agent Factory Backlog (now tracked as Milestones F8–F11)

See `docs/domains/factory.md` for full v2 plan with phases.

Previously completed:
- [x] **PM uses Sonnet** — switched from Opus 4 to Sonnet 4.6
- [x] **SQLite checkpointer** — replaced MemorySaver with AsyncSqliteSaver (state survives restarts)
- [x] **Webhook handler decoupling** — `ainvoke` runs in background `asyncio.Task`, webhook returns immediately
- [x] **Grinder auto-transitions task** — grinder node calls `transition_task()` on complete/fail, records `pr_url` and `branch`

Moved to v2 milestones (see F8–F11 above):
- Cost tracking → F8
- Concurrency control / `active_grinders` → F8
- Bookkeeper bot → F8
- Signed grinder commits → F8
- HBR resource manager + daily budget → F9
- 24/7 heartbeat scheduler → F9
- Live D3 factory visualization → F10
- Stale PR fixer bot → F11

---

## Notes

- Start with in-memory graph, add persistence later
- Focus on correctness over performance initially
- Keep Python as the primary interface; Rust is an implementation detail
- Python 3.14 requires ABI3 forward compatibility flag for PyO3
