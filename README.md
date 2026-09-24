# MeshWiki

A modern, self-hosted wiki platform inspired by [MoinMoin](https://moinmo.in/), [Graphingwiki](http://graphingwiki.python-hosting.com/), and [Obsidian](https://obsidian.md/). MeshWiki combines file-based Markdown storage with a Rust-powered graph engine for metadata queries, backlinks, and interactive graph visualization.

MeshWiki has two layers:

1. **The wiki** — a fast, standalone Markdown wiki with `[[WikiLinks]]`, backlinks, metadata queries, and a live graph view. This is the mature core and runs on its own with no external services.
2. **The agent factory** *(optional, experimental)* — an autonomous software-development layer that turns wiki "task pages" into work carried out by LLM coding agents that open pull requests. It's off by default; see [Agent Factory](#agent-factory-experimental) below.

New here? Start with [Quick Start](#quick-start) to get the wiki running — you can ignore the factory entirely until you want it.

## Features

- **Markdown wiki pages** - Full Markdown support with tables, code blocks, task lists, strikethrough, and more
- **Wiki links** - `[[PageName]]` and `[[PageName|Display Text]]` syntax with missing-page detection
- **Backlinks** - Panel showing all pages that link to the current page
- **Frontmatter metadata** - YAML frontmatter displayed in page view, queryable via graph engine
- **MetaTable queries** - Graphingwiki-style metadata queries via `<<MetaTable(...)>>` macro with YAML frontmatter
- **Custom macros** - Extensible `<<Macro(...)>>` system for embedding dynamic content ([developer guide](docs/custom-macros.md))
- **Graph visualization** - Interactive D3.js force-directed graph at `/graph` with real-time WebSocket updates
- **Rust graph engine** - Fast graph operations powered by [petgraph](https://github.com/petgraph/petgraph) + [PyO3](https://pyo3.rs/)
- **File-based storage** - Pages stored as plain Markdown files, easy to backup and version control
- **Split-pane editor** - Optional live Markdown preview (toggle with Ctrl+P), toolbar, keyboard shortcuts (Ctrl+B/I/K/S), wiki link autocomplete
- **Search & discovery** - Instant search box, full-text search, tag index, TOC sidebar, breadcrumbs, recently modified pages
- **Dark mode** - One-click toggle with CSS custom properties, persisted in localStorage
- **Responsive design** - Mobile-friendly with hamburger nav, stacked layouts below 768px
- **Syntax highlighting** - Fenced code blocks highlighted via highlight.js with light/dark themes
- **HTMX interactions** - Snappy server-rendered UI without heavy JavaScript
- **Agent factory** *(optional)* - Autonomous LLM coding agents driven from wiki task pages ([details](#agent-factory-experimental))
- **CI pipeline** - GitHub Actions running Python and Rust test suites with coverage enforcement
- **Easy self-hosting** - Docker Compose + Caddy on any VPS, with automatic HTTPS and CI/CD

## Quick Start

### With Rust graph engine (recommended)

```bash
git clone https://github.com/jyrkihuhta/meshwiki.git
cd meshwiki
./dev.sh
```

This builds the Rust graph engine via [Maturin](https://www.maturin.rs/), installs Python dependencies, and starts the server at **http://localhost:8000**.

Options:
```bash
./dev.sh --skip-build    # Start server without rebuilding Rust
./dev.sh --build-only    # Build Rust engine only
```

### Without Rust engine

The app works without the graph engine — graph features (backlinks, MetaTable, visualization) gracefully degrade.

```bash
cd src
pip install -e .
uvicorn meshwiki.main:app --reload
```

### Example Pages

The repository includes example wiki pages in `src/data/pages/` that demonstrate wiki links, MetaTable queries, frontmatter metadata, task lists, and more. To load them, copy them into your data directory:

```bash
cp src/data/pages/*.md data/pages/
```

To remove the example pages and start fresh:

```bash
./scripts/remove-example-data.sh
```

### Prerequisites

- **Python 3.12+**
- **Rust** (install via [rustup](https://rustup.rs/)) — only needed for graph features
- **Maturin** — installed automatically by `dev.sh`, or `pip install maturin`

## Usage

### Wiki Links

Link to other pages with double-bracket syntax:

```markdown
See [[OtherPage]] for details.
Check the [[Setup Guide|guide]] to get started.
```

Existing pages render as links; missing pages render with distinct styling and link to the editor.

### MetaTable Queries

Query page metadata using YAML frontmatter. Add frontmatter to your pages:

```yaml
---
status: draft
author: alice
tags: [python, wiki]
---
```

Then use the MetaTable macro to query across pages:

```markdown
<<MetaTable(status=draft, ||name||status||author||)>>
```

Filter operators: `key=value` (equals), `key~=substring` (contains), `key/=regex` (matches).

### Graph Visualization

Visit `/graph` for an interactive force-directed graph of all pages and their links. Nodes are clickable, draggable, and update in real-time as pages change.

## Agent Factory (experimental)

The factory is an optional layer that lets MeshWiki build software on its own. You describe a task on a wiki "task page" (with structured frontmatter), and a [LangGraph](https://langchain-ai.github.io/langgraph/) orchestrator decomposes it into subtasks, dispatches LLM coding agents ("grinders") that run in sandboxes, opens GitHub pull requests, and drives a review loop back into the wiki via webhooks.

- **Status:** v1 complete (phases 1–7); v2 (phases 8–11) in progress. It is functional but still evolving — treat it as experimental.
- **Off by default:** the factory API is disabled unless you set `MESHWIKI_FACTORY_ENABLED=true`, and it fails closed unless a `MESHWIKI_FACTORY_API_KEY` is configured. The wiki works fully without ever enabling it.
- **External dependencies:** it needs LLM API access (Anthropic-compatible, MiniMax by default) and, for sandboxed execution, [E2B](https://e2b.dev/); it also uses the GitHub API to open PRs.
- **Learn more:** [`docs/domains/factory.md`](docs/domains/factory.md) (design and scope), [`docs/runbook.md`](docs/runbook.md) (operations), and the PRD at [`docs/prd/003-agent-factory.md`](docs/prd/003-agent-factory.md).

The orchestrator lives in [`orchestrator/`](orchestrator/) and runs as its own service; the wiki-side API that task pages talk to lives under `src/meshwiki/api/`.

## Running Tests

```bash
# Web app (unit + E2E) — from the src/ directory
cd src
pip install -e ".[dev]"
pytest tests/ -v                       # ~750 unit tests
pytest tests/ --cov=meshwiki           # with coverage

playwright install chromium            # first time only
pytest e2e/ -v --browser chromium      # 49 E2E browser tests
```

```bash
# Rust graph engine — from graph-core/
cd graph-core
source .venv/bin/activate
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
cargo test                             # ~70 Rust tests
python -m pytest tests/ -v             # ~70 PyO3 integration tests
```

```bash
# Agent factory orchestrator — from orchestrator/
cd orchestrator
pip install -e ".[dev]"
pytest tests/ -v                       # ~600 tests
```

Roughly **1,500 tests** across the web app, orchestrator, E2E, and Rust suites. CI runs the Python and Rust suites automatically via GitHub Actions.

## Project Structure

```
meshwiki/
├── dev.sh                      # Development startup script
├── Makefile                    # Common dev/test/build tasks
├── Dockerfile                  # Multi-stage build (Rust + Python)
├── graph-core/                 # Rust graph engine
│   ├── Cargo.toml
│   ├── src/                    # Rust source (lib, graph, parser, models, query, events, watcher)
│   └── tests/                  # PyO3 integration tests
├── src/                        # Python web application
│   ├── pyproject.toml
│   ├── meshwiki/
│   │   ├── main.py             # FastAPI routes + WebSocket endpoints
│   │   ├── config.py           # Settings (MESHWIKI_* env vars)
│   │   ├── api/                # Agent-factory JSON API (pages, tasks, webhooks)
│   │   ├── core/
│   │   │   ├── storage.py      # Abstract storage + FileStorage
│   │   │   ├── parser.py       # Markdown + wiki links + macros
│   │   │   ├── graph.py        # Rust engine wrapper (optional import)
│   │   │   ├── task_machine.py # Factory task state machine
│   │   │   ├── ws_manager.py   # WebSocket connection manager
│   │   │   └── models.py       # Pydantic models
│   │   ├── templates/          # Jinja2 templates (base, page views, graph)
│   │   └── static/             # CSS + D3.js graph visualization
│   ├── data/pages/             # Example wiki pages
│   └── tests/                  # Web-app tests
├── orchestrator/               # Agent factory (LangGraph orchestrator + agents)
│   ├── factory/                # State, nodes, agents (grinder, PM), integrations
│   └── tests/                  # Orchestrator tests
├── .github/                    # CI, lint, stale workflows + issue/PR templates + Dependabot
├── scripts/                    # Utility scripts (remove-example-data.sh, etc.)
├── docs/                       # Documentation (see the table below)
├── deploy/
│   ├── vps/                    # VPS deployment (Docker Compose + Caddy)
│   ├── apps/meshwiki/          # K8s manifests (Deployment, Service, VirtualService)
│   └── flux/                   # Flux GitOps configuration
├── infra/local/                # Terraform for local k3d cluster (main/istio/rancher)
└── data/pages/                 # Wiki content (gitignored)
```

## Configuration

Environment variables with the `MESHWIKI_` prefix (web app):

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHWIKI_DATA_DIR` | `data/pages` | Page storage directory |
| `MESHWIKI_DEBUG` | `false` | Debug mode |
| `MESHWIKI_APP_TITLE` | `MeshWiki` | Application title in header |
| `MESHWIKI_GRAPH_WATCH` | `true` | Enable file watcher for live graph updates |
| `MESHWIKI_AUTH_ENABLED` | `false` | Enable password authentication |
| `MESHWIKI_AUTH_PASSWORD` | | Login password (required if auth enabled) |
| `MESHWIKI_SESSION_SECRET` | `dev-secret-...` | Session signing key (**change in production**) |
| `MESHWIKI_FACTORY_ENABLED` | `false` | Enable the agent-factory JSON API |
| `MESHWIKI_FACTORY_API_KEY` | | API key for the factory API — **required when the factory is enabled** (it fails closed without one) |

The orchestrator service is configured separately via `FACTORY_`-prefixed variables (LLM keys, GitHub token, E2B); see [`docs/domains/factory.md`](docs/domains/factory.md).

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Backend | FastAPI (Python 3.12+) | Async web framework |
| Graph Engine | Rust (petgraph + PyO3) | Fast graph operations and metadata queries |
| Frontend | Jinja2 + HTMX | Server-rendered templates with dynamic updates |
| Visualization | D3.js | Interactive force-directed graph |
| Real-time | WebSocket + asyncio | Live graph updates |
| Storage | Markdown files + YAML frontmatter | Plain-text, git-friendly |
| Orchestrator | LangGraph (Python) | Agent-factory task decomposition and dispatch |
| Infrastructure | Docker Compose + Caddy | Self-hosted VPS with automatic HTTPS |
| IaC | Terraform | Local cluster provisioning |

## Self-Hosted Deployment

Deploy your own MeshWiki instance on any Linux VPS in a few minutes:

### Prerequisites

- A Linux VPS with Docker and Docker Compose installed
- A domain name with DNS A record pointing to your VPS IP

### Setup

1. **Clone and configure:**

   ```bash
   git clone https://github.com/jyrkihuhta/meshwiki.git
   cd meshwiki
   sudo mkdir -p /opt/meshwiki/data/pages
   sudo chown -R 1001:1001 /opt/meshwiki/data  # app user UID inside container
   cp deploy/vps/.env.example /opt/meshwiki/.env
   # Edit /opt/meshwiki/.env — set MESHWIKI_AUTH_PASSWORD and MESHWIKI_SESSION_SECRET
   ```

2. **Edit `deploy/vps/Caddyfile`** — replace `${VPS_DOMAIN}` with your domain, or set it as a `VPS_DOMAIN` GitHub secret if using the CI/CD pipeline.

3. **Copy files and start:**

   ```bash
   cp deploy/vps/docker-compose.prod.yml /opt/meshwiki/docker-compose.yml
   cp deploy/vps/Caddyfile /opt/meshwiki/Caddyfile
   cd /opt/meshwiki
   docker compose up -d
   ```

Caddy automatically provisions HTTPS certificates via Let's Encrypt. Your wiki is live at `https://yourdomain.example`.

Open ports 80 and 443 in your firewall.

### CI/CD (optional)

The included GitHub Actions pipeline builds a multi-arch Docker image (amd64 + arm64), pushes to GHCR, and deploys to your VPS via SSH — with health checks and automatic rollback on failure. Add these GitHub environment secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.

### Kubernetes (alternative)

For local development with k3d, Istio, and Flux GitOps, see the [Getting Started](docs/getting-started.md) guide.

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Setup guide for local dev and k8s deployment |
| [Architecture](docs/architecture.md) | System design and component overview |
| [Custom Macros](docs/custom-macros.md) | Developer guide for creating `<<Macro>>` extensions |
| [Agent Factory](docs/domains/factory.md) | Design and scope of the autonomous agent factory |
| [Runbook](docs/runbook.md) | Operating the factory and services |
| [TODO](TODO.md) | Milestones and roadmap |
| [PRD: Infrastructure](docs/prd/001-infrastructure.md) | Infrastructure requirements |
| [PRD: MeshWiki MVP](docs/prd/002-meshwiki-mvp.md) | Application requirements |
| [PRD: Agent Factory](docs/prd/003-agent-factory.md) | Agent factory requirements |
| [ADR-001: k3d Approach](docs/adr/001-k3d-terraform-approach.md) | k3d Terraform decision |
| [Contributing](CONTRIBUTING.md) | Contributor guide |

## Status

| Area | Status |
|------|--------|
| Wiki MVP — pages, wiki links, backlinks, metadata, search, editor | ✅ Complete |
| Rust graph engine + live graph visualization | ✅ Complete |
| Hardened CI/CD, VPS deployment, auth, structured logging, metrics | ✅ Complete |
| Agent factory v1 (phases 1–7) | ✅ Complete |
| Agent factory v2 (phases 8–11) | 🚧 In progress |

See [TODO.md](TODO.md) for the full roadmap.

## License

[MIT](LICENSE)
