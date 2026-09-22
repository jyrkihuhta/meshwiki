# Agent Factory — Scope & Authorization (opt-in)

> This content was split out of `CLAUDE.md` so the main guidelines stay focused on the
> wiki application. It documents the **optional** agent-factory subsystem and the
> separate Molly service. Nothing in the wiki depends on any of this. To re-enable it
> for Claude Code, paste the "Scope & Authorization" section below back into
> `CLAUDE.md`.

## Scope & Authorization

The wiki application itself performs no security testing of any kind — it stores and
renders pages. The optional agent-factory subsystem, and the separate Molly
security-testing service it can drive, operate only against explicitly authorized
targets:

- **Bug-bounty programs the operator is enrolled in** on HackerOne and Intigriti,
  tested strictly within each program's published scope. Every request is checked
  against per-target allow/deny rules (`scope_rules` / `scope_exclude_rules`) by a Rust
  `ScopeChecker` before it is sent; out-of-scope hosts are blocked.
- **Deliberately-vulnerable practice targets** on a private range (e.g. DVGA and other
  "target-dummy" apps) used for regression testing. Staging is locked to this practice
  range and never points at live third-party targets.

This is authorized, scoped security testing. There is no capability for testing hosts
outside the configured, authorized scope.

## Molly service — Caddy routing note

This is the Molly-specific version of gotcha #30 (the generalized version remains in
`CLAUDE.md`). Caddy (`meshwiki-caddy-1`) is on `meshwiki_default`. Services from other
Compose projects (e.g. `molly` from `wintermutecore`) must be explicitly connected:
`docker network connect meshwiki_default molly`. Once connected, Caddy resolves them by
container name. Use `${VPS_DOMAIN}` for wiki/staging domains; hardcode other service
domains (e.g. `molly.penni.fi`) that belong to different projects.

## Related

- `docs/prd/003-agent-factory.md` — agent factory full spec
- `docs/domains/factory.md` — factory domain doc (JSON API, state machine, webhooks)
