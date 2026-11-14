# Deep review of `staging` (November 2026)

A whole-tree review of `staging` at commit `f59fde5`, covering the wiki app, the
agent factory, the Rust graph engine, and deploy/CI, each for security,
correctness, performance, and architecture/tests. Code only: nothing on the VPS
was inspected. No code was changed.

- **This file:** summary, the findings grouped into themes, and a table of every
  finding (Appendix A).
- **[`2026-11-deep-review-details.md`](2026-11-deep-review-details.md):** the full
  description, failure scenario and suggested fix for each high and medium
  finding (Appendix B).
- **`TODO.md`, section "Deep review (2026-11)":** the same work as a prioritised
  checklist.

## Result

| | Count |
|---|---|
| Findings reported by reviewers | 320 |
| Held up after an independent check | 316 |
| Refuted | 3 |
| Could not be settled | 1 |

Of the 316: **3 high, 120 medium, 180 low, 13 informational**. By area: wiki app
101, agent factory 106, Rust engine 19, deploy and CI 46, cross-cutting 44.

Read the counts with two things in mind:

- **They overstate the number of distinct problems.** Units overlapped on
  purpose, so one root cause often appears two to five times (for example, the
  auth configuration problem was reported by five units). The themes below merge
  them; expect roughly 60 to 70 pieces of work, not 316.
- **Most findings are new relative to TODO.md** (284). The other 32 correct,
  extend, or close items already tracked there, or concern PR #224.

## How it was done

1. **Review.** 22 reviewers, one per unit of the codebase, each reading its files
   in full and reporting findings with a location, a failure scenario and a fix.
2. **Verify.** Every finding was handed to a separate skeptic whose job was to
   refute it by re-reading the code (and running small snippets where that
   settled it). Verdicts: 182 confirmed, 134 confirmed with a correction (usually
   a lower severity or a narrower scope), 3 refuted, 1 undetermined.
3. **Reproduce.** Every finding still rated high after step 2 got a second check
   that tried to demonstrate it by running the real code. 16 were sent; 15
   completed. All 15 were demonstrated, but 11 were downgraded from high to
   medium and one had its claimed impact rejected. Severity inflation was the
   most common reviewer error.
4. **Gaps.** A completeness critic compared what was covered against the
   repository and listed what nobody owned (see "Not covered").

Limits worth knowing:

- Medium and low findings were checked by reading, not by execution, unless the
  reviewer happened to run them. 14 findings in total carry a "reproduced" mark.
- The Rust watcher was exercised on macOS only; production runs Linux.
- PR #224 (sanitizer and CSP) was open during the review and is not part of
  `staging`. Reviewers were told not to re-report what it fixes. Four findings
  concern the PR itself.
- The run was interrupted twice by usage limits and once by the machine
  sleeping. Reviews ran on Fable 5.2; about two thirds of the verification ran
  on Opus. One second check never completed (noted under theme 3).

## High-severity findings

### 1. Authentication fails open when secrets are missing or left at defaults

`wiki-routes-a-3` (reproduced), also `wiki-routes-b-4`, `deploy-vps-1`,
`cross-architecture-docs-1`, `deploy-k8s-infra-1`.

Nothing ties `MESHWIKI_AUTH_ENABLED=true` to a usable password and session
secret. Three cases were demonstrated against the real app:

- No password set: submitting an empty password logs in.
- `MESHWIKI_SESSION_SECRET` unset: the secret is the default string from
  `config.py`, so anyone can sign their own `authenticated` cookie. The forged
  cookie gave full write access, and also passes the WebSocket, `/api/graph` and
  `/metrics` session checks.
- `MESHWIKI_SESSION_SECRET=` (blank, as shipped in `deploy/vps/.env.example` and
  `staging.env.example`): the blank value overrides the default and is accepted,
  with the same result.

What makes this reachable without operator error: the staging deploy step in
`ci.yml` copies `staging.env.example` into place when `staging.env` is missing,
which yields the example password and a blank secret.

**Fix:** validate at startup. With auth on, refuse to start unless the password
is non-empty and the secret is non-empty, not the default, and long enough.
Reject empty passwords in the login route. Stop CI from installing the example
file. **Check now:** that the session secret on both VPS environments is set to
a real value (this review did not look).

### 2. One page with badly typed frontmatter takes the whole wiki down

`wiki-storage-1` (reproduced), with `wiki-storage-2`, `wiki-routes-b-1`,
`wiki-routes-a-1`, `wiki-routes-a-7` (the last four reproduced or verified at
medium).

`_parse_frontmatter` catches YAML errors but not pydantic validation errors, and
the loops that scan every page have no per-file guard. Ordinary YAML triggers
it: `title: 2024`, `tags: python`, `children: A`, a date-typed title, or a
non-UTF-8 file. Once such a file exists, listings, search, tags, the sidebar,
and after the next save or restart every HTML route including `/login`, return
500.

The editor cannot create such a file (it parses before writing), but two other
paths can:

- **Inline MetaTable editing** (`PATCH /api/page/{name}/metadata`) sets the
  field without validation. Editing a `children` cell to a plain word was enough
  to reproduce the outage end to end.
- **Anything that writes files directly:** the factory's bots build frontmatter
  by string interpolation, and pages also arrive by hand or by git.

**Fix:** catch `ValidationError` in `_parse_frontmatter` and degrade (coerce or
move bad values to extras); guard each file in every scan loop, log and skip;
validate in the PATCH endpoint; make the sidebar tree builder tolerate
non-string `parent_task`.

### 3. The factory cannot complete a task with more than one subtask

`factory-tests-1` (reproduced, high), `factory-graph-nodes-1` (reproduced,
medium), with `factory-graph-nodes-2`, `-3`, `-6`, `-7`, `-8`.

Two second checks reproduced the same defect and disagreed on severity: one kept
it high, one rated it medium because the factory is optional, staging-only, and
most staging tasks use `skip_decomposition` (one subtask). It is listed here
because it breaks the factory's main design, and because CI is green on it.

- **Fan-in never fires.** When two or more subtasks are reviewed in the same
  LangGraph superstep, each `pm_review` branch sees its siblings as still in
  review and routes to END. The run stops with all subtasks merged and the
  parent never finalised. Reproduced with 2 and 3 subtasks on the real compiled
  graph.
- **Deferred subtasks are never dispatched.** There is no edge back to
  `assign_grinders`, so anything held back by the concurrency cap or the
  file-conflict rule stays pending forever. TODO.md marks this fixed; the
  loop-back was lost in a later restructure. (`factory-tests-2`, which reports
  the same thing from the test side, did not get its second check; it is rated
  medium here to match.)
- **`active_grinders` only grows**, and is filled when a grinder finishes, not
  when it starts, so the cap counts the wrong thing.

**Fix:** route every `pm_review` branch through one join node that decides
finalise / wait / re-dispatch on the merged state, restore the path back to
`assign_grinders`, and add real-graph tests with two or more subtasks.

## Medium findings by theme

IDs refer to Appendix A; full text is in the details file.

### Factory graph: runs that end in the wrong state

- **A failed review counts as approval.** A failed PM review or failed subtask
  is treated as terminal-and-fine, and the parent is marked done.
  (`factory-graph-nodes-6`, reproduced)
- **"Merged" without a merge.** A subtask is recorded as merged even when the PR
  was not merged; `merge_check` cannot catch it. (`factory-graph-nodes-7`,
  reproduced; `factory-tests-7`)
- **Human approval does not resume the run.** The webhook resumes with a dict
  input, which restarts the graph at `task_intake` instead of continuing from
  the interrupt. (`factory-graph-nodes-8`, reproduced; `factory-server-1`,
  `factory-tests-4`)
- **Restart pushes waiting tasks through as rejected**, and auto-fails
  in-progress subtask pages because they have no checkpoint of their own.
  (`factory-server-2`, `factory-server-3`)
- **Escalation always abandons;** retry and redecompose are unreachable, and the
  PM-diagnose retry fails fast without re-running the grinder.
  (`factory-graph-nodes-4`, `-5`, `factory-pm-4`, `factory-tests-3`)
- **State leaks between runs** on the same thread; human rejection re-reviews an
  arbitrary subtask and drops the feedback; an empty dispatch ends the run
  silently; partly created decompositions are finalised as complete.
  (`factory-graph-nodes-9`, `-11`, `-12`, `-13`, `factory-server-4`)
- **Why CI is green:** routers are tested with hand-built states and the
  pipeline tests use one subtask and a different resume call than production.
  (`factory-graph-nodes-17`)

### Wiki ↔ factory contract

- **Transition fields are silently dropped.** The client sends `pr_url`,
  `branch`, `cost_usd` and similar flattened; the wiki expects them nested under
  `extra_fields`. (`cross-trust-boundaries-1`, reproduced)
- **The GitHub merge webhook can never match a task:** nothing writes
  `pr_number`, and the graph lookup always throws.
  (`cross-trust-boundaries-4`, `wiki-api-core-1`)
- **Read-modify-write of whole pages.** The client and bots update pages with
  GET then PUT; the 60-second heartbeat can revert a status transition and drop
  concurrent appends, without firing webhooks. (`cross-trust-boundaries-5`,
  `factory-bots-4`, `factory-server-8`)
- **The factory can overwrite arbitrary pages.** PM-chosen subtask page names
  are written with a create-or-overwrite PUT. (`cross-trust-boundaries-8`)
- **Page names are not URL-encoded:** `?` or `#` in a name sends the write to a
  different page. (`factory-server-7`; extends a TODO item)
- **Status that does not exist.** The bookkeeper queries `in_review`, so its
  merged-PR reconciliation never runs. (`factory-bots-1`,
  `cross-trust-boundaries-10`)
