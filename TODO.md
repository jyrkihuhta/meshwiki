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
| 11 | **Macro System** — PageList, RecentChanges, BackLinks, PageCount, Include, NewPage macros | 🔄 In Progress (2 items left) |
| 12 | **Authentication** — user accounts, login/logout, access control | 🔄 Partial — single-password session auth done; user accounts planned |
| 13 | **Observability** — structured logging, metrics endpoint | 🔄 Partial — structlog + `/metrics` done; conventions doc pending |
| 14 | **Version History** — SQLite revisions, diff view, restore | ✅ Complete |
| S1 | **Staging Integration** — `staging` branch, grinders → staging, auto-merge, E2B template | ✅ Complete |
| F8 | **Factory v2: Gap Fixes** — cost tracking, concurrency control, bookkeeper bot | ✅ Complete |
| F9 | **Factory v2: HBR Manager** — resource tracking, daily budget, 24/7 scheduler | ✅ Complete |
| F10 | **Factory v2: Live Visualization** — D3.js factory graph, `/factory/live`, WebSocket | ✅ Complete (command-center view open) |
| F11 | **Factory v2: Stale PR Bot** — autonomous CI failure fixer | ✅ Complete |

**Priority:** HIGH findings from [Deep review (2026-11)](#deep-review-2026-11)
(auth startup checks, frontmatter tolerance) and its deploy/CI items → merge the
H2 fix (#224) → remaining [Code review (2026-10-07)](#code-review-2026-10-07)
items → factory graph fixes (F1–F3) → armory-research cutover → 11 → 12 → 13

**~1,570 tests passing** (~785 web unit + 49 Playwright E2E + ~664 orchestrator + 70
graph-core), CI pipeline active.

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

### Milestone F8: Factory v2 — Gap Fixes ✅
Fix correctness and reliability issues in the v1 orchestrator.

**Cost & resource control (highest impact)**
- [ ] **F8.1** Concurrency control — cap `route_grinders` at `FACTORY_MAX_CONCURRENT_SANDBOXES` (3), populate `active_grinders` in `grind_node` — ⚠️ reopened: the cap exists but `active_grinders` is filled when a grinder *finishes* and is never cleared (see Deep review 2026-11, F1)
- [x] **F8.2** Cost tracking — `cost_usd` never incremented; add `factory/cost.py`, read `response.usage` from PM agent calls, track E2B wall-clock time

**Correctness**
- [x] **F8.3** Fan-in state merge bug — when parallel grinders finish at different times, `collect_results` sees a merged subtasks list that drops the failed status of earlier-finishing subtasks; investigate LangGraph reducer annotation on `subtasks` / `failed_subtask_ids` fields in `FactoryState` and add an `Annotated` reducer so all branch updates are correctly combined
- [x] **F8.4** Per-subtask PM review — currently PM review waits for ALL grinders to finish before reviewing any; restructure so each grind instance fans out to its own `pm_review` via `Send()`, unblocking fast subtasks from slow rework cycles
- [x] **F8.5** PM review feedback visible on wiki — `append_to_page` in `pm_review_node` isn't writing feedback to the task page; PM review decisions should be visible in the Agent Log section

**Reliability**
- [x] **F8.6** Bookkeeper bot — periodic job reconciling stale task states (stuck in_progress → failed, merged PRs → merged)
- [x] **F8.7** Unit tests for routing functions — `route_after_grinding`, `route_grinders` file-overlap, `route_after_pm_review`

**Performance**
- [x] **F8.8** Pre-bake Python deps into E2B template — `pip install -e '.[dev]'` runs from scratch every grind session; baking deps into the `meshwiki-grinder` E2B template snapshot would make this near-instant and eliminate a large chunk of per-run disk/time overhead (requires rebuilding the template via `e2b template build`)

**Security**
- [x] **F8.9** GitHub webhook secret on staging — `MESHWIKI_GITHUB_WEBHOOK_SECRET` is empty in staging env, so HMAC verification is skipped; anyone can POST fake "PR merged" events to trigger task page transitions
- [x] **F8.10** Auth-gate `/ws/terminal/{name}` WebSocket — currently unauthenticated; anyone who knows (or guesses) a task page name can read live grinder terminal output, which may include API keys, tokens, or repo contents streamed via Kilo

**Efficiency**
- [x] **F8.11** httpx clients — share a single `httpx.AsyncClient` per session in `MeshWikiClient` and `GitHubClient`

**Larger refactors / lower priority**
- [x] **F8.12** Stable page identity via UUID — terminal session keys, WebSocket lookups, and graph thread IDs all use the fragile human-readable page name (spaces/underscores/special chars cause mismatch bugs). Root cause: wiki URLs encode spaces as underscores, but page names can also contain real underscores (e.g. `get_engine()`), making the two indistinguishable in a URL. Add a `uuid` frontmatter field generated on page creation; use it as the canonical key everywhere internally, keeping the page name only for display/URL routing
- [x] **F8.13** Redecompose escalation — implement `"redecompose"` decision in `escalate_node`
- [~] **F8.14** Signed grinder commits — deferred (GitHub App token or GPG key in E2B sandbox)
- [x] **F8.15** Persist grinder terminal output and run a review bot — terminal chunks are currently streamed to the browser and discarded; storing them (e.g. appended to the task wiki page or a sidecar log file) would enable a post-run bot to analyze patterns across sessions: recurring lint failures, commands that always fail first try, slow steps, Kilo confusion about tool use. Bot output could feed back into improved task prompts, better bootstrap steps, or a "known issues" section in CLAUDE.md
- [x] **F8.16** `/api/graph`, `/ws/graph`, `/metrics` are unauthenticated — exposes all page names, links, and per-page view counts to anonymous users; acceptable for now but worth locking down before any public exposure

**Completed**
- [x] Configurable PM model — `FACTORY_PM_DECOMPOSE_MODEL`, `FACTORY_PM_REVIEW_MODEL`, `FACTORY_PM_TRIAGE_MODEL` env vars
- [x] Review feedback in rework — `subtask["review_feedback"]` appended to Kilo task prompt on rework iterations
- [x] PM review token cost — diff capped at `FACTORY_PM_REVIEW_MAX_DIFF_LINES` (default 500), two-pass triage via `FACTORY_PM_TRIAGE_MODEL` (Haiku fast-path; escalates to Sonnet only when triage requests changes)
- [ ] Deferred subtask routing bug — `route_after_grinding` now loops back to `assign_grinders` when pending subtasks remain — ⚠️ reopened: the loop-back edge is gone again, deferred subtasks are never dispatched (see Deep review 2026-11, F1)
- [x] Spurious `task.assigned` restart from subtask transitions — webhook_server checks `data["parent_task"]` and ignores `task.assigned` for subtask pages
- [x] E2B sandbox disk optimisation — `git clone --depth 1` + `pip install --no-cache-dir` in grinder_agent.py
- [x] Orchestrator dep install in grinder — `pip install -e '.[dev]'` added to bootstrap (GrinderBootstrap PR #126)

**Key files:** `orchestrator/factory/cost.py` (new), `orchestrator/factory/nodes/assign.py`, `orchestrator/factory/agents/pm_agent.py`, `orchestrator/factory/agents/grinder_agent.py`, `orchestrator/factory/integrations/`

### Milestone F9: Factory v2 — HBR Resource Manager ✅
Internal resource tracking and 24/7 heartbeat scheduler.

- [x] `factory/hbr.py` — track active sandboxes, daily cost vs budget, per-model API usage
- [x] `assign.py` checks `hbr.can_allocate_sandbox()` before dispatching
- [x] Scheduler heartbeat — implemented as `factory/bots/scheduler.py` (not `factory/scheduler.py`), alongside `bots/worker_heartbeat.py`
- [x] `GET /hbr/status` endpoint — active sandboxes, daily cost, budget remaining

**Key files:** `orchestrator/factory/hbr.py`, `orchestrator/factory/bots/scheduler.py`, `orchestrator/factory/webhook_server.py`

### Milestone F10: Factory v2 — Live D3.js Visualization ✅ (stretch item open)
Real-time factory activity view using D3.js, same visual language as the wiki graph.

- [x] `core/factory_ws_manager.py` — push-based WebSocket manager + 500-entry activity ring buffer
- [x] `core/task_machine.py` — broadcast task transitions to factory WS after webhook emit
- [x] `GET /ws/factory` WebSocket endpoint — session check added in #223 (M1)
- [x] `GET /api/factory/graph` and `GET /api/factory/activity` REST endpoints
- [x] `/factory/live` page: D3 force graph (task circles colored by status, agent diamonds, dashed parent edges), detail panel (slide-in right, terminal embed for in_progress), activity feed strip at bottom
- [x] `base.html` — add conditional "Factory" nav link when `factory_enabled`
- [ ] **Command-center flow view** — XSIAM-style horizontal pipeline visualization: sources (wiki backlog) → processing vortex (PM + grinder nodes) → outcomes (merged/failed/open PRs), with animated flowing paths between stages, live counters per stage, and branching arcs for auto vs manual routes. Inspired by Palo Alto XSIAM Command Center dashboard aesthetic.

**Key files:** `core/factory_ws_manager.py` (new), `static/js/factory.js` (new), `static/css/factory.css` (new), `templates/factory_live.html` (new), `core/task_machine.py`, `main.py`, `api/tasks.py`

### Milestone F11: Factory v2 — Stale PR Bot ✅
First autonomous bot. Monitors CI failures on factory PRs and creates fix tasks.

- [x] `factory/bots/stale_pr_bot.py` — scan open `factory/*` PRs for check failures > 30min, create fix task pages
- [x] Integrate with scheduler / HBR (uses `hbr` for budget checks)
- [x] Safety guards: only `factory/*` branches, capped fix attempts per PR, respects budget

**Key files:** `orchestrator/factory/bots/stale_pr_bot.py`, `orchestrator/factory/bots/ci_fixer.py`

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
- [x] Macro escape syntax — `MacroEscapeExtension` renders escaped `<<MacroName>>` as a literal (`macro-literal` span)
- [ ] `<<TaskStatus>>` rework edge — if a task has been returned from `review` back to `in_progress` at least once, show the back-edge in the Mermaid state diagram with the retry count as an edge label (e.g. `review -->|×2| in_progress`); read attempt count from `subtask["attempt"]` or a dedicated `rework_count` field in frontmatter
**Key files:** `core/parser.py` (new extensions), `docs/custom-macros.md`

### Milestone 12: Authentication
Add user accounts and access control.

- [x] Design auth approach — session cookie (Starlette `SessionMiddleware`), single shared password
- [ ] Implement user model and storage (multi-user accounts)
- [x] Add login/logout routes (`/login`, rate-limited — see Code review M2 for the proxy-IP flaw)
- [x] Protect edit/delete routes (`AuthMiddleware` when `MESHWIKI_AUTH_ENABLED`)
- [ ] Add user info to templates (needs user accounts)
- [ ] CSRF tokens on state-changing forms (today only `SameSite=lax` mitigates)

**Key files:** `auth.py`, `main.py` (login routes, middleware order)

### Milestone 13: Observability
Add structured logging and metrics for production readiness.

- [x] Add structured logging (structlog JSON — `core/logging.py`)
- [x] Add basic metrics endpoint (`/metrics`, plus `/health/live` and `/health/ready`)
- [ ] Document logging conventions
- [ ] Replace silent `except Exception: pass` blocks with debug logging (see Code review L4)

**Key files:** `main.py`, `core/logging.py`, `core/metrics.py`

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
- [ ] JSON API: `GET /api/v1/pages/{name}/history[/{rev}]` — ⚠️ reopened: unreachable, shadowed by the earlier `GET /pages/{name:path}` route (Deep review 2026-11)
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
- Signed grinder commits (F8.14) deferred — GitHub App token or GPG key in E2B sandbox so factory PRs carry verified authorship

---

## Review follow-ups (2026-09-24)

Follow-ups from a security/correctness review. The HIGH/MEDIUM findings that
still applied to `staging` were fixed in a separate pass; the items below are
the deferred LOW findings and improvement ideas, grouped by theme.

### Correctness (LOW findings B10–B13)
- **Decompose `planned→planned` + acceptance-criteria bug** — ✅ no longer
  applies (checked 2026-11): `decompose_node` does not transition subtasks, and
  acceptance criteria come from the PM tool call. Leftovers: the docstring still
  says it transitions subtasks, and the `skip_decomposition` path builds its
  subtask with empty acceptance criteria.
- **Branch name from page name → invalid git refs** — branch names built from
  wiki page names can produce refs git rejects (spaces, `..`, leading/trailing
  slashes, control chars, reserved sequences). Sanitize to a valid ref.
  Narrower than described (2026-11): decomposed subtasks use uuid-based ids;
  the page-name case remains for `skip_decomposition` and pre-seeded subtask
  pages, where the id is also interpolated unquoted into sandbox shell commands.
- **Rate limiter keys on proxy IP** — still open, severity raised: tracked as
  **M2** in Code review (2026-10-07) (it's a global login-lockout DoS, not just a
  shared bucket).
- **Rust/Python frontmatter parse divergence + code-block links** — the Rust
  parser and the Python parser can disagree on frontmatter edge cases, and the
  Rust link extractor creates graph edges for `[[wiki links]]` that appear
  inside fenced code blocks (the Python side already skips code blocks). Align
  the two parsers and skip code blocks in the Rust link extraction.

### Async correctness
- **Blocking I/O in async paths** — 🔄 partially fixed: `list_pages_with_metadata`
  and the `/api/v1/tasks` batch load now run in an executor (#209, #210).
  Remaining: `get_page`, `save_page`, `search_by_tag`, `update_frontmatter_field`
  and `patch_frontmatter` still call synchronous `read_text()`/`write_text()`
  inside `async def`; `search_by_tag` scans every page inline. **Also
  `search_pages`** (2026-11): the heaviest remaining path, it reads and parses
  every page on the event loop for each search-box query; `<<Include>>`
  rendering does the same per included page.
- **PyO3 GIL** — the `GraphEngine` PyO3 methods hold the GIL for the whole call;
  wrap heavy graph operations (rebuild, query, metatable) in `py.allow_threads`
  so concurrent Python work isn't blocked.

### Factory F8 robustness
- Enforce concurrency caps (parent tasks + sandboxes) at dispatch, not just in
  status reporting. — implemented but the counter is wrong; see Deep review
  2026-11, F1.
- Add real cost tracking — 🔄 partly done (PM calls and sandbox time are
  accumulated). Missing: model spend inside the E2B sandbox, PM spend in the
  daily-budget gate, `cost_usd` for running/abandoned tasks, and persistence
  across restarts (budget totals reset on every restart).
- ✅ One shared `httpx.AsyncClient` per `MeshWikiClient` — done (F8.11).
- URL-encode page names when building MeshWiki API URLs (hierarchical names with
  slashes/spaces). Worse than a 404 risk (2026-11): `?` or `#` in a name sends
  the read-modify-write helpers to a *different* page.

### Build / dependency hygiene
- No lockfiles and all deps pinned as `>=` — add lockfiles (pip-tools/uv,
  `Cargo.lock` committed) for reproducible builds. Also (2026-11):
  `graph-core/.gitignore` ignores `Cargo.lock` (remove that first, then build
  with `--locked`); base images, rustup and maturin are unpinned; the Dockerfile
  comment claims a base-image digest that is not pinned.
- ✅ `config.py` `repo_root` — no longer hardcoded (defaults to the working
  directory).
- `POSTGRES_DSN` — gone from the code; only a line in `deploy/vps/.env.example`
  remains to delete.
- ✅ `FACTORY_PORT` — consistent by design: app default 8001 is production, CI
  writes 8002 for staging.

### CI / deploy
- Gate deploy on `test-orchestrator` — ✅ staging deploy now requires
  `test-orchestrator` success (or skipped when no orchestrator changes).
  **Production does not have the gate** (confirmed 2026-11): `deploy` accepts a
  skipped `merge-orchestrator`, which is what a failed `test-orchestrator`
  produces, so the wiki ships at the new commit with the old orchestrator image.
- Orchestrator image is shipped as unversioned `:latest` with no rollback path —
  tag images and keep a rollback.
- Most E2E tests never run in CI — 🔄 a smoke subset runs on PRs into `main`
  (`pre-merge-check.yml`), and the full suite passes locally (#205). Run the full
  suite in CI, at least on PRs into `main`.
- Add secret scanning and `cargo clippy` to the lint/CI pipeline.

---

## Code review (2026-10-07)

Whole-repo review (wiki app, auth, storage/rendering, factory API, orchestrator
agents, deploy). Findings marked **verified** were reproduced against the code;
the rest come from reading the code. Fix HIGH first.

### HIGH

- [x] **H1 · Data loss: page names containing a dot collide** — **verified**.
  `FileStorage._get_path` builds the filename with `.with_suffix(".md")`, which
  replaces everything after the last dot: `Release 1.2` → `Release_1.md`,
  `v2.0 Notes` → `v2.md`, `config.yaml` → `config.md`. Saving `Release 1.2`
  silently **overwrote** `Release 1`.
  *Fix:* append `".md"` to the last segment instead of `with_suffix`; make
  `_path_to_name` symmetric; add a one-off check for existing truncated files
  and collisions before switching; regression tests for dotted names.
  (`core/storage.py:_get_path`)
  ✅ **Fixed in #223** — `.md` is appended instead of `with_suffix`; no existing dotted page files on prod/staging, so no migration.
- [x] **H2 · Stored XSS: rendered Markdown isn't sanitized** — **verified**.
  Raw HTML (`<script>`, `<img onerror=…>`) passes through `parse_wiki_content`
  and is emitted with `| safe` (`page/view.html`, `page/revision.html`,
  `partials/preview.html`, `partials/page_content_fragment.html`); the CSP allows
  `script-src 'unsafe-inline'`, so it runs. Pages aren't only written by trusted
  editors: the factory files pages built from **external, untrusted text**
  (H1 report titles/descriptions via the H1 researcher, LLM output, grinder
  terminal logs). A crafted report title could run script in a logged-in
  operator's browser.
  *Fix:* sanitize rendered HTML with an allowlist (`nh3`/`bleach`) that keeps
  Markdown output and the macro HTML; then move inline scripts (theme bootstrap
  in `base.html`, task-terminal JS emitted by the parser) to static files or
  nonces and drop `'unsafe-inline'` from `script-src`.
  ✅ **Fixed** — rendered HTML is allowlist-sanitized (`core/sanitize.py`, `nh3`) as the last Markdown postprocessor; all inline script/handlers moved to `static/js`; CSP `script-src` no longer has `'unsafe-inline'`. See "Found while fixing H2" below.
- [x] **H3 · XSS in `/api/autocomplete`** — **verified** (page names may contain
  `<`, `>` and `"`). `main.py:api_autocomplete` interpolates names unescaped into
  `data-value="{name}">{name}`, so a page with a crafted name runs script in
  every editor's `[[` dropdown.
  *Fix:* `html_escape(name, quote=True)` for the attribute and the text; consider
  restricting the page-name charset in `_validate_page_name`.
  ✅ **Fixed in #223** — names are HTML-escaped in the attribute and the text.

### MEDIUM

- [x] **M1 · `/ws/factory` WebSocket is unauthenticated.** `AuthMiddleware`
  exempts all `/ws/` paths ("WebSockets do their own auth"); `/ws/graph` and
  `/ws/terminal` check the session, but `ws_factory` only checks
  `factory_enabled`, so anyone can read the live factory event stream.
  *Fix:* apply the same `auth_enabled` + session check as `ws_graph`.
  ✅ **Fixed in #223** — `/ws/factory` applies the same session check as `/ws/graph`.
- [ ] **M2 · Login lockout is global behind Caddy.** The rate limiter keys on
  `request.client.host`, and uvicorn runs without `--proxy-headers`, so every
  client appears as Caddy's IP: five bad logins from anyone lock **everyone** out
  for 10 minutes. `_login_attempts` also grows without bound.
  *Fix:* run uvicorn with `--proxy-headers --forwarded-allow-ips=<caddy network>`
  (or parse `X-Forwarded-For` only from the trusted proxy); prune old entries.
- [ ] **M3 · Session cookie never `Secure`; no CSRF tokens.** `SessionMiddleware`
  is configured `https_only=False` unconditionally. State-changing POSTs (save,
  delete, restore, metadata) rely only on `SameSite=lax`.
  *Fix:* `https_only=not settings.debug`; add CSRF tokens (Milestone 12).
- [ ] **M4 · Argument injection in the local grinder's `_search_code`.** The
  LLM-chosen `pattern` goes to `rg` with no `--` separator, so a pattern like
  `--pre=sh` becomes an rg option that runs a command on every file. Only the
  non-E2B providers use this executor (default is E2B), but tool input can be
  steered by untrusted task content.
  *Fix:* `["rg", "-e", pattern, "--", search_path]`; also pass `--` before file
  lists in `git add`, and validate branch names before `git checkout -b`/`push`.

### LOW

- [ ] **L1 · Stale auth exemptions.** `_PUBLIC_PREFIXES` includes `/api/tasks/`
  and `/api/agents/`, but no routes exist there (the API is under `/api/v1/`).
  Harmless today; any route added later would silently skip auth. Remove them.
- [ ] **L2 · Orchestrator webhook check fails open.** `_verify_signature` accepts
  unsigned requests when `FACTORY_WEBHOOK_SECRET` is empty ("dev mode"). The
  secret is set on the VPS today; fail closed outside debug, matching the
  factory API key change in #200.
- [ ] **L3 · `shell=True` in `_run_lint` / `_run_autofix`.** No injection today
  (only config-derived paths), but use argv lists like the other helpers.
- [ ] **L4 · Silent exception swallowing.** 13 `except Exception: pass` blocks
  (mostly `main.py`, e.g. graph metadata lookups) hide failures. Log at debug.
- [ ] **L5 · `RevisionStore` shares one SQLite connection across threads.**
  `check_same_thread=False` with no lock is safe only because every call
  currently runs on the event-loop thread. Add a lock (or per-thread
  connections) before any revision work moves to an executor; the calls are also
  synchronous inside async paths.
- [ ] **L6 · `CalloutExtension()` is registered twice** in the parser's extension
  list; remove the duplicate.

### Found while fixing H2

Writing browser tests for H2 turned up four more injection points. All fixed in
the same change, each with a regression test:

- [x] **`/api/pages/{name}/preview` returned the page's raw source as HTML.** Every
  wiki link fetches it on hover (`hx-get`) and swaps it into the page, so hovering
  a link to a page containing `<img onerror=…>`/`<script>` ran it, bypassing any
  render-time sanitizing. It now renders the existing `partials/hover_card.html`
  with a plain-text excerpt (`core/excerpt.py`), autoescaped.
- [x] **`hover_card.js` was a silent no-op** (it returned early because it ran in
  `<head>` before the card existed), so the hover card never displayed. Rewritten
  to look the card up per event and show it on `htmx:afterSettle` (htmx re-applies
  the response's attributes during settle, which wiped an inline `display`).
- [x] **`graph.js` search results built HTML from page names unescaped** (the
  `data-name` attribute and the highlighted text). Both are escaped now.
- [x] **`onsubmit="return confirm('… {{ page_name }} …')"` in three templates**
  (`view`, `history`, `revision`): Jinja's `&#39;` decodes back to `'` before the JS
  runs, so a page named `x');alert(1);('` broke out of the string. Replaced by
  `data-confirm` + a delegated capture-phase handler in `behaviors.js`.

Other changes that came with it: the staging deploy now waits for the web image to
be published (staging runs app code from a mount but dependencies from the image,
so a new dependency like `nh3` must not be deployed ahead of its image); the CSP
gained `object-src 'none'; base-uri 'self'`; the CSP/sanitization E2E tests run in
the pre-merge check on PRs into `main`.

**Residual risks / follow-ups**
- [ ] `data-*` hooks are allowlisted per tag but can't tell macro output from
  hand-written HTML, so page authors can forge them (e.g. an editable table cell
  pointing at another page's metadata). Needs a victim to double-click into the cell
  and type; low impact. Closing it fully means marking trusted macro output.
- [ ] `id` is allowed in content (needed for headings/footnotes), so DOM clobbering
  of an element id is possible in principle; no current script looks up a
  content-supplied id.
- [ ] `style-src` still has `'unsafe-inline'` (templates use `style=`); removing it
  is the next CSP step.
- [ ] CSRF tokens (see M3) — still only `SameSite=lax`.

---

## Armory research harmonization (Oct 2026)

Playbook-gap research is moving to one place: Molly owns gap analysis
(`molly.armory.research`: one canonical taxonomy, coverage from
`PlaybookLoader`), and the factory's `ArmoryResearchBot` is the single creator of
`Task_Playbook_*` pages, deduping on `artifact_path`. This fixes duplicate tasks
such as repeatedly re-filing `other-generic.md`.

- [x] Molly stopgap pushed: no `other` catch-all; coverage counts the
  `playbook:` slug and filename stem (molly `ef080f1`)
- [x] `molly.armory.research` module + findings contract `{"findings": [...]}`
  (molly `a912082`)
- [x] `ArmoryResearchBot` consumer, disabled by default (#220)
- [ ] Merge #221 — consumer parses the frozen `{"findings": [...]}` shape
- [ ] Decide the publish transport (molly endpoint vs committed `findings.json`);
  point `_fetch_findings` at the resulting handle
- [ ] Possibly relocate `research`/`sources`/`h1_researcher` into molly-armory,
  keeping `taxonomy`/`load_armory` in the engine to avoid an import cycle
  (Molly-side decision)
- [ ] Cutover, sequenced with no double-tasking window: enable
  `FACTORY_ARMORY_RESEARCH_ENABLED`, retire `class_gap_researcher.py`'s domain
  logic, remove `h1_researcher`'s task-creation path

---

## Deep review (2026-11)

Whole-`staging` review at `f59fde5` (wiki, factory, Rust engine, deploy/CI).
316 findings held up after independent verification: 3 high, 120 medium, 180
low, 13 info; many share a root cause. Full report:
[`docs/reviews/2026-11-deep-review.md`](docs/reviews/2026-11-deep-review.md)
(summary, themes, table of every finding) and
[`…-details.md`](docs/reviews/2026-11-deep-review-details.md). IDs in brackets
refer to that report. Items below are grouped work, in suggested order.

### HIGH

- [ ] **A1 · Auth fails open on missing or default secrets** — reproduced. With
  auth on: an unset password accepts an empty login; an unset or blank
  `MESHWIKI_SESSION_SECRET` makes the session cookie forgeable (full write
  access). The env examples ship the secret blank, and the staging deploy copies
  `staging.env.example` into place when `staging.env` is missing.
  *Fix:* refuse to start with auth on unless password and secret are set,
  non-default and long enough; reject empty passwords at `/login`; stop CI
  installing the example file. **First: check the secrets on the VPS.**
  [`wiki-routes-a-3`, `deploy-vps-1`]
- [x] **A2 · One badly typed frontmatter field takes the wiki down** —
  reproduced. `title: 2024`, `tags: python`, `children: A` or a non-UTF-8 file
  raises an unhandled validation error in every page scan; listings, search,
  sidebar and then every HTML route return 500. Inline MetaTable editing
  (`PATCH /api/page/{name}/metadata`) can write such a value.
  *Fix:* catch `ValidationError` in `_parse_frontmatter` and degrade; guard each
  file in the scan loops; validate in the PATCH endpoint; tolerate non-string
  `parent_task` in the tree builder.
  [`wiki-storage-1`, `-2`, `wiki-routes-b-1`, `wiki-routes-a-1`, `-7`]
  *Done:* declared fields are coerced on read (`coerce_frontmatter`; values with
  no sensible reading are kept under `<field>_invalid`); every scan skips and
  logs a file it cannot read; writes are strict (`InvalidFrontmatterError` →
  422) and the field-update paths convert to the field's type; the tree
  builder, epic child list and date sorts tolerate any YAML type and mixed
  naive/aware timestamps; the PATCH route protects `uuid` and invalidates the
  page cache [`wiki-routes-b-3`]. Review follow-up: tagged scalars PyYAML
  cannot build (`!!bool maybe`) no longer escape the loader; the JSON API sends
  metadata through `json_safe_metadata()` so binary, self-referential and
  `.nan`/`.inf` values cannot fail a listing; `/api/factory/tasks?status=` and
  the factory-enabled editor save tolerate a list- or mapping-valued
  `type`/`status`; writes that store a `<field>_invalid` key log it. See
  "Frontmatter tolerance" in `docs/domains/business-logic.md`. Not done from
  the suggested fixes:
  rejecting `uuid`/`created`/`modified` writes inside the storage layer (the
  route does it; `/api/v1` PATCH still may set them, now type-checked), and
  dropping the page-tree computation from `/login`.
- [ ] **F1 · Factory cannot complete a task with 2+ subtasks** — reproduced
  (rated high by one check, medium by another: factory-only). Parallel
  `pm_review` branches each see siblings as unfinished and end the run; deferred
  subtasks are never dispatched (no edge back to `assign_grinders`);
  `active_grinders` only grows.
  *Fix:* one join node after `pm_review` deciding finalise / wait /
  re-dispatch; restore the loop-back; real-graph tests with 2+ subtasks.
  [`factory-tests-1`, `factory-graph-nodes-1`, `-2`, `-3`]

### MEDIUM — deploy and CI

- [ ] **C1** Production `deploy` job switches the staging checkout to `main`
  (regression of `daf7828`, on both branches). [`ci-workflows-2`]
- [ ] **C2** Staging pushes overwrite production's compose file and Caddyfile
  and reload the shared Caddy. [`ci-workflows-6`]
- [ ] **C3** Production rollback is unreachable when the new container is
  unhealthy; it also runs `up -d` on every service and leaves `.env` on the
  failed version. [`ci-workflows-1`, `deploy-vps-8`]
- [ ] **C4** Pin third-party actions to commit SHAs (one is on `@master`).
  [`ci-workflows-8`]
- [ ] **C5** Staging healthcheck never resets its failure counter after a kill:
  a slow start becomes a kill loop. [`deploy-vps-2`]
- [ ] **C6** Backups: `backup.sh` tars a live WAL-mode SQLite DB and is not
  installed where cron expects it; runbook commands and restore steps are wrong.
  [`deploy-vps-3`, `cross-architecture-docs-8`]
- [ ] **C7** Request-size limits: no body cap in Caddy, uvicorn or the app;
  public API routes parse the body before the API-key check. *Not verified*
  (from the completeness critic).
- [ ] **C8** `orchestrator/e2b.Dockerfile` passes the GitHub token as a build
  arg used in `RUN` (recorded in image history). *Not verified.*

### MEDIUM — wiki

- [ ] **W1** Task state machine side doors: inline metadata edit, two-step save
  and revision restore change `status` unchecked. [`wiki-routes-b-2`,
  `wiki-routes-a-6`]
- [ ] **W2** No conflict detection on save; a stale editor tab overwrites bot
  updates and can fire transitions. Add a base-revision token.
  [`wiki-routes-b-6`, `wiki-frontend-8`]
- [ ] **W3** Page cache goes stale: PATCH and other write paths skip
  invalidation; invalidations during a scan are lost; 60 s dedup drops the
  second update. [`wiki-routes-b-3`, `wiki-storage-4`, `-5`, `wiki-api-core-10`]
- [ ] **W4** Rename onto an existing page overwrites it, then 500s.
  [`wiki-storage-3`]
- [ ] **W5** Page writes are not atomic or ordered with the revision insert.
  [`wiki-storage-8`]
- [ ] **W6** Revision store grows without bound (no-op saves, bot patches);
  add dedup and retention. [`wiki-storage-9`, `factory-bots-5`]
- [ ] **W7** Page names: canonicalise before history/restore/delete; URL-encode
  names and tags in templates and redirects (`#`/`?` redirect writes).
  [`wiki-routes-a-5`, `wiki-routes-b-7`, `wiki-frontend-4`]
- [ ] **W8** Unbounded Prometheus label series from unauthenticated requests.
  [`wiki-routes-a-4`, `wiki-api-core-7`]
- [ ] **W9** Parser limits: Include expansion budget; quadratic macro regexes
  (9–13 s on a 50 KB page); macros that raise on author input.
  [`wiki-parser-1`, `-3`, `-6`]
- [ ] **W10** Terminal sessions never freed, no size cap; outbound webhook
  dispatcher loses events and stalls on one dead endpoint.
  [`wiki-api-core-8`, `-11`]
- [ ] **W11** `hx-boost` breakage: `/search` navigation, save result and toast,
  unsaved-changes guard, live-update WebSocket, scripts stacking up on each
  navigation. [`wiki-frontend-1`, `-2`, `-3`, `-5`, `-6`]
- [ ] **W12** Share one render path between `view_page`, the fragment route and
  the HTMX save branch. [`wiki-routes-b-15`]

### MEDIUM — Rust graph engine

- [ ] **R1** Stub nodes are stamped "now", so the watcher's mtime short-circuit
  skips newly created or renamed pages — reproduced. [`rust-bindings-1`]
- [ ] **R2** Engine names keep underscores, link targets keep spaces: links to
  multi-word pages attach to stubs. [`rust-graph-2`]
- [ ] **R3** Deleting a page drops incoming edges; backlinks stay lost until
  restart. [`rust-graph-1`]
- [ ] **R4** Regex filter recompiled per page under the GIL and graph lock —
  reproduced. [`rust-bindings-2`]
- [ ] **R5** Watcher errors swallowed; `start_watching()` reports success after
  the thread exits. [`rust-bindings-6`]
- [ ] **R6** Rust unit tests cannot link and never run in CI (68 tests).
  [`rust-graph-6`]

### MEDIUM — factory

- [ ] **F2** Failed PM review / failed subtask counts as approval; subtask
  recorded `merged` when the PR was not merged — both reproduced.
  [`factory-graph-nodes-6`, `-7`]
- [ ] **F3** Human approval and rework restart the graph at `task_intake`
  instead of resuming — reproduced. [`factory-graph-nodes-8`,
  `factory-server-1`]
- [ ] **F4** Restart pushes tasks waiting for human review through as rejected
  and auto-fails in-progress subtask pages. [`factory-server-2`, `-3`]
- [ ] **F5** Escalation always abandons; PM-diagnose retry never re-runs the
  grinder. [`factory-graph-nodes-4`, `-5`]
- [ ] **F6** Stale state across runs on one thread; human rejection re-reviews
  an arbitrary subtask; empty dispatch ends silently; partial decompositions
  finalised as complete. [`factory-graph-nodes-9`, `-11`, `-12`, `-13`]
- [ ] **F7** Contract: transition `extra_fields` sent flattened and dropped
  (reproduced); GitHub merge webhook can never match a task; bookkeeper queries
  a status that does not exist. [`cross-trust-boundaries-1`, `-4`,
  `factory-bots-1`]
- [ ] **F8** Replace GET-then-PUT page updates (client, heartbeat, bots) with a
  patch/append endpoint; the heartbeat can revert transitions.
  [`cross-trust-boundaries-5`, `factory-bots-4`]
- [ ] **F9** PM-chosen subtask page names are written with an overwriting PUT;
  bots create slash-named pages the UI rejects. [`cross-trust-boundaries-8`,
  `cross-architecture-docs-2`]
- [ ] **F10** Build task-page frontmatter with a YAML serializer, not string
  interpolation (newlines in external text inject keys).
  [`factory-armory-2`, `factory-bots-7`]
- [ ] **F11** Scrub the MiniMax/Kilo key from terminal stream and logs;
  validate the scraped PR URL belongs to this task. [`factory-grinder-1`, `-2`]
- [ ] **F12** Triage can approve and auto-merge from the first 500 diff lines.
  [`factory-pm-1`]
- [ ] **F13** Local grinder executor is not a security boundary (`.git` writes,
  `run_tests`): remove or gate as dev-only. [`factory-grinder-4`]
- [ ] **F14** Grinder rework fails on the shallow clone; bootstrap errors skip
  the clone check; 25k-char logs appended per attempt. [`factory-grinder-3`,
  `-5`, `-6`]
- [ ] **F15** PM default path: no retry, 60 s timeout, no second provider; no
  decision → subtask fails; `github_read_file` cannot work in decomposition.
  [`factory-pm-6`, `-8`, `-9`]
- [ ] **F16** Armory validator passes files it cannot see; forbidden-import
  gate bypassable. [`factory-armory-3`, `-4`]
- [ ] **F17** Research dedup: class-gap generic suggestions; armory-research cap
  applied before the existing-page skip; survey truncates at 1,000 playbooks.
  [`factory-armory-1`, `-5`, `-7`]
- [ ] **F18** Bots: heartbeat rule fails healthy tasks; all bots re-run on every
  restart; one leaked asyncio task per tick; CI-fixer comment spam.
  [`factory-bots-2`, `-6`, `-9`, `-10`]
- [ ] **F19** `scripts/abandon_dropped_target_tasks.py` blocks every open
  playbook task on an empty upstream list. [`deploy-vps-9`]
- [ ] **F20** Tests: multi-subtask and resume through the real graph; triage and
  default-provider paths; budget wiring; a wiki↔orchestrator contract test.
  [`factory-graph-nodes-17`, `factory-tests-8`, `-10`]

### Follow-ups to PR #224

- [ ] Staging deploy still accepts a skipped image `merge` (new code on an old
  image when the build fails). [`ci-workflows-4`]
- [ ] Hover-preview endpoint skips page-name validation (500 on traversal-style
  names). [`wiki-routes-b-12`]
- [ ] CSP `script-src` trusts three whole public CDNs; `connect-src` allows any
  `wss:` host. [`wiki-routes-a-12`]

### Corrections to Code review (2026-10-07) items

- **M2** — cause is misstated: uvicorn's proxy-header handling is on by default;
  the missing piece is the trust list (`--forwarded-allow-ips`). Also the
  failure count never decays and a lockout blocks the correct password.
- **M3** — do not use `https_only=not settings.debug` (breaks the auth tests and
  plain-HTTP deployments); add a dedicated setting.
- **M4** — `--glob` must come before `--`, or the proposed command breaks file
  filtering. See F13 for the wider point.
- **L3** — `_run_autofix` is broken, not just `shell=True`: `ruff --fix src/` is
  rejected by current ruff (needs `ruff check --fix`).

### LOW

180 low and 13 informational findings are listed per unit in Appendix A of the
report (docs drift, k8s/Terraform scaffolding rot, script footguns, hardening,
test hygiene). Pick them up alongside the theme they belong to.

---

## Recently completed (Sep–Oct 2026)

- `/api/v1/tasks` served from the graph index; full scan off the event loop
  (#209). Staging CPU ~110% → <1%
- Large task-query batches loaded off the event loop (#210)
- OOM protection (`mem_reservation`, `oom_score_adj`) and staging self-heal
  health check (#206)
- `memory.penni.fi` added to the deployed Caddyfile on `staging` and `main`
  (#207, #208)
- E2E autocomplete fixes, symlinked data-dir watcher fix, lint on PRs into
  `staging` (#205)
- Release to `main`, ruff backlog cleared (#204)
- Page-history DB untracked from git (#203); install-path fixes (#202)
- Security/correctness fixes: grinder path confinement, factory API fails
  closed, token scrubbing, graph stub nodes (#200)
- Staging data cleanup (ops): 1,770 merged/done task pages archived;
  `.revisions.db` pruned from 1.18 GB to 295 MB
