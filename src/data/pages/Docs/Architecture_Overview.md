---
author: admin
created: '2026-02-11T12:02:08.889723'
modified: '2026-02-11T12:02:08.889723'
priority: high
status: active
tags:
- documentation
- architecture
title: Architecture Overview
---

# Architecture Overview

MeshWiki is built with a layered architecture combining Python and Rust.

## Components

### FastAPI Application (Python)

The web layer handles HTTP requests, template rendering, and WebSocket connections.

- **Routes** - Page CRUD, search, tags, graph API
- **Storage** - Abstract `Storage` class with `FileStorage` implementation
- **Parser** - Markdown processing with custom extensions (wiki links, MetaTable, strikethrough)
- **WebSocket Manager** - Real-time graph event broadcasting

### Graph Engine (Rust)

A high-performance graph engine compiled as a Python module via PyO3.

- **petgraph** - Directed graph data structure
- **YAML parsing** - Frontmatter extraction with `serde_yaml`
- **Link extraction** - Wiki link parsing with `pulldown-cmark`
- **File watching** - Live updates via `notify` crate with 500ms debounce
- **Query engine** - Filter-based metadata queries for [[Docs/MetaTable Queries|MetaTable]]

### Frontend

Server-rendered HTML with minimal JavaScript:

- **Jinja2** - Template engine
- **HTMX** - Dynamic updates without heavy JS
- **D3.js** - Force-directed graph visualization at `/graph`

## Data Flow

```
Markdown Files (data/pages/*.md)
    |
    ├── FileStorage (Python) ──> Page CRUD, Search
    |
    └── GraphEngine (Rust) ──> Backlinks, MetaTable, Graph API
            |
            └── FileWatcher ──> WebSocket ──> D3.js Graph
```

## Storage

Pages are stored as plain Markdown files:

- Location: `data/pages/`
- Naming: `PageName.md` (spaces become underscores)
- Frontmatter: YAML between `---` delimiters
- Content: Standard Markdown with wiki link extensions

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | File-based | Git-friendly, simple backup |
| Graph | Rust + petgraph | Performance for large wikis |
| Frontend | HTMX + Jinja2 | Server-rendered, minimal JS |
| Real-time | WebSocket | Live graph updates |
| Deployment | Kubernetes + Flux | GitOps, scalable |

## Related

- [[Project Roadmap]] - Development timeline
- [[Docs/Getting Started]] - Setup instructions
- [[Docs/MetaTable Queries]] - Query engine usage
- [[Docs/Kubernetes Setup]] - Deployment details

