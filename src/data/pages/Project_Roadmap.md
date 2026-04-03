---
title: Project Roadmap
tags:
  - project
  - planning
status: active
author: admin
priority: high
---

# Project Roadmap

Current development status and planned milestones.

## Completed

- [x] **M1-4**: Rust graph engine foundation (petgraph, PyO3, file watching)
- [x] **M5**: Python integration (backlinks, MetaTable, frontmatter)
- [x] **M6**: Real-time visualization (D3.js, WebSocket)
- [x] **M7**: Editor experience (live preview, toolbar, shortcuts, autocomplete)
- [x] **M8**: Navigation & discovery (search, TOC, tags, recent changes)
- [x] **M9**: Visual polish (dark mode, responsive layout, toast notifications, syntax highlighting)

## In Progress

### M10: Graph Enhancements

- [ ] Search/filter on graph page
- [ ] Node sizing by connection count
- [ ] Focus mode (click to show neighborhood)
- [ ] Hover tooltips with page info
- [ ] Legend for node colors

## Planned

### M11: Macro System

- [ ] `<<PageList(tag=value)>>` macro
- [ ] `<<RecentChanges(n=10)>>` macro
- [ ] `<<BackLinks>>` inline macro
- [ ] `<<PageCount>>` macro

### M12: Authentication

- [ ] User accounts and login
- [ ] Edit attribution
- [ ] Access control

### M13: Observability

- [ ] Structured logging (structlog)
- [ ] Metrics endpoint (`/metrics`)

## Stats

- **274 tests** passing (70 Rust + 204 Python)
- **CI/CD** active via GitHub Actions
- **GitOps** deployment with Flux

## Related

- [[Docs/Architecture Overview]] - System design
- [[Docs/Kubernetes Setup]] - Deployment infrastructure
- [[Docs/Getting Started]] - User guide