- **Slash-named pages** created by bots cannot be viewed, edited or deleted in
  the browser. (`cross-architecture-docs-2`)

### Wiki: task state machine and concurrent edits

- **The state machine has side doors.** Inline metadata edit, a two-step save,
  and revision restore all change `status` without the transition check.
  (`wiki-routes-b-2`, `wiki-routes-a-6`, `wiki-frontend-7`,
  `cross-architecture-docs-3`)
- **No conflict detection on save.** An editor tab holding old content silently
  overwrites concurrent bot updates and can fire factory transitions from a
  stale `status`. (`wiki-routes-b-6`, `wiki-frontend-8`, `wiki-storage-12`)
- **Page cache goes stale.** Metadata PATCH and several other write paths never
  invalidate it; an invalidation that lands during a scan is lost; a 60-second
  dedup drops the second update to a page. (`wiki-routes-b-3`, `wiki-storage-4`,
  `-5`, `wiki-api-core-10`)

### Wiki: storage integrity

- **Rename overwrites.** Renaming onto an existing page silently replaces it,
  then returns 500. (`wiki-storage-3`, `wiki-api-core-3`)
- **Writes are not atomic** and are not ordered with the revision insert.
  (`wiki-storage-8`)
- **Revision store grows without bound:** a full copy per write, including no-op
  saves and every bot frontmatter patch. (`wiki-storage-9`, `factory-bots-5`)
- **Names are not canonicalised or encoded.** History, restore and delete can
  operate on a different key than the file; `#` or `?` in a name redirects
  writes to another page. (`wiki-routes-a-5`, `wiki-routes-b-7`,
  `wiki-frontend-4`)

### Wiki: denial of service and resource limits

- **Unbounded Prometheus label series** driven by unauthenticated requests.
  (`wiki-routes-a-4`, `wiki-routes-b-5`, `wiki-api-core-7`)
- **Include macro has no expansion budget;** wildcard includes grow
  exponentially and block the event loop. (`wiki-parser-1`)
- **Quadratic macro regexes:** a 50 KB page with unterminated openers costs
  about 9 to 13 seconds of blocked event loop. (`wiki-parser-3`)
- **Macros raise on author input** and turn the page into a 500.
  (`wiki-parser-6`)
- **Terminal sessions are never freed** and have no size limit.
  (`wiki-api-core-8`)
- **Outbound webhook dispatcher** loses events on shutdown and overflow; one
  dead endpoint stalls all deliveries. (`wiki-api-core-11`)

### Wiki: frontend behaviour under `hx-boost`

- Boosted navigation to `/search` replaces the whole page with the dropdown
  partial. (`wiki-frontend-1`)
- Boosted save returns a degraded view, loses the toast, and fails silently.
  (`wiki-frontend-2`)
- The unsaved-changes guard is bypassed. (`wiki-frontend-3`)
- The live-update WebSocket is closed by any swap and never reopened; body
  scripts re-run on every navigation and stack up polling loops and sockets.
  (`wiki-frontend-5`, `-6`)
- Rendering logic is copied across three routes and the copies have drifted.
  (`wiki-routes-b-15`)

### Rust graph engine

- **New and renamed pages may never be indexed.** Stub nodes are stamped with
  the current time, and the watcher's mtime short-circuit compares against it.
  (`rust-bindings-1`, reproduced; `rust-graph-3`)
- **Names disagree.** The engine keeps filename underscores while link targets
  keep spaces, so links to multi-word pages attach to stubs and real pages
  report as missing. (`rust-graph-2`, `rust-bindings-3`)
- **Deleting a page drops its incoming edges;** backlinks do not come back when
  it is recreated, until restart. (`rust-graph-1`, `rust-bindings-4`)
- **Regex filters are recompiled per page** while holding the GIL and the graph
  lock; one MetaTable can freeze the server. (`rust-bindings-2`, reproduced)
- **Watcher errors are swallowed;** `start_watching()` reports success after the
  thread has exited. (`rust-bindings-6`)
- **The Rust unit tests cannot link and never run in CI** (68 `#[test]`
  functions). (`rust-graph-6`, `rust-bindings-7`)

### Factory agents

- **MiniMax/Kilo key is not scrubbed** from the terminal stream or the persisted
  log; only the GitHub token is. (`factory-grinder-1`)
- **PR URL is scraped** as the last `github.com/.../pull/N` string in raw
  terminal output, with no check that it is this task's PR.
  (`factory-grinder-2`, `factory-pm-3`)
- **Triage can approve and auto-merge** from the first 500 diff lines with no
  tools. (`factory-pm-1`)
- **Local grinder is not contained:** writes under `.git/` and `run_tests` give
  command execution on the orchestrator host. Not the default provider.
  (`factory-grinder-4`; extends M4)
- **Rework breaks on the shallow clone;** bootstrap errors bypass the clone
  check; each attempt's 25k-character log is appended to the page and fed back
  into the next prompt. (`factory-grinder-3`, `-5`, `-6`)
- **PM default path** has no retry, a 60-second timeout and no second provider;
  a review that ends without a decision fails the subtask; `github_read_file`
  cannot work during decomposition although the prompt requires it.
  (`factory-pm-6`, `-8`, `-9`)

### Factory bots and armory

- **Frontmatter built by string interpolation.** Quotes break the page; newlines
  in external text inject trusted keys such as `repo` or `parent_task`.
  (`factory-armory-2`, `factory-bots-7`)
- **Armory validator passes what it cannot see** (no frontmatter, missing patch,
  null values), and the forbidden-import gate is easy to bypass.
  (`factory-armory-3`, `-4`)
- **Dedup gaps:** class-gap generic suggestions are never deduplicated; the
  armory-research cap is applied before the existing-page skip, so leftovers
  starve later findings; the armory survey truncates at 1,000 playbooks.
  (`factory-armory-1`, `-5`, `-7`)
- **Heartbeat rule** cannot detect hung tasks and fails healthy ones after
  outages and human-review pauses. (`factory-bots-2`)
- **Every restart re-runs all bots at once,** including the weekly and daily
  ones; the base loop leaks one asyncio task per tick; the CI-fixer can spam PR
  comments. (`factory-bots-10`, `-6`, `-9`)
- **`scripts/abandon_dropped_target_tasks.py`** blocks every open playbook task
  if the upstream list comes back empty. (`deploy-vps-9`)

### Deploy and CI

- **Production deploy switches the staging checkout to `main`.** The step
  removed in `daf7828` is back, on both branches. (`ci-workflows-2`, reproduced
  in git history; `cross-architecture-docs-17`)
- **Staging pushes overwrite production's compose file and Caddyfile** and
  reload the shared Caddy. (`ci-workflows-6`, `deploy-vps-4`)
- **Production rollback is unreachable** when the new container is unhealthy:
  `compose up` exits first under `set -e`. The rollback also runs `up -d` on
  every service. (`ci-workflows-1`)
- **Production deploys when orchestrator tests fail.** Confirms the open TODO
  question. (`known-findings-status-11`, `ci-workflows-3`)
- **Third-party actions on mutable tags,** including one on `@master`, in jobs
  that hold deploy and registry credentials. (`ci-workflows-8`)
- **Staging self-heal healthcheck** never resets its failure counter, so a slow
  start becomes a kill loop. (`deploy-vps-2`)
- **Backups:** the script tars a live WAL-mode SQLite database and is not
  installed where the cron line expects; the runbook's commands do not match the
  deployed layout and its restore skips the history database. (`deploy-vps-3`,
  `cross-architecture-docs-8`)

### Tests

- The riskiest factory paths have no tests: triage approval, the default
  provider path, budget wiring, the wiki/orchestrator contract.
  (`factory-tests-8`, `-10`, `cross-trust-boundaries-14`)
- Several tests encode bugs as expected behaviour or cannot fail; see the
  `wiki-tests` and `factory-tests` tables.

## Findings about PR #224

None of these block the PR; they are follow-ups to it.

- The staging deploy can still run new code on an old image when the image
  build fails, because a skipped `merge` job is accepted. (`ci-workflows-4`,
  medium)
- The hover-preview endpoint still skips page-name validation and returns 500 on
  traversal-style names. (`wiki-routes-b-12`, low)
- The CSP still trusts three whole public CDNs for scripts and any `wss:` host.
  (`wiki-routes-a-12`, low)
- Page content can forge MetaTable edit hooks. Already listed in the PR as a
  residual risk. (`wiki-parser-14`, low)

## Corrections to items already in TODO.md

- **H1, H3, M1** are fixed on `staging` (#223) but still unticked. PR #224 ticks
  them, so this file leaves them alone.
- **M2:** the cause is stated wrongly. uvicorn's proxy-header handling is on by
  default; what is missing is the trust list (`--forwarded-allow-ips`). The fix
  line is right. Also: the failure count never decays, and a lockout blocks the
  correct password too.
- **M3:** `https_only=not settings.debug` would break the auth tests and
  plain-HTTP deployments. Use a dedicated setting.
- **M4:** the proposed `rg` command breaks `--glob` (it must come before `--`).
  The local executor also runs model-written code through `run_tests`, so the
  honest framing is "development only", not "confined".
- **L3:** `_run_autofix` is broken, not just untidy: `ruff --fix src/` is
  rejected by current ruff.
- **Fixed but listed as open:** shared `httpx.AsyncClient`; hardcoded
  `repo_root`; `POSTGRES_DSN` (only an env-example line remains);
  `FACTORY_PORT` mismatch (now by design); B10's decompose bug.
- **Listed as done but not working:** F8.1 concurrency control; "Deferred
  subtask routing bug"; Milestone 14's JSON history API (shadowed by an earlier
  catch-all route).
