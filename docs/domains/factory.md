# Domain: Agent Factory

**Owner:** TBD
**Status:** Phase 1 complete, Phase 2 in progress
**Language:** Python (MeshWiki layer) + future Python (LangGraph orchestrator)
**PRD:** `docs/prd/003-agent-factory.md`

## Scope

The autonomous agent software development factory:
- Task pages with structured frontmatter as the single source of truth
- State machine enforcing legal task transitions
- JSON API for programmatic CRUD and state transitions (used by agents)
- Outbound webhook dispatcher to notify the LangGraph orchestrator
- Inbound GitHub webhook receiver to sync PR merge events back to task state
- LangGraph orchestrator with PM and Grinder agents (future phases)

**Not in scope:** Wiki UI/rendering (business-logic), Rust graph engine (graph-engine), deployment (infrastructure)

## Current State

### Phase 1: MeshWiki Foundation ✅ (merged #32)
- [x] `src/meshwiki/core/task_machine.py` — state machine with `TASK_TRANSITIONS`, `InvalidTransitionError`, `transition_task()`
- [x] `src/meshwiki/core/webhooks.py` — `WebhookDispatcher` (async queue, HMAC-signed HTTP POST)
- [x] `src/meshwiki/api/auth.py` — `require_api_key` dependency (`MESHWIKI_FACTORY_API_KEY`)
- [x] `src/meshwiki/api/pages.py` — generic page CRUD (`GET/POST /api/v1/pages`)
- [x] `src/meshwiki/api/tasks.py` — task list + transition endpoints (`POST /api/v1/tasks/{name}/transition`)
- [x] `src/meshwiki/api/agents.py` — agent listing
- [x] Config additions: `factory_api_key`, `factory_webhook_url`, `factory_webhook_secret`, `github_webhook_secret`, `factory_enabled`

### Phase 2: GitHub Integration 🔧 (branch: `feature/phase-2-github-webhooks`)
- [ ] `src/meshwiki/api/webhooks.py` — inbound GitHub webhook receiver
- [ ] PR-to-task lookup via graph engine (find task page by `pr_number` or `pr_url` frontmatter)
- [ ] On PR merge: auto-transition task `review → merged → done`
- [ ] Factory Dashboard wiki page (manual `Factory_Dashboard.md`, no code)

### Phase 3–7: Orchestrator (not started)
- LangGraph orchestrator scaffold, PM agent, Grinder agent, PM chat interface, hardening

## Architecture

```
src/meshwiki/
├── api/
│   ├── __init__.py      # Mounts router at /api/v1
│   ├── auth.py          # API key auth dependency
│   ├── pages.py         # Generic page CRUD
│   ├── tasks.py         # Task list + transition endpoints
│   ├── agents.py        # Agent listing
│   └── webhooks.py      # GitHub inbound webhook receiver (Phase 2)
├── core/
│   ├── task_machine.py  # State machine (TASK_TRANSITIONS, transition_task)
│   └── webhooks.py      # Outbound webhook dispatcher (async queue, HMAC)
└── config.py            # MESHWIKI_FACTORY_* env vars

docs/prd/003-agent-factory.md  # Full spec (4000+ lines)
```

## Task Data Model

All task state is stored as YAML frontmatter in wiki pages (`extra="allow"` passes through arbitrary fields).
Page naming: `Task_XXXX_short_description.md` (zero-padded four-digit ID).

Key frontmatter fields: `type: task`, `status`, `priority`, `branch`, `pr_url`, `pr_number`, `assignee`, `parent_task`, `token_budget`, `retry_count`.

## State Machine

```
draft → planned → decomposed → approved → in_progress → review → merged → done
                                                          ↓
                                                       rejected → in_progress
                                              failed → planned
                          (any) → blocked → planned | approved | in_progress
```

Full transition map in `src/meshwiki/core/task_machine.py:TASK_TRANSITIONS`.

## API Endpoints

All endpoints under `/api/v1/` require `X-API-Key` header matching `MESHWIKI_FACTORY_API_KEY`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/pages` | List all pages |
| GET | `/api/v1/pages/{name}` | Get page content + metadata |
| POST | `/api/v1/pages/{name}` | Create/update page |
| DELETE | `/api/v1/pages/{name}` | Delete page |
| GET | `/api/v1/tasks` | List tasks (filters: `status`, `assignee`, `parent_task`, `priority`) |
| POST | `/api/v1/tasks/{name}/transition` | Transition task state |
| GET | `/api/v1/agents` | List agent pages |
| POST | `/api/v1/github/webhook` | GitHub inbound webhook (Phase 2, no API key — uses HMAC) |

## Webhook Events

Outbound webhook POSTed to `MESHWIKI_FACTORY_WEBHOOK_URL` on each task transition.
Payload is HMAC-signed with `MESHWIKI_FACTORY_WEBHOOK_SECRET` in `X-MeshWiki-Signature` header.

Key canonical events: `task.decomposed`, `task.approved`, `task.assigned`, `task.pr_created`, `task.pr_merged`, `task.pr_rejected`, `task.failed`, `task.blocked`, `task.completed`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHWIKI_FACTORY_ENABLED` | `false` | Enable factory features |
| `MESHWIKI_FACTORY_API_KEY` | `""` | API key for agent access |
| `MESHWIKI_FACTORY_WEBHOOK_URL` | `""` | Outbound webhook target (orchestrator) |
| `MESHWIKI_FACTORY_WEBHOOK_SECRET` | `""` | HMAC secret for outbound webhooks |
| `MESHWIKI_GITHUB_WEBHOOK_SECRET` | `""` | HMAC secret for inbound GitHub webhooks |

## Phase 2 Implementation Guide

The inbound GitHub webhook receiver (`src/meshwiki/api/webhooks.py`) needs to:

1. Accept `POST /api/v1/github/webhook` — no API key, verified via HMAC (`X-Hub-Signature-256`)
2. Handle `pull_request` events where `action == "closed"` and `merged == true`
3. Look up the task page by `pr_number` (use graph engine `metatable()` or scan task pages)
4. Transition the task: `review → merged`, then `merged → done`
5. Store `merged_at` timestamp in task frontmatter

PR-to-task lookup: query pages where `type=task` and `pr_number={pr_number}` using the graph engine's `query()` method, or fall back to scanning `list_pages_with_metadata()`.

Mount the router in `src/meshwiki/api/__init__.py` (already mounts `/api/v1`).

## Testing Strategy

- Unit tests for `task_machine.py` state transitions (valid + invalid)
- Unit tests for `webhooks.py` HMAC signing
- Integration tests for all `/api/v1/` endpoints (httpx TestClient with `X-API-Key`)
- Integration tests for GitHub webhook receiver (mock payload + HMAC)
- Test that invalid transitions return 422
- Test that missing pages return 404

Existing tests: `src/tests/test_task_machine.py`, `src/tests/test_api_*.py`

## Gotchas

- **API key is optional** — if `MESHWIKI_FACTORY_API_KEY` is empty, `require_api_key` passes all requests. Intentional for dev/local use.
- **GitHub webhook uses HMAC, not API key** — `POST /api/v1/github/webhook` authenticates via `X-Hub-Signature-256` (SHA-256 HMAC of the raw body), not the factory API key.
- **Graph engine optional** — `core/graph.py` may be unavailable; PR-to-task lookup must fall back to scanning `list_pages_with_metadata()` if graph engine is not loaded.
- **Frontmatter strings** — `pr_number` is stored as a string (for graph filter compatibility), not an int.
- **Factory Dashboard is a wiki page** — `Factory_Dashboard.md` uses `<<MetaTable>>` macros; it's created manually, no code needed.
