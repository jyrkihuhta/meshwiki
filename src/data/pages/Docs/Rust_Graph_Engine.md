---
title: Rust Graph Engine
tags:
  - development
  - rust
  - architecture
status: active
author: alice
priority: high
---

# Rust Graph Engine

The `graph_core` crate provides high-performance graph operations for MeshWiki, compiled as a Python module via PyO3.

## Architecture

```
graph-core/src/
├── lib.rs      # PyO3 entry point, GraphEngine class
├── graph.rs    # petgraph WikiGraph
├── parser.rs   # YAML frontmatter + wiki link extraction
├── models.rs   # PageNode, WikiLink structs
├── query.rs    # Filter enum, query(), metatable()
├── events.rs   # GraphEvent enum, EventQueue
└── watcher.rs  # FileWatcher (notify crate)
```

## Key Types

### GraphEngine

The main PyO3 class exposed to Python:

```python
from graph_core import GraphEngine, Filter

engine = GraphEngine("/path/to/pages")
engine.scan_directory()

# Query
pages = engine.list_pages()
backlinks = engine.get_backlinks("PageName")
outlinks = engine.get_outlinks("PageName")
metadata = engine.get_metadata("PageName")

# MetaTable
result = engine.metatable(
    [Filter.equals("status", "active")],
    ["name", "status", "author"]
)
```

### Filter

Query filters for metadata:

| Method | Example | Matches |
|--------|---------|---------|
| `Filter.equals(k, v)` | `Filter.equals("status", "active")` | Exact match |
| `Filter.contains(k, v)` | `Filter.contains("tags", "python")` | Substring |
| `Filter.matches(k, v)` | `Filter.matches("author", "^a.*")` | Regex |

### WikiGraph

Internal graph built on `petgraph::DiGraph`:

- Nodes = pages (with metadata from frontmatter)
- Edges = wiki links between pages
- Thread-safe via `Arc<Mutex<WikiGraph>>`

## File Watching

The `FileWatcher` uses the `notify` crate with 500ms debounce:

1. Watches `data/pages/` for file changes
2. On change: re-parses the file, updates graph
3. Pushes `GraphEvent` to the event queue
4. Python polls events via `poll_events()` at 0.5s intervals
5. Events are broadcast to WebSocket clients

## Building

```bash
cd graph-core
source .venv/bin/activate
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
python -m pytest tests/ -v  # 70 tests
```

## Testing

70 integration tests covering:

- Graph construction and queries
- Frontmatter parsing
- Wiki link extraction
- Filter operations
- MetaTable results
- Event system
- File watching

## Related

- [[Docs/Architecture Overview]] - Full system architecture
- [[Docs/Python Development]] - Python app development
- [[Docs/MetaTable Queries]] - Using MetaTable from wiki pages