- **Blocking I/O:** add `search_pages`, the heaviest remaining path, to the
  list.
- **Cost tracking:** partly implemented. Missing: in-sandbox model spend, PM
  spend in the budget gate, and persistence across restarts.

## Refuted or unsettled

| ID | Claim | Outcome |
|---|---|---|
| `factory-armory-14` | `artifact_path` is not confined to the playbooks directory | Refuted: the producer can only emit canonical paths. |
| `wiki-api-core-14` | TODO lists H1 and M1 as open | Refuted as a finding: PR #224 already corrects it. |
| `cross-trust-boundaries-2` | Broken resume causes a duplicate grinder run and PR | Mechanism real (see `factory-graph-nodes-8`); the claimed impact did not occur. |
| `factory-pm-18` | Reasoning-style model output breaks verdict parsing | Not settled: depends on live model behaviour. |

## Not covered

From the completeness critic. These were **not** put through verification.

- **Request-size limits (proposed medium).** No body cap exists in Caddy,
  uvicorn or the app, and public API routes parse the body before the API-key
  check. The critic measured about 270 MB peak memory from one 50 MB and one
  20 MB anonymous request. Compose sets no hard memory limit.
- **`orchestrator/e2b.Dockerfile` (proposed medium).** No unit owned it. It
  passes the GitHub token as a build argument used in a `RUN`, which Docker
  records in image history. Base image and installed tools are unpinned.
- **Retention across stores.** Checkpoint database growth was never traced;
  terminal logs are stored in the checkpoint, the page, and every revision.
  Disk-full behaviour is untested.
- **Bot tests and the storage/parser/macro tests** were only pattern-scanned.
- **`orchestrator/scripts/`:** `seed_pressure_test.py` defaults to the staging
  URL and seeds real factory tasks with no dry run.
- **Not read:** CSS, the generated Flux bootstrap, issue templates,
  `docs/custom-macros.md` against the sanitizer rules, and the watcher on Linux
  inotify.

## Suggested order of work

1. Auth startup validation, and check the live secrets (theme 1).
2. Frontmatter tolerance and PATCH validation (theme 2).
3. CI: remove the staging-checkout step from the production job; stop staging
   pushes writing production's compose file and Caddyfile; make rollback
   reachable.
4. Merge PR #224, then request-size limits.
5. Factory graph join node, loop-back and resume, with multi-subtask tests.
   Until then, treat decomposed tasks as unsupported.
6. Wiki/factory contract: nested `extra_fields`, URL-encoding, a patch endpoint
   in place of GET-then-PUT.
7. Rust engine: name normalisation, mtime short-circuit, delete/backlink
   handling, and getting the Rust tests to run.
8. Everything else, by theme.

## Appendix A: every finding that held up

IDs are `<review unit>-<n>`. *Check* is how far the finding was taken: **reproduced** = a second agent demonstrated it by running the real code; **verified** = an independent skeptic re-read the code and accepted it (in full or with the correction shown in Appendix B). *Status* is `new` unless the item extends or corrects something already in TODO.md, or concerns PR #224.


### Wiki app


**`wiki-routes-a`** (12)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 3 | high | `src/meshwiki/config.py:16` | Auth fails open on default or blank secrets: empty password logs in, session cookie is forgeable | reproduced | new |
| 1 | medium | `src/meshwiki/main.py:300` | One page with a non-string parent_task takes down every HTML route, including /login and the editor | verified | new |
| 4 | medium | `src/meshwiki/main.py:126` | Unauthenticated requests create unbounded Prometheus label series (memory growth) | verified | new |
| 5 | medium | `src/meshwiki/main.py:553` | Page name is not canonicalised: history, restore and delete operate on a different key than the file | verified | new |
| 6 | medium | `src/meshwiki/main.py:600` | Restore bypasses the task state machine and cache invalidation, and relabels whatever revision is latest | verified | new |
| 7 | medium | `src/meshwiki/main.py:486` | Hand-written or externally written frontmatter can 500 the whole wiki (aware datetimes, wrongly typed title/tags) | verified | new |
| 2 | low | `src/meshwiki/main.py:341` | Sidebar tree builder is exponential on DAGs and recursion-unbounded on chains | verified | new |
| 8 | low | `src/meshwiki/main.py:580` | Revision view renders raw file content (frontmatter shown as body) and navigation assumes contiguous revision numbers | verified | new |
| 9 | low | `src/meshwiki/main.py:513` | Unhandled input errors return 500: template parameter, oversized integers, over-long names | verified | new |
| 10 | low | `src/meshwiki/main.py:698` | Redirects interpolate the decoded page name without URL-encoding | verified | new |
| 11 | low | `src/meshwiki/main.py:658` | Diff runs a quadratic SequenceMatcher synchronously on the event loop and fetches both revisions twice | verified | new |
| 12 | low | `src/meshwiki/main.py:164` | PR #224 CSP still trusts three whole public CDNs for scripts and any wss: host | verified | in-pr-224 |

**`wiki-routes-b`** (15)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/meshwiki/main.py:1056` | PATCH /api/page/{name}/metadata can write a wrong-typed frontmatter field that makes the whole wiki return 500 | reproduced | new |
| 2 | medium | `src/meshwiki/main.py:1040` | Task status state machine is bypassable through the metadata PATCH endpoint and through a two-step save | verified | new |
| 3 | medium | `src/meshwiki/main.py:1056` | Metadata PATCH does not invalidate the page cache, so tags, sidebar and factory task lists stay stale | verified | new |
| 4 | medium | `src/meshwiki/main.py:1428` | Auth fails open on default or empty session secret and empty password | verified | new |
| 5 | medium | `src/meshwiki/main.py:1518` | Unauthenticated requests create unbounded Prometheus label series (memory growth and /metrics bloat) | verified | new |
| 6 | medium | `src/meshwiki/main.py:958` | Saving from a stale editor silently overwrites concurrent changes and can fire factory transitions | verified | new |
| 7 | medium | `src/meshwiki/main.py:1008` | Page names containing '#' or '?' are redirected (and posted) to a different page because URLs are built without percent-encoding | verified | new |
| 15 | medium | `src/meshwiki/main.py:830` | Rendering logic is duplicated across view_page, the fragment route and the HTMX save branch, and the copies have drifted | verified | new |
| 8 | low | `src/meshwiki/main.py:965` | save_page returns 500 after a half-applied save when the state machine rejects the transition | verified | new |
| 9 | low | `src/meshwiki/main.py:1399` | ws_terminal calls websocket.close() in finally after a disconnect, raising RuntimeError on every dropped viewer | verified | new |
| 10 | low | `src/meshwiki/main.py:1330` | WebSocket handlers never read from the socket and do not check Origin | verified | new |
| 11 | low | `src/meshwiki/main.py:1506` | /metrics bearer check crashes on non-ASCII tokens and reuses the factory write key for scraping | verified | new |
| 12 | low | `src/meshwiki/main.py:1017` | Hover preview endpoint skips page-name validation and returns 500 on traversal-style names | verified | in-pr-224 |
| 13 | low | `src/meshwiki/main.py:1107` | Search and Include paths read every page synchronously on the event loop | verified | known-still-open |
| 14 | low | `src/meshwiki/main.py:1474` | Readiness probe is public, mutates the filesystem and echoes exception text | verified | new |

**`wiki-parser`** (14)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/meshwiki/core/parser.py:1502` | Include macro has no expansion budget: wildcard/fan-out includes blow up factorially/exponentially and block the event loop | verified | new |
| 3 | medium | `src/meshwiki/core/parser.py:1753` | Macro regexes are quadratic on unterminated openers (NewPage worst): a 50 KB page costs ~9-13 s of blocked event loop | verified | new |
| 6 | medium | `src/meshwiki/extensions/running_clock.py:44` | Several macros raise uncaught exceptions on author-controlled input, turning the page view and preview into a 500 | verified | new |
| 2 | low | `src/meshwiki/core/parser.py:2138` | <<TableOfContents>> macro renders nothing on real pages (toc_html is never supplied by the view path) | verified | new |
| 4 | low | `src/meshwiki/core/parser.py:750` | TaskStatus crashes the page (500) on non-string frontmatter values such as pr_number: 5 | verified | new |
| 5 | low | `src/meshwiki/core/parser.py:771` | TaskStatus rework back-edge never renders when metadata comes from the graph engine | verified | new |
| 7 | low | `src/meshwiki/core/parser.py:1936` | Code-block skipping is copy-pasted 14 times in three different variants; several macros expand inside code | verified | new |
| 8 | low | `src/meshwiki/core/parser.py:1133` | Every parse pays for data most pages never use (engine.list_pages() on each parse, whole-wiki reads for Include) | verified | new |
| 9 | low | `src/meshwiki/core/parser.py:1555` | Included content is rendered without the parent's context: wrong link styling, empty macros, duplicate heading ids, one self-copy | verified | new |
| 10 | low | `src/meshwiki/core/parser.py:387` | Page names and tags are interpolated into href/query strings without HTML-attribute escaping or URL-encoding | verified | new |
| 11 | low | `src/meshwiki/core/parser.py:1885` | EpicStatus puts raw child status into Mermaid source, so one unusual status breaks the whole epic diagram | verified | new |
| 12 | low | `src/meshwiki/core/parser.py:490` | TagList output is not stashed, so tag names are re-interpreted as Markdown and the list lands inside a paragraph | verified | new |
| 13 | low | `src/meshwiki/core/parser.py:1284` | Children macro and RecentChanges ordering depend on data the fallback path does not provide | verified | new |
| 14 | low | `src/meshwiki/core/sanitize.py:70` | PR #224 sanitizer lets page content forge MetaTable edit hooks (data-page/data-field/data-editable on td) | verified | in-pr-224 |

