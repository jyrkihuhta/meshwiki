# MeshWiki

A modern, self-hosted wiki platform inspired by [MoinMoin](https://moinmo.in/), [Graphingwiki](http://graphingwiki.python-hosting.com/), and [Obsidian](https://obsidian.md/). MeshWiki combines file-based Markdown storage with a Rust-powered graph engine for metadata queries, backlinks, and interactive graph visualization.

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
- **Toast notifications** - Save/delete feedback with auto-dismiss animations
- **HTMX interactions** - Snappy server-rendered UI without heavy JavaScript
- **CI pipeline** - GitHub Actions running both Python and Rust test suites with coverage enforcement
- **Kubernetes-native** - Deployed via GitOps with Flux, Istio ingress, and Rancher management

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

## Screenshots

<!-- TODO: Add screenshots of page view, graph visualization, MetaTable -->

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

## Running Tests

```bash
# Install dev dependencies
cd src
pip install -e ".[dev]"

# Unit tests (200 tests)
pytest tests/ -v
pytest tests/ --cov=meshwiki    # With coverage

# E2E browser tests (49 tests, requires Playwright)
playwright install chromium      # First time only
pytest e2e/ -v --browser chromium

# All Python tests together
pytest tests/ e2e/ -v --browser chromium
```

```bash
# Rust graph engine tests (70 tests)
cd graph-core
source .venv/bin/activate
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
python -m pytest tests/ -v
```

**319 total tests** across all suites. CI runs automatically via GitHub Actions.

## Project Structure

```
meshwiki/
├── dev.sh                      # Development startup script
├── graph-core/                 # Rust graph engine
│   ├── Cargo.toml
│   ├── src/                    # Rust source (lib, graph, parser, models, query, events, watcher)
│   └── tests/                  # PyO3 integration tests (70 tests)
├── Dockerfile                  # Multi-stage build (Rust + Python)
├── .github/                    # CI, lint, stale workflows + issue/PR templates + Dependabot
├── scripts/                    # Utility scripts (remove-example-data.sh)
├── src/                        # Python application
│   ├── pyproject.toml
│   ├── meshwiki/
│   │   ├── main.py             # FastAPI routes + WebSocket endpoint
│   │   ├── config.py           # Settings (MESHWIKI_* env vars)
│   │   ├── core/
│   │   │   ├── storage.py      # Abstract storage + FileStorage
│   │   │   ├── parser.py       # Markdown + wiki links + MetaTable macro
│   │   │   ├── graph.py        # Rust engine wrapper (optional import)
│   │   │   ├── ws_manager.py   # WebSocket connection manager
│   │   │   └── models.py       # Pydantic models
│   │   ├── templates/          # Jinja2 templates (base, page views, graph)
│   │   └── static/             # CSS + D3.js graph visualization
│   ├── data/pages/             # Example wiki pages (11 pages)
│   └── tests/                  # Tests (204 tests)
├── docs/                       # Documentation
│   ├── architecture.md         # System design
│   ├── getting-started.md      # Setup and deployment guide
│   ├── custom-macros.md        # Macro developer guide
│   ├── prd/                    # Product requirements
│   ├── adr/                    # Architecture decision records
│   ├── domains/                # Domain-specific design docs
│   └── research/               # Background research
├── deploy/                     # Kubernetes deployment
│   ├── apps/meshwiki/         # K8s manifests (Deployment, Service, VirtualService)
│   └── flux/                   # Flux GitOps configuration
├── infra/local/                # Terraform for local k3d cluster
│   ├── main.tf                 # k3d cluster
│   ├── istio.tf                # Istio service mesh
│   └── rancher.tf              # Rancher management
└── data/pages/                 # Wiki content (gitignored)
```

## Configuration

Environment variables with `MESHWIKI_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHWIKI_DATA_DIR` | `data/pages` | Page storage directory |
| `MESHWIKI_DEBUG` | `false` | Debug mode |
| `MESHWIKI_APP_TITLE` | `MeshWiki` | Application title in header |
| `MESHWIKI_GRAPH_WATCH` | `true` | Enable file watcher for live graph updates |

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Backend | FastAPI (Python 3.12+) | Async web framework |
| Graph Engine | Rust (petgraph + PyO3) | Fast graph operations and metadata queries |
| Frontend | Jinja2 + HTMX | Server-rendered templates with dynamic updates |
| Visualization | D3.js | Interactive force-directed graph |
| Real-time | WebSocket + asyncio | Live graph updates |
| Storage | Markdown files + YAML frontmatter | Plain-text, git-friendly |
| Infrastructure | k3d + Istio + Rancher + Flux | Kubernetes-native GitOps deployment |
| IaC | Terraform | Local cluster provisioning |

## Kubernetes Deployment

For deploying to a local k3d cluster with Istio and Flux GitOps, see the [Getting Started](docs/getting-started.md) guide.

```bash
# Quick overview
cd infra/local && terraform apply     # Create k3d cluster + Istio + Rancher
docker build -t meshwiki:latest .    # Build from repo root (multi-stage with Rust)
k3d image import meshwiki:latest -c meshwiki
kubectl rollout restart deployment/meshwiki -n meshwiki
```

Access at **http://wiki.localhost:8080** (requires `/etc/hosts` entry).

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Setup guide for local dev and k8s deployment |
| [Architecture](docs/architecture.md) | System design and component overview |
| [Custom Macros](docs/custom-macros.md) | Developer guide for creating `<<Macro>>` extensions |
| [TODO](TODO.md) | Milestones and roadmap |
| [PRD: Infrastructure](docs/prd/001-infrastructure.md) | Infrastructure requirements |
| [PRD: MeshWiki MVP](docs/prd/002-meshwiki-mvp.md) | Application requirements |
| [ADR-001: k3d Approach](docs/adr/001-k3d-terraform-approach.md) | k3d Terraform decision |
| [Contributing](CONTRIBUTING.md) | Contributor guide |

## Status

| Milestones | Description | Status |
|------------|-------------|--------|
| 1–6 | Infrastructure, Wiki MVP, Graph Engine, Visualization | ✅ Complete |
| 7–8 | Editor Experience, Navigation & Discovery | ✅ Complete |
| 9 | Visual Polish & Responsiveness | ✅ Complete |
| 10–11 | Graph Enhancements, Macros | Planned |
| 12–13 | Authentication, Observability | Planned |

**319 tests** (200 unit + 49 E2E + 70 Rust), CI active. See [TODO.md](TODO.md) for the full roadmap.

## License

[MIT](LICENSE)