**`wiki-storage`** (15)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | high | `src/meshwiki/core/storage.py:158` | One page with type-mismatched frontmatter (or non-UTF-8 bytes) takes down every listing, search, sidebar and the page itself | reproduced | new |
| 2 | medium | `src/meshwiki/core/storage.py:396` | Inline metadata edit of `children` writes a scalar that makes the page unreadable and breaks all listings | reproduced | new |
| 3 | medium | `src/meshwiki/core/storage.py:485` | rename_page silently overwrites an existing target page, then fails on the history update | verified | new |
| 4 | medium | `src/meshwiki/core/page_cache.py:151` | Page cache loses invalidations that arrive while a scan is running; stale list persists indefinitely | verified | new |
| 5 | medium | `src/meshwiki/core/page_cache.py:137` | Several write paths never invalidate the page cache; without the graph engine the list stays stale | verified | new |
| 8 | medium | `src/meshwiki/core/storage.py:219` | Page writes are not atomic and are not ordered with the revision insert | verified | new |
| 9 | medium | `src/meshwiki/core/revision_store.py:80` | Revision store grows without bound: full page copy per write, including no-op saves and every bot frontmatter patch | verified | new |
| 12 | medium | `src/meshwiki/core/storage.py:190` | No conflict detection on save: an editor form holding old frontmatter overwrites concurrent bot updates | verified | new |
| 6 | low | `src/meshwiki/core/storage.py:225` | Revision history is keyed by the raw name string, so space/underscore (and case) spellings of one page have separate histories and deletes leave orphans | verified | new |
| 7 | low | `src/meshwiki/core/storage.py:321` | A page name segment ending in .md creates a directory that crashes every scan | verified | new |
| 10 | low | `src/meshwiki/core/storage.py:248` | Deleting a page permanently deletes its entire history | verified | new |
| 11 | low | `src/meshwiki/core/storage.py:172` | Saving rewrites the user's frontmatter: non-ASCII escaped, keys sorted, comments and block scalars lost | verified | new |
| 13 | low | `src/meshwiki/core/storage.py:210` | Pages with `created` but no `uuid` never get a uuid | verified | new |
| 14 | low | `src/meshwiki/core/storage.py:268` | Blocking I/O in async paths: search_pages is missing from the tracked list and is the heaviest one | verified | known-worse |
| 15 | info | `src/meshwiki/core/storage.py:127` | TODO H1 (dotted page names collide) is already fixed in the tree but still listed as open | verified | known-fixed-stale-todo |

**`wiki-api-core`** (13)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/meshwiki/api/webhooks.py:63` | GitHub merge webhook can never match a task: nothing writes pr_number, and the graph lookup always throws | verified | new |
| 3 | medium | `src/meshwiki/api/pages.py:138` | Rename onto an existing page silently overwrites it and then returns 500 | verified | new |
| 7 | medium | `src/meshwiki/core/metrics.py:57` | Prometheus label cardinality is unbounded and can be driven by unauthenticated requests | verified | new |
| 8 | medium | `src/meshwiki/core/terminal_sessions.py:90` | Terminal sessions are never freed and their buffers have no size limit | verified | new |
| 10 | medium | `src/meshwiki/core/ws_manager.py:98` | 60-second leading-edge dedup drops the second update to a page, including the cache invalidation | verified | new |
| 11 | medium | `src/meshwiki/core/webhooks.py:137` | Outbound webhook dispatcher loses events on shutdown and overflow, and one dead endpoint stalls all deliveries | verified | new |
| 2 | low | `src/meshwiki/api/webhooks.py:116` | GitHub webhook endpoint is open to anyone when the secret is unset, and is not gated on factory_enabled | verified | new |
| 4 | low | `src/meshwiki/api/pages.py:156` | Page history API routes are unreachable: shadowed by the catch-all GET /pages/{name:path} | verified | new |
| 5 | low | `src/meshwiki/core/task_machine.py:142` | extra_fields can overwrite status, so any transition request can jump to any state | verified | new |
| 6 | low | `src/meshwiki/core/task_machine.py:113` | State machine gaps: 'blocked' detour skips the approval gate and the assignee check; non-task pages and odd status values are mishandled | verified | new |
| 9 | low | `src/meshwiki/api/tasks.py:184` | Terminal session lifecycle is tied to one API route, and stream handlers can wait forever | verified | new |
| 12 | low | `src/meshwiki/api/validation.py:22` | Page-name validation is not applied everywhere and accepts aliases and unusable names | verified | new |
| 13 | low | `src/meshwiki/api/webhooks.py:142` | GitHub webhook returns 500 for malformed payloads and for tasks not in review, and is not idempotent | verified | new |

**`wiki-frontend`** (20)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/meshwiki/templates/base.html:17` | hx-boost navigations to /search get the dropdown partial, replacing the whole page | verified | new |
| 2 | medium | `src/meshwiki/templates/page/edit.html:35` | Save button (boosted form) returns a degraded view, loses the toast, and fails silently on errors | verified | new |
| 3 | medium | `src/meshwiki/static/js/editor.js:16` | Unsaved-changes guard is bypassed by hx-boost and leaves a stale prompt behind | verified | new |
| 4 | medium | `src/meshwiki/templates/page/edit.html:35` | Page names and tags are put into URLs without URL-encoding; '#'/'?' in a name redirects writes to a different page | verified | new |
| 5 | medium | `src/meshwiki/templates/base.html:150` | Page live-update WebSocket is closed by any htmx swap and never reopened | verified | new |
| 6 | medium | `src/meshwiki/static/js/factory.js:481` | Body scripts re-execute on every boosted navigation and never tear down: polling loops, WebSockets and intervals accumulate | verified | new |
| 7 | medium | `src/meshwiki/static/js/metatable.js:64` | Inline MetaTable editing bypasses the task state machine and flattens typed frontmatter values | verified | new |
| 8 | medium | `src/meshwiki/templates/page/edit.html:35` | Edit form has no base-revision token: concurrent writes are overwritten and stale status triggers transitions | verified | new |
| 9 | low | `src/meshwiki/static/js/factory.js:429` | Factory activity strip: history is loaded in reversed order and the first poll floods it with every task | verified | new |
| 10 | low | `src/meshwiki/static/js/factory.js:47` | Factory dashboard downloads six full task lists every 5 seconds just to show counts | verified | new |
| 11 | low | `src/meshwiki/templates/base.html:290` | Sidebar tree expansion state is never saved, is keyed per URL, and only the nearest ancestor is opened | verified | new |
| 12 | low | `src/meshwiki/static/js/graph.js:610` | Graph view: double-click focus is pre-empted by click navigation, tooltip 'Modified' is always Unknown, WebSocket reconnect never resyncs | verified | new |
| 13 | low | `src/meshwiki/core/parser.py:1769` | NewPage macro with a parent argument always fails: it builds a slash path that the server rejects | verified | new |
| 14 | low | `src/meshwiki/templates/page/history.html:51` | History page links to a non-existent diff for revision 1 and offers Restore on the current revision | verified | new |
| 15 | low | `src/meshwiki/templates/base.html:74` | 'New Page' nav link opens the editor for the existing Home page | verified | new |
| 16 | low | `src/meshwiki/templates/base.html:12` | Third-party scripts load from CDNs with floating versions and no Subresource Integrity | verified | new |
| 17 | low | `src/meshwiki/static/js/editor.js:112` | Expired session: XHR/htmx/fetch requests receive the login page HTML and inject it in place | verified | new |
| 18 | low | `src/meshwiki/static/js/editor.js:132` | Editor: autocomplete leaves a doubled ']]' after the wiki-link button, and toolbar/paste edits do not refresh the preview | verified | new |
| 19 | low | `src/meshwiki/templates/base.html:61` | Accessibility gaps in templates and widgets | verified | new |
| 20 | info | `src/meshwiki/templates/base.html:225` | `?toast=` query parameter displays arbitrary attacker-chosen text as an app notification | verified | new |

**`wiki-tests`** (12)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | low | `src/tests/test_editor.py:10` | Running a test file on its own writes revisions into the real example data dir's .revisions.db | verified | new |
| 2 | low | `src/tests/conftest.py:26` | Reload-based fixtures leak env vars, SQLite connections and a /tmp/nonexistent directory; about 100 copies of the same boilerplate | verified | new |
| 3 | low | `src/tests/test_observability.py:326` | test_no_stdlib_logging_in_app_code can never fail | verified | new |
| 4 | low | `src/tests/test_graph_visualization.py:121` | TestEventToDict asserts only inside `if events:` and costs 3 seconds of fixed sleep | verified | new |
| 5 | low | `tests/test_graphql_introspect.py:7` | Top-level tests/ is orphaned: one file cannot be collected and neither runs in CI | verified | new |
| 6 | low | `src/tests/test_auth.py:62` | Auth blind spots: /ws/graph session check and the login lockout path have no tests; rate-limiter state is global | verified | new |
| 7 | low | `src/tests/test_config.py:10` | Tests are not hermetic against a local .env or exported MESHWIKI_* variables | verified | new |
| 8 | low | `src/e2e/test_navigation.py:19` | E2E test_search_no_results cannot fail and burns a fixed 2s | verified | new |
| 9 | low | `src/e2e/conftest.py:77` | E2E server fixture hides startup failures and has a port race | verified | new |
| 10 | low | `src/tests/test_graph_search.py:90` | Many assertions are too weak to detect the regression they name | verified | new |
| 11 | low | `src/tests/test_webhooks.py:151` | Webhook _send tests use AsyncMock responses, so raise_for_status is never actually checked on the success path | verified | new |
| 12 | info | `TODO.md:462` | TODO still lists H1, H3 and M1 as open although staging contains the fixes and regression tests | verified | known-fixed-stale-todo |

### Agent factory


**`factory-grinder`** (14)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/agents/grinder_agent.py:1163` | MiniMax/Kilo API key is given to the sandboxed agent but never scrubbed from the terminal stream or the persisted log | verified | new |
| 2 | medium | `orchestrator/factory/agents/grinder_agent.py:1366` | PR URL is scraped as the last github.com/.../pull/N string in raw TUI output, with no check that it is this task's PR | verified | new |
| 3 | medium | `orchestrator/factory/agents/grinder_agent.py:1263` | Shallow single-branch clone makes the rework and resume branch instructions fail | verified | new |
| 4 | medium | `orchestrator/factory/agents/grinder_agent.py:390` | Local grinder's path confinement does not contain the agent: .git writes and run_tests give command execution on the orchestrator host | verified | known-worse |
| 5 | medium | `orchestrator/factory/agents/grinder_agent.py:1400` | Each attempt's 25k-character terminal log is appended to the task page and fed back into the next prompt, which is passed as one shell argument | verified | new |
| 6 | medium | `orchestrator/factory/agents/grinder_agent.py:1268` | Bootstrap commands raise on non-zero exit, so the clone check is dead code and unscrubbed stderr reaches the log | verified | new |
| 7 | low | `orchestrator/factory/agents/grinder_agent.py:1237` | One awaited HTTP POST per PTY chunk sits on the E2B event path; a slow wiki stalls the stream and can lose the final output | verified | new |
| 8 | low | `orchestrator/factory/agents/grinder_agent.py:1192` | Sandbox lifetime, bootstrap timeouts and cleanup do not line up; sandboxes can leak or die mid-run | verified | new |
| 9 | low | `orchestrator/factory/agents/grinder_agent.py:499` | Local grinder runs blocking subprocesses on the event loop and shares one working tree between parallel subtasks | verified | new |
| 10 | low | `orchestrator/factory/agents/grinder_agent.py:1525` | Local grinder loop has no error handling, a 30 s non-streaming timeout, and unbounded tool output | verified | new |
| 11 | low | `orchestrator/factory/agents/grinder_agent.py:1258` | Task frontmatter `repo:` and the base branch are interpolated unquoted into sandbox shell commands, and any repo the token can reach is accepted | verified | new |
| 12 | low | `orchestrator/factory/agents/grinder_agent.py:1444` | The 'minimax' local provider builds an Anthropic client against /v1 and routes through the Anthropic circuit breaker | verified | new |
| 13 | low | `orchestrator/factory/agents/grinder_agent.py:1030` | Persisted terminal log: incomplete ANSI stripping and an escapable code fence | verified | new |
| 14 | low | `orchestrator/factory/agents/grinder_agent.py:1384` | E2B grinder cost excludes all model spend, so the daily budget does not constrain the main cost | verified | known-still-open |

**`factory-pm`** (19)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/agents/pm_agent.py:1053` | Triage pass can approve (and auto-merge) a PR from the first 500 diff lines with no tools | verified | new |
| 3 | medium | `orchestrator/factory/agents/grinder_agent.py:1366` | PR URL is scraped from raw terminal output: last match wins, any repo, and wraps truncate the number | verified | new |
| 4 | medium | `orchestrator/factory/agents/pm_agent.py:1176` | PM diagnose retry path cannot work: revised description never reaches the grinder and the retry fails fast | verified | new |
| 6 | medium | `orchestrator/factory/agents/pm_agent.py:511` | Default (MiniMax/OpenRouter) path has no retry, a 60 s non-streaming timeout and no secondary provider | verified | new |
| 8 | medium | `orchestrator/factory/agents/pm_agent.py:1169` | A review that ends without a decision tool call becomes 'changes_requested' with no feedback, which fails the subtask | verified | new |
| 9 | medium | `orchestrator/factory/agents/pm_agent.py:855` | github_read_file can never work during decomposition although the system prompt makes it mandatory | verified | new |
| 2 | low | `orchestrator/factory/agents/pm_agent.py:926` | Grinder-controlled terminal log is fed to the reviewer as 'Acceptance Criteria' | verified | new |
| 5 | low | `orchestrator/factory/agents/pm_agent.py:618` | Anthropic path only falls back on billing errors; overload exhaustion, 5xx, 429 and timeouts raise | verified | new |
| 7 | low | `orchestrator/factory/agents/pm_agent.py:362` | Model tool-call output is parsed without validation; malformed or truncated output crashes the loop | verified | new |
| 10 | low | `orchestrator/factory/agents/pm_agent.py:706` | LLM-chosen subtask page name is not validated and pages are written with an overwriting PUT | verified | new |
| 11 | low | `orchestrator/factory/agents/pm_agent.py:1140` | github_read_file path is model-controlled and can escape the repository in the API URL | verified | new |
| 12 | low | `orchestrator/factory/agents/grinder_agent.py:1163` | Only the GitHub token is scrubbed from grinder output; MiniMax key is unprotected and ANSI codes defeat the scrubber | verified | new |
| 13 | low | `orchestrator/factory/agents/pm_agent.py:1093` | Conversation history mixes adapter blocks and SDK blocks, so a mid-loop provider switch back to Anthropic crashes | verified | new |
| 14 | low | `orchestrator/factory/agents/pm_agent.py:1089` | PM cost is priced at the configured model, silently zero for unknown models, and dropped on failure | verified | new |
| 15 | low | `orchestrator/factory/agents/grinder_agent.py:1525` | safe_messages_create applies the Anthropic breaker to a MiniMax-pointed client | verified | new |
| 16 | low | `orchestrator/factory/agents/grinder_agent.py:434` | Local grinder runs parallel subtasks in one shared working tree | verified | new |
| 17 | low | `orchestrator/factory/agents/grinder_agent.py:1336` | Prompts have no size bounds; the grinder prompt is passed as one shell argument | verified | new |
| 19 | low | `orchestrator/factory/agents/grinder_agent.py:1263` | Task frontmatter 'repo' flows unvalidated into a sandbox shell command and the GitHub client | verified | new |
| 20 | low | `orchestrator/tests/test_pm_agent.py:341` | Tests skip the riskiest paths: triage approval, fallback routing, adapter error cases | verified | new |

**`factory-graph-nodes`** (17)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/graph.py:111` | Fan-in never fires: epics whose subtasks are all approved in the same superstep are never finalized | reproduced | new |
| 2 | medium | `orchestrator/factory/graph.py:44` | Subtasks deferred by the concurrency cap or file-conflict serialization are never dispatched | reproduced | new |
| 3 | medium | `orchestrator/factory/nodes/grind.py:112` | active_grinders is add-only and populated after the grinder finishes, so the cap is wrong and re-dispatch is blocked | verified | new |
| 4 | medium | `orchestrator/factory/nodes/escalate.py:39` | escalate_node always decides 'abandon' and leaves the parent task stuck; retry and redecompose are unreachable | verified | new |
| 5 | medium | `orchestrator/factory/nodes/grind.py:54` | pm_diagnose retry never re-runs the grinder: grind_node fails fast on attempt > 0 without review_feedback | verified | new |
| 6 | medium | `orchestrator/factory/nodes/pm_review.py:69` | A failed PM review (or any failed subtask) is treated as approval and the parent is marked done | reproduced | new |
| 7 | medium | `orchestrator/factory/nodes/pm_review.py:172` | Subtask is recorded as 'merged' even when the PR was not merged; merge_check is dead code | reproduced | new |
| 8 | medium | `orchestrator/factory/graph.py:274` | Resuming an interrupted thread with a dict input restarts the graph at task_intake; human approval never reaches human_review_code | reproduced | new |
| 9 | medium | `orchestrator/factory/graph.py:256` | Human rejection loops back to pm_review of an arbitrary already-approved subtask and discards the feedback | verified | new |
| 11 | medium | `orchestrator/factory/nodes/assign.py:44` | route_grinders returning an empty list ends the run silently with no wiki status change | verified | new |
| 12 | medium | `orchestrator/factory/state.py:11` | Subtasks and loop counters from a previous run persist when a task is re-run on the same thread | verified | new |
| 13 | medium | `orchestrator/factory/nodes/decompose.py:146` | Partially created decompositions are dispatched and later finalized as a complete epic | verified | new |
| 17 | medium | `orchestrator/tests/test_pipeline.py:128` | Tests cover routers with hand-built state and a single-subtask pipeline, so the multi-subtask and resume failures above pass CI | verified | new |
| 10 | low | `orchestrator/factory/graph.py:133` | Post-merge armory validation rework loop ignores max_attempts and has no practical bound | verified | new |
| 14 | low | `orchestrator/factory/nodes/decompose.py:47` | Subtask page frontmatter is built by string interpolation of PM-generated titles | verified | new |
| 15 | low | `orchestrator/factory/nodes/decompose.py:167` | Several node transitions are illegal in the wiki task state machine and fail silently, leaving wiki and graph state divergent | verified | new |
| 16 | low | `orchestrator/factory/nodes/grind.py:82` | Daily budget only counts grinder spend; PM decompose, review and diagnose costs are not recorded | verified | new |

**`factory-server`** (15)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/webhook_server.py:776` | Approval and rework webhooks restart the graph from START instead of resuming the interrupted thread | verified | new |
| 2 | medium | `orchestrator/factory/webhook_server.py:264` | Restart pushes every task waiting for human code review past the interrupt as 'rejected' | verified | new |
| 3 | medium | `orchestrator/factory/webhook_server.py:215` | Startup auto-fails in-progress subtask pages because they have no checkpoint of their own | verified | new |
| 4 | medium | `orchestrator/factory/webhook_server.py:757` | Re-assigned task reuses its old thread and inherits stale state; replayed task.assigned re-runs a finished task | verified | new |
| 7 | medium | `orchestrator/factory/integrations/meshwiki_client.py:84` | Unencoded page names make the MeshWiki client read and overwrite the wrong page | verified | known-worse |
| 8 | medium | `orchestrator/factory/integrations/meshwiki_client.py:179` | Heartbeat and append helpers do GET-then-PUT of the whole page, which can revert concurrent changes and corrupt frontmatter | verified | new |
| 5 | low | `orchestrator/factory/hbr.py:128` | Daily budget gate misses most spend and resets on every restart | verified | new |
| 6 | low | `orchestrator/factory/integrations/github_client.py:200` | github_read_file path is interpolated into the API URL, letting the PM agent read other repositories with the factory token | verified | new |
| 9 | low | `orchestrator/factory/integrations/minimax_client.py:78` | MiniMax quota of exactly 0 is read as 'unknown' and dispatch continues | verified | new |
| 10 | low | `orchestrator/factory/webhook_server.py:618` | /tasks, /status and /hbr/status are unauthenticated; /tasks is a read proxy using the orchestrator's wiki API key | verified | new |
| 11 | low | `orchestrator/factory/webhook_server.py:69` | Queued resumes are lost on restart and are replayed into a closing checkpointer during shutdown | verified | new |
| 12 | low | `orchestrator/factory/webhook_server.py:713` | Webhook handler returns 500 on malformed input and has no replay window | verified | new |
| 13 | low | `orchestrator/factory/webhook_server.py:198` | One unreadable checkpoint or wiki error during startup resume aborts the whole service | verified | new |
| 14 | low | `orchestrator/factory/integrations/github_client.py:245` | GitHub list calls fetch only the first 100 results, so large PRs partly skip validation | verified | new |
| 15 | low | `orchestrator/Dockerfile:5` | Image runs as root and copies the whole build context; upper-case FACTORY_LOG_LEVEL is likely to break startup | verified | new |

**`factory-bots`** (14)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/bots/bookkeeper.py:291` | Bookkeeper 'merged PR' reconciliation is dead code: it queries a status that does not exist | verified | new |
| 2 | medium | `orchestrator/factory/bots/bookkeeper.py:208` | Heartbeat cannot detect hung graph tasks, and stale-heartbeat rule fails healthy tasks after outages, retries and human-review pauses | verified | new |
| 4 | medium | `orchestrator/factory/bots/worker_heartbeat.py:104` | Bots update pages with GET-then-PUT of the whole page; the 60s heartbeat can revert status transitions and drop concurrent appends | verified | new |
| 5 | medium | `orchestrator/factory/bots/base.py:213` | Per-tick page rewrites grow the revision DB without bound and churn the watcher/graph | verified | new |
| 6 | medium | `orchestrator/factory/bots/base.py:147` | BaseBot._loop leaks one pending asyncio task per tick | verified | new |
| 7 | medium | `orchestrator/factory/bots/insight.py:314` | Unquoted frontmatter values written by bots break YAML and strip page metadata | verified | new |
| 9 | medium | `orchestrator/factory/bots/ci_fixer.py:195` | CI-fixer can spam PR comments, re-analyses the same failure, and feeds the LLM the wrong part of the job log | verified | new |
| 10 | medium | `orchestrator/factory/bots/base.py:95` | Every restart re-runs all bots immediately: weekly Insight and daily Purgatory fire on each deploy; Insight auto-files self-dispatching tasks with weak dedup | verified | new |
| 3 | low | `orchestrator/factory/bots/bookkeeper.py:144` | Bookkeeper Rule 1 auto-fails human-owned tasks, and is timezone-fragile | verified | new |
| 8 | low | `orchestrator/factory/bots/stale_pr_bot.py:193` | Stale-PR bot: both fix attempts fire 5 minutes apart, fix tasks open new factory PRs, and fix PRs recurse | verified | new |
| 11 | low | `orchestrator/factory/bots/terminal_review.py:167` | Terminal-review rescans every completed task each hour and can stall permanently on failing pages | verified | new |
| 12 | low | `orchestrator/factory/bots/scheduler.py:190` | Scheduler: a failing dispatch blocks the head of the queue, and a quota-API error aborts the tick without updating status | verified | new |
| 13 | low | `orchestrator/factory/bots/stale_pr_bot.py:109` | GitHub usage: no pagination, no rate-limit handling, steady polling of every review PR | verified | new |
| 14 | low | `orchestrator/factory/bots/bookkeeper.py:166` | Bot tests encode the bugs rather than the contract | verified | new |

**`factory-armory`** (13)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/bots/class_gap_researcher.py:651` | class-gap-researcher: generic suggestions are never deduplicated against open tasks or the armory | verified | new |
| 2 | medium | `orchestrator/factory/bots/armory_research.py:91` | Task-page frontmatter is built by string interpolation: quotes break the page, newlines inject trusted keys (repo, artifact_type, parent_task) | verified | new |
| 3 | medium | `orchestrator/factory/nodes/validate_armory.py:319` | Playbook/toolspec validator passes files it cannot see: no frontmatter, no checks, missing patch, modified hunks, null values | verified | new |
| 4 | medium | `orchestrator/factory/nodes/validate_armory.py:275` | Forbidden-import gate is trivially bypassed and is not applied to .py files in playbook/toolspec/wordlist PRs | verified | new |
| 5 | medium | `orchestrator/factory/bots/armory_research.py:228` | armory-research: per-run cap is applied before the existing-page skip, so leftover pages starve all later findings | verified | new |
| 7 | medium | `orchestrator/factory/bots/class_gap_researcher.py:556` | Armory survey silently truncates at 1,000 playbooks (GitHub Contents API directory limit) | verified | new |
| 6 | low | `orchestrator/factory/bots/class_gap_researcher.py:491` | class-gap-researcher overwrites an existing task page via PUT when the same name recurs (same day, or same date a year later) | verified | new |
| 8 | low | `orchestrator/factory/bots/class_gap_researcher.py:622` | class-gap open-task dedup depends on page-name spelling and a partial status list, and hides list_tasks failures | verified | new |
| 9 | low | `orchestrator/factory/nodes/validate_armory.py:137` | Post-merge armory gate cannot undo a merge and fails open when PR files cannot be fetched | verified | new |
| 10 | low | `orchestrator/factory/bots/class_gap_researcher.py:833` | LLM suggestion parsing fails silently and unvalidated field types crash the run | verified | new |
| 11 | low | `orchestrator/factory/bots/armory_research.py:232` | armory-research: one malformed finding aborts the whole run; rendering happens outside the error handling | verified | new |
| 12 | low | `orchestrator/factory/bots/armory_research.py:303` | armory-research subprocess: child not reaped or fully terminated on timeout, unbounded stdout buffering | verified | new |
| 13 | info | `orchestrator/factory/bots/armory_research.py:278` | Nothing prevents both gap producers running together, and their dedup schemes cannot see each other's tasks | verified | known-still-open |

**`factory-tests`** (14)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | high | `orchestrator/tests/test_pipeline.py:128` | No multi-subtask test through the real graph: parallel PM approvals both route to END and the task is never finalized | reproduced | new |
| 2 | medium | `orchestrator/tests/test_concurrency.py:181` | Deferred subtasks are never dispatched and active_grinders is never cleared; tests cover only first-round selection and assert the wrong bookkeeping | verified | known-still-open |
| 3 | medium | `orchestrator/tests/test_nodes.py:1152` | escalate_node tests hand-seed failed_subtask_ids, which nothing in the live graph ever writes; escalation always abandons | verified | new |
| 4 | medium | `orchestrator/tests/test_pipeline.py:194` | Pipeline tests resume with aupdate_state(as_node)+ainvoke(None); the webhook resumes with ainvoke({...}), which restarts the graph and does not approve or rework anything | verified | new |
| 7 | medium | `orchestrator/tests/test_nodes.py:1233` | merge_check tests use states the live graph never produces; an approved but unmerged PR still ends with the parent marked done | verified | new |
| 8 | medium | `orchestrator/tests/test_pm_agent.py:341` | The review paths that run by default in production are not tested: triage fast-path approval and the OpenAI-compatible provider path | verified | new |
| 10 | medium | `orchestrator/tests/test_hbr.py:1` | Budget enforcement is tested only on an isolated HBRManager; the wiring that feeds and enforces it is untested | verified | new |
| 5 | low | `orchestrator/tests/test_webhook_server.py:77` | Webhook event dispatch has no route-level tests; a task.assigned delivered again after completion re-runs the whole pipeline | verified | new |
| 6 | low | `orchestrator/tests/test_webhook_server.py:92` | test_webhook_invalid_hmac sends the wrong header name, so the signature comparison is never tested | verified | new |
| 9 | low | `orchestrator/tests/test_grinder_agent.py:1471` | Sandbox failure and cleanup paths are untested; sbx.kill() is mocked but never asserted | verified | new |
| 11 | low | `orchestrator/tests/__init__.py:1` | No conftest: process-wide singletons and environment leak between tests, making results order- and environment-dependent | verified | new |
| 12 | low | `orchestrator/tests/test_bots.py:86` | test_bots.py opens real HTTP connections to the MeshWiki URL and can write bot status pages into a running wiki | verified | new |
| 13 | low | `orchestrator/test_grinder_live.py:98` | orchestrator/test_grinder_live.py is collected by a bare `pytest` and runs a live sandbox call at import time | verified | new |
| 14 | low | `orchestrator/tests/test_nodes.py:1040` | Finalize tests pin 'completed' for runs that finalized nothing, and the armory contract tests are skipped wherever the sibling repo is absent | verified | new |

### Rust graph engine


**`rust-graph`** (9)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `graph-core/src/graph.rs:191` | Deleting a page drops its incoming edges, so backlinks are lost until restart (live graph diverges from rebuild) | verified | new |
| 2 | medium | `graph-core/src/graph.rs:466` | Graph page names are raw file stems (underscores) while the wiki's page names use spaces; [[Two Words]] links attach to a stub instead of the real page | verified | new |
| 3 | medium | `graph-core/src/models.rs:62` | Stub nodes are stamped last_modified = now, so the watcher's mtime short-circuit ignores a real file that later appears with an older mtime | verified | new |
| 6 | medium | `graph-core/Cargo.toml:13` | The Rust unit tests cannot be linked and are not run in CI | verified | new |
| 4 | low | `graph-core/src/graph.rs:397` | build_from_directory clears the graph first and aborts on any directory error, leaving an empty graph (and rebuild() then leaves the watcher stopped) | verified | new |
| 5 | low | `graph-core/src/graph.rs:77` | Stub nodes are never garbage-collected and leak through get_page/get_metadata | verified | new |
| 8 | low | `graph-core/Cargo.toml:17` | serde_yaml 0.9 is a deprecated, unmaintained dependency | verified | new |
| 9 | low | `graph-core/src/graph.rs:469` | Non-UTF-8 file names all collapse to one page named "unknown" during a full scan | verified | new |
| 7 | info | `graph-core/src/events.rs:115` | EventQueue is unbounded and silently drops events on a poisoned lock | verified | new |

**`rust-bindings`** (10)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `graph-core/src/watcher.rs:225` | Watcher mtime short-circuit compares against link-stub creation time, so newly created/renamed pages are never indexed | reproduced | new |
| 2 | medium | `graph-core/src/query.rs:58` | Regex filter is recompiled for every page while holding the GIL and graph lock: one MetaTable can freeze the server for minutes | reproduced | new |
| 3 | medium | `graph-core/src/graph.rs:466` | Engine page names keep filename underscores while link targets keep spaces: links to multi-word pages are stubs, existing pages report as missing | verified | new |
| 4 | medium | `graph-core/src/watcher.rs:312` | Deleting a page drops all incoming edges; backlinks do not come back when the page is recreated | verified | new |
| 6 | medium | `graph-core/src/watcher.rs:101` | Watcher start-up and runtime errors are swallowed; start_watching() reports success while the thread has already exited | verified | new |
| 7 | medium | `graph-core/Cargo.toml:13` | Rust unit tests cannot be linked and are never run in CI (68 #[test] functions, including the mtime-storm regression test) | verified | new |
| 5 | low | `graph-core/src/parser.rs:141` | extract_wiki_links is quadratic on unclosed '[[' - one page can stall startup rebuild for minutes | verified | new |
| 8 | low | `graph-core/src/parser.rs:45` | Frontmatter divergence, concrete cases: non-mapping '---' blocks swallow links; YAML 1.2 vs 1.1 scalar values differ from Python | verified | known-still-open |
| 9 | low | `graph-core/src/watcher.rs:166` | Directory-level and symlink cases are not handled by the watcher/scanner | verified | new |
| 10 | low | `graph-core/src/lib.rs:213` | Stub nodes leak through get_page/get_metadata and are never garbage-collected | verified | new |

### Deploy and CI


**`ci-workflows`** (16)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `.github/workflows/ci.yml:628` | Production rollback is unreachable when the new web container is unhealthy | verified | new |
| 2 | medium | `.github/workflows/ci.yml:549` | Production deploy again switches the staging checkout to main (regression of daf7828) | reproduced | new |
| 4 | medium | `.github/workflows/ci.yml:385` | Staging deploy can run new code against an old image when the build fails or Rust tests fail (also in PR #224) | verified | in-pr-224 |
| 6 | medium | `.github/workflows/ci.yml:413` | Staging pushes overwrite the production compose file and Caddyfile and reload the live Caddy | verified | new |
| 8 | medium | `.github/workflows/ci.yml:284` | Third-party actions are referenced by mutable tags, including trivy-action@master, in jobs holding deploy and registry credentials | verified | new |
| 3 | low | `.github/workflows/ci.yml:523` | Production deploys when orchestrator tests fail | verified | known-still-open |
| 5 | low | `.github/workflows/ci.yml:391` | Staging deploy failures are invisible: job-level continue-on-error plus `\|\| true` on every compose command | verified | new |
| 7 | low | `.github/workflows/ci.yml:481` | Staging deploy ships the branch tip, not the commit that passed tests | verified | new |
| 9 | low | `.github/workflows/ci.yml:282` | A Trivy or SARIF-upload failure blocks the production deploy after :latest is already pushed | verified | new |
| 10 | low | `.github/workflows/ci.yml:405` | SSH host key is trusted on first use on every run; secrets are streamed over that connection | verified | new |
| 11 | low | `.github/workflows/ci.yml:351` | "CI passed" stays green when the `changes` job fails, and lint/E2E are not part of it | verified | new |
| 12 | low | `.github/dependabot.yml:4` | Dependabot's pip entry points at /src, which has no manifest; the orchestrator and Docker images are not covered | verified | new |
| 13 | low | `.github/workflows/pre-merge-check.yml:15` | pre-merge-check checks out the PR branch name instead of the PR commit, and the up-to-date check cannot work | verified | new |
| 14 | low | `.github/workflows/ci.yml:524` | No ordering between concurrent runs: an older commit can be deployed to production or tagged :latest/:staging last | verified | new |
| 15 | low | `.github/workflows/ci.yml:1` | Workflows have no top-level permissions block | verified | new |
| 16 | low | `.github/workflows/ci.yml:85` | Rust unit tests and orchestrator lint never run in CI | verified | new |

**`deploy-vps`** (15)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `deploy/vps/staging.env.example:13` | Env examples ship an empty MESHWIKI_SESSION_SECRET, which the app accepts, making session cookies forgeable; CI auto-installs the staging example | verified | new |
| 2 | medium | `deploy/vps/docker-compose.prod.yml:62` | Staging self-heal healthcheck never resets its failure counter after killing PID 1, so a slow start becomes a kill loop | verified | new |
| 3 | medium | `scripts/backup.sh:33` | Backup script copies a live WAL-mode SQLite DB with tar, can abort on changing files, is not deployed where the cron line expects it, and the documented restore loses history and ownership | verified | new |
| 4 | medium | `.github/workflows/ci.yml:414` | Every staging push overwrites production's compose file and Caddyfile and reloads Caddy (cross-unit: ci.yml) | verified | new |
| 9 | medium | `scripts/abandon_dropped_target_tasks.py:50` | abandon_dropped_target_tasks.py blocks every open playbook task if Molly returns an empty/renamed list, and its blocked_reason is silently dropped | verified | new |
| 5 | low | `orchestrator/Dockerfile:5` | Orchestrator image runs as root and is built with `COPY . .` and no .dockerignore | verified | new |
| 6 | low | `deploy/vps/docker-compose.prod.yml:42` | Compose has no hard resource limits or container hardening; staging gets the same OOM protection as prod; all services share one network | verified | new |
| 7 | low | `deploy/vps/Caddyfile:8` | Caddy caches /static/* for 24h but templates reference unversioned asset URLs | verified | new |
| 8 | low | `.github/workflows/ci.yml:625` | Failed prod deploy rolls back the container but leaves .env pinned to the failed version; rollback also runs `up -d` on every service (cross-unit: ci.yml) | verified | new |
| 10 | low | `deploy/vps/.env.example:23` | Env example files encourage putting unused LLM keys in the wiki container and omit the GitHub webhook secret, which fails open | verified | new |
| 11 | low | `scripts/remove-example-data.sh:12` | remove-example-data.sh targets the wrong directory by default and deletes by filename only, including a user's edited Home page | verified | new |
| 12 | low | `scripts/validate-branch.sh:11` | validate-branch.sh has an inverted up-to-date check and leaks its server; test-pr.sh installs from the wrong directory and hides failures | verified | new |
| 13 | low | `scripts/bootstrap.sh:20` | bootstrap.sh is meant to be sourced but runs `set -euo pipefail`, changing the caller's shell | verified | new |
| 14 | low | `Dockerfile:16` | Builds are not reproducible beyond the missing Python lockfiles: floating base images, unpinned rustup/maturin, Cargo.lock gitignored, no --locked | verified | known-worse |
| 15 | info | `python/molly/tools/base.py:1` | Files belonging to another project (python/molly, playbook scripts) live in this repo untested and unpackaged | verified | new |

**`deploy-k8s-infra`** (15)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | low | `deploy/apps/meshwiki/deployment.yaml:22` | K8s Deployment runs the wiki with authentication off and the default session secret | verified | new |
| 2 | low | `deploy/apps/meshwiki/deployment.yaml:7` | RollingUpdate strategy with a ReadWriteOnce PVC and a single-writer data directory | verified | new |
| 3 | low | `deploy/apps/meshwiki/deployment.yaml:37` | Probes hit the page index instead of the dedicated health endpoints | verified | new |
| 5 | low | `deploy/apps/meshwiki/deployment.yaml:15` | No pod or container securityContext; PVC writability depends on local-path defaults | verified | new |
| 6 | low | `deploy/apps/meshwiki/deployment.yaml:18` | Image is a local-only mutable tag, so the Flux GitOps path cannot actually deploy or update the wiki | verified | new |
| 7 | low | `deploy/apps/test-app/virtualservice.yaml:8` | Test app is served on hello.localhost but every doc says test.localhost; it is also deployed everywhere Flux runs | verified | new |
| 8 | low | `deploy/flux/flux-system/gotk-sync.yaml:10` | Committed Flux bootstrap is a 10k-line generated snapshot pinned to the owner's repo and the main branch | verified | new |
| 9 | low | `infra/local/istio.tf:142` | Terraform kubectl provisioners run against whatever kube context is current, including on destroy | verified | new |
| 10 | low | `infra/local/istio.tf:81` | k3d load-balancer nginx.conf is overwritten by hand with hard-coded node names and no triggers | verified | new |
| 11 | low | `infra/local/main.tf:5` | Changing any cluster variable recreates the cluster but leaves every dependent resource stale in state | verified | new |
| 12 | low | `infra/local/variables.tf:59` | Rancher is installed with bootstrap password 'admin' on ports published to all host interfaces | verified | new |
| 13 | low | `infra/local/outputs.tf:11` | Terraform outputs and README state a fixed API endpoint that k3d does not use | verified | new |
| 14 | low | `infra/local/versions.tf:4` | Unpinned providers, an unused provider, and component versions outside each other's support range | verified | new |
| 4 | info | `deploy/apps/meshwiki/deployment.yaml:30` | 256Mi memory limit is half of what the VPS deployment reserves after a real OOM kill | verified | new |
| 15 | info | `deploy/apps/meshwiki/deployment.yaml:1` | K8s and Terraform scaffolding is documented as a working deployment path but has drifted from the real one | verified | new |

### Cross-cutting


**`cross-trust-boundaries`** (13)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `orchestrator/factory/integrations/meshwiki_client.py:126` | Orchestrator sends transition extra fields flattened; wiki expects nested extra_fields and silently drops them (pr_url, branch, cost_usd, factory_note never saved) | reproduced | new |
| 4 | medium | `src/meshwiki/api/webhooks.py:74` | GitHub webhook task lookup: graph-engine path always throws, pr_number compared as string only, not scoped to repo, and nothing writes pr_number | verified | new |
| 5 | medium | `orchestrator/factory/integrations/meshwiki_client.py:179` | Orchestrator updates pages by GET + full PUT, racing with state transitions and reverting status without firing webhooks | verified | new |
| 8 | medium | `orchestrator/factory/nodes/decompose.py:143` | PM-chosen subtask page names are written with create-or-overwrite PUT; untrusted task text can make the factory overwrite arbitrary wiki pages | verified | new |
| 10 | medium | `orchestrator/factory/bots/bookkeeper.py:291` | Bookkeeper queries a status the wiki state machine does not have ('in_review') | verified | new |
| 3 | low | `src/meshwiki/api/webhooks.py:117` | Wiki GitHub webhook accepts unsigned requests when the secret is empty and is exempt from wiki login; anyone can drive a review task to done | verified | new |
| 6 | low | `src/meshwiki/core/task_machine.py:142` | State machine can be bypassed through the API: extra_fields may overwrite status, and PUT/PATCH change status with no validation or webhook | verified | new |
| 7 | low | `src/meshwiki/api/pages.py:156` | JSON API history endpoints are unreachable: shadowed by the greedy GET /pages/{name:path} route | verified | known-fixed-stale-todo |
| 9 | low | `orchestrator/factory/integrations/meshwiki_client.py:36` | Client-side frontmatter patching writes raw unescaped values and only recognises LF '---' blocks | verified | new |
| 11 | low | `src/meshwiki/api/webhooks.py:167` | Webhooks in both directions have no replay protection or idempotency key; wiki GitHub endpoint returns 500 on redelivery and can strand a task in 'merged' | verified | new |
| 12 | low | `orchestrator/factory/integrations/meshwiki_client.py:98` | Unencoded page names in client URLs are worse than the tracked TODO item: '?' and '#' redirect writes to a different page, and reserved suffixes collide with routes | verified | known-worse |
| 13 | low | `src/meshwiki/api/tasks.py:207` | Wiki-side failures after a transition are not handled: InvalidTransitionError from the UI save path and terminal-session side effects for non-task names | verified | new |
| 14 | low | `orchestrator/tests/test_meshwiki_client.py:91` | No test exercises the wiki/orchestrator contract end to end; each side's tests mock the other with the wrong shape | verified | new |

**`cross-architecture-docs`** (18)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/meshwiki/config.py:17` | Web app has no startup config validation: empty or default session secret and empty password are accepted, and the shipped env examples leave the secret empty | verified | new |
| 2 | medium | `src/meshwiki/main.py:461` | Factory bots create slash-named pages that the wiki UI rejects, so they cannot be viewed, edited or deleted in the browser | verified | new |
| 3 | medium | `src/meshwiki/main.py:1040` | Inline metadata edit bypasses the task state machine that the editor save path enforces | verified | new |
| 8 | medium | `docs/runbook.md:53` | Runbook commands do not match the deployed layout, and its restore procedure skips the page-history database | verified | new |
| 17 | medium | `.github/workflows/ci.yml` | Production deploy job switches the shared staging checkout to main | verified | new |
| 4 | low | `src/meshwiki/api/pages.py:157` | Revision-history JSON API is unreachable because an earlier catch-all route shadows it | verified | new |
| 5 | low | `src/meshwiki/core/page_cache.py:137` | Page-list cache is only invalidated by UI save/delete or the Rust file watcher, and an invalidation that lands during a rebuild is lost | verified | new |
| 6 | low | `pyproject.toml:36` | Packaging config builds an empty wheel; `pip install -e .` does not make `meshwiki` importable | verified | new |
| 7 | low | `README.md:258` | Documented self-hosting steps cannot work: one compose file bundles prod, staging and two orchestrators, and the Caddyfile hardcodes the owner's other domains | verified | new |
| 9 | low | `src/meshwiki/extensions/running_clock.py:46` | <<RunningClock timezone="...">> raises an uncaught ValueError and returns 500 for the page | verified | new |
| 10 | low | `docs/domains/factory.md:408` | Domain docs that CLAUDE.md tells subagents to read describe a system that no longer exists, including a wrong security claim | verified | new |
| 11 | low | `docs/getting-started.md:133` | README, CLAUDE.md, CONTRIBUTING and getting-started give conflicting commands, paths, counts and branch workflow | verified | new |
| 12 | low | `src/meshwiki/config.py:11` | Data directory and .env are resolved against the current working directory, so the documented dev flow edits tracked example pages and the helper script targets a different directory | verified | new |
| 13 | low | `.github/dependabot.yml:4` | Dependency and tooling hygiene: Dependabot points at a directory with no manifest, the orchestrator is never linted or dependency-scanned, and declared deps do not match imports | verified | new |
| 14 | low | `python/molly/tools/graphql_introspect.py:1` | Stray and dead code: files from another project, tests CI never runs, unused modules and functions | verified | new |
| 15 | low | `src/meshwiki/core/parser.py:289` | God modules with copy-pasted logic and a core-to-main import inversion; concrete refactoring seams | verified | new |
| 16 | low | `deploy/vps/.env.example:22` | Configuration sprawl and stale examples: settings read outside Settings, dead variables in env examples, inconsistent factory gating | verified | new |
| 18 | info | `TODO.md:462` | TODO review items that the code shows as already fixed are still listed as open | verified | known-fixed-stale-todo |

**`known-findings-status`** (13)

| ID | Severity | Location | Finding | Check | Status |
|---|---|---|---|---|---|
| 2 | medium | `orchestrator/factory/nodes/assign.py:34` | 'Enforce concurrency caps at dispatch' is implemented but the counter is wrong: active_grinders counts finished grinders and is never reset, so deferred and retried subtasks are never dispatched | verified | known-worse |
| 11 | medium | `.github/workflows/ci.yml:518` | Production deploy does not have the test-orchestrator gate, and orchestrator rollback/versioning is half done | verified | known-worse |
| 3 | low | `TODO.md:498` | M2 description is inaccurate (proxy headers are already on) and the limiter has more wrong with it than the global lockout | verified | known-still-open |
| 4 | low | `TODO.md:507` | M3 proposed fix (`https_only=not settings.debug`) would break the auth test suite and plain-HTTP deployments | verified | known-still-open |
| 5 | low | `orchestrator/factory/agents/grinder_agent.py:425` | M4: the proposed rg command breaks --glob, and the item overstates what the fix buys (the same executor runs LLM-written code via run_tests with an unvalidated path) | verified | known-worse |
| 6 | low | `orchestrator/factory/agents/grinder_agent.py:538` | L3: `_run_autofix` is not just shell=True, it is broken on current ruff (`ruff --fix src/` is rejected) | verified | known-worse |
| 8 | low | `TODO.md:425` | 'Add real cost tracking' is partly implemented; the remaining gaps are specific and not what the bullet says | verified | known-still-open |
| 10 | low | `orchestrator/factory/agents/grinder_agent.py:1159` | Branch-name item is narrower than described but unsanitized names also reach shell command text | verified | known-still-open |
| 12 | low | `src/meshwiki/core/storage.py:268` | Blocking-I/O item omits the worst remaining path: `search_pages` scans and parses every page on the event loop | verified | known-still-open |
| 1 | info | `TODO.md:462` | H1, H3 and M1 are fixed on staging but still unchecked in TODO.md | verified | known-fixed-stale-todo |
| 7 | info | `TODO.md:427` | 'Use one shared httpx.AsyncClient per MeshWikiClient' is already done | verified | known-fixed-stale-todo |
| 9 | info | `TODO.md:395` | B10 (decompose planned->planned + acceptance criteria from files_touched) no longer matches the code | verified | known-fixed-stale-todo |
| 13 | info | `TODO.md:435` | Build/dependency hygiene bullets are stale: repo_root is no longer hardcoded, POSTGRES_DSN is not in config, FACTORY_PORT is consistent | verified | known-fixed-stale-todo |

