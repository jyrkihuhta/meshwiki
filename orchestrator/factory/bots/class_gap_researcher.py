"""Class-gap researcher bot.

Periodic bot that surveys Molly's playbook armory + the factory's open
task queue, then asks an LLM to identify gaps worth filling — across
three tracked research streams:

- **generic** — a playbook gap that applies to any matching leaf via
  tech-tag ``applies_to``, not tied to one target's quirks. Always in
  scope, in staging and beyond.
- **target_specific** — a playbook gap that only makes sense against one
  target's proprietary API shape. Gated behind
  ``settings.class_gap_researcher_allow_target_specific`` (default off):
  the staging deployment only has target-dummy practice targets, where a
  target-specific playbook buys nothing over a generic one. Flip the
  setting on for a future production deployment against real targets.
- **tool_idea** — a proposal for a new Molly tool capability that doesn't
  exist yet. Written as a ``toolspecs/`` markdown proposal (NOT working
  code — see ``armory_prompts.TOOLSPEC_FORMAT``), for a human or a later
  ``artifact_type: tool`` task to decide whether to forge for real.

For each suggestion it creates a MeshWiki task page assigned to the
factory, with ``skip_decomposition: true`` so the grinder picks it up
directly.

The default model is non-Anthropic (``MiniMax-M2.7``) so the bot keeps
running even when the Anthropic monthly cap is engaged — making this
useful exactly when human bandwidth is most constrained.

The bot is idempotent: it persists already-suggested ``(target,
vulnerability_class)`` pairs and ``capability_name``s to MeshWiki task
pages (via ``_fetch_open_tasks``) and to the armory itself (via
``_fetch_armory_classes`` / ``_fetch_armory_tool_ideas``) so the next run
doesn't re-suggest the same gaps, even if the previous PRs haven't merged
yet.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import get_settings
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

ARMORY_REPO_DEFAULT = "jyrkihuhta/molly-armory"
# The staging Molly instance is hard scope-locked to target-dummy as of
# 2026-09-05 (deck PLAN.md M2) — real targets (whatnot/boozt/doppler/acronis)
# are paused. KNOWN_TARGETS matters for validating LLM output and for the
# armory-coverage dedup scan, not for steering suggestions toward any one
# target — that's governed by KIND_TARGET_SPECIFIC being gated off by
# default (see settings.class_gap_researcher_allow_target_specific).
KNOWN_TARGETS = ("notekeeper", "taskboard", "dashql", "couponshop", "ssrfbox", "jwtlab")

KIND_GENERIC = "generic"
KIND_TARGET_SPECIFIC = "target_specific"
KIND_TOOL_IDEA = "tool_idea"
_PLAYBOOK_KINDS = (KIND_GENERIC, KIND_TARGET_SPECIFIC)
_ALL_KINDS = frozenset({KIND_GENERIC, KIND_TARGET_SPECIFIC, KIND_TOOL_IDEA})


def _build_system_prompt(allow_target_specific: bool) -> str:
    target_specific_clause = (
        """
- `target_specific` — the attack literally depends on a target-specific \
quirk (e.g. a specific framework version, a known custom endpoint). Set \
`"target"` to the real leaf it fires on."""
        if allow_target_specific
        else """
Do NOT propose `target_specific` suggestions right now — target-specific \
research is disabled for this deployment (it only has practice targets, \
where a target-specific playbook buys nothing over a generic one)."""
    )
    schema_kind_values = (
        '"generic", "target_specific", or "tool_idea"'
        if allow_target_specific
        else '"generic" or "tool_idea" (target_specific is disabled — see above)'
    )
    return f"""You are a security research bot identifying gaps in Molly's \
armory: both playbook (attack-class) gaps and tool-capability gaps.

Molly is an offensive security testing agent. Playbooks are Markdown files \
with YAML frontmatter that describe ONE attack class via a set of HTTP \
mutations and detection criteria. Playbooks match LEAVES (discovered \
endpoints) via a tag intersection between the playbook's `applies_to` \
list and the leaf's `tech_fingerprint` — so a playbook with \
`applies_to: [rest-api, json-body]` fires against EVERY REST endpoint \
that accepts JSON, on EVERY target, not just one.

Every suggestion has a `kind`, one of:
- `generic` — STRONGLY PREFERRED. A class-level playbook with tech-only \
applies_to tags (e.g. [rest-api, webhook, integration]) and relative \
paths in url_override (`/oauth/authorize`, `/auth/saml/acs`) that fires \
across all matching leaves at zero per-target cost.{target_specific_clause}
- `tool_idea` — Molly's TOOL PRIMITIVES themselves can't do something yet \
(not a missing playbook, a missing capability). E.g. no way to forge a \
JWT with an attacker-controlled algorithm, no way to fuzz a binary \
protocol. This does NOT produce a playbook — it produces a tracked \
proposal (a toolspec) for a human or a later build task to evaluate. \
Do NOT propose a tool_idea for something a `generic` playbook using the \
existing http_mutation/llm_analysis capabilities could already cover.

Your job: find gaps NOT yet covered (by an existing playbook, an existing \
tool/toolspec, OR an open factory task) that would meaningfully expand \
Molly's testing capability.

Constraints on every suggestion:
- DISTINCT from anything in the existing list (not a sub-variant or renaming)
- Detectable with clear, evidence-backed pass/fail criteria — a real \
response delta, not a bare status-code guess (this range is for proving out \
playbook mechanics safely; the same discipline as reporting on HackerOne \
applies even though these aren't bounty programs)
- Specific, testable detection criteria (status code, body pattern, OOB \
callback, timing delta, differential response)
- Prefer classes with public H1 disclosures or CVEs as evidence
- Generic-where-possible: write `applies_to` with tech tags, not target names

Output ONLY a JSON array, no surrounding markdown or prose. `kind` is \
{schema_kind_values}.

For `kind: "generic"` or `"target_specific"`:
[
  {{
    "kind": "generic",
    "target": "notekeeper" or "taskboard" or "dashql" or "couponshop" or "ssrfbox" or "jwtlab",
    "vuln_class": "kebab-case-class-name",
    "title": "Short human-readable title (one sentence)",
    "rationale": "Why this gap is worth filling - reportability, \
prevalence on similar targets, recent H1 trends. 2-3 sentences.",
    "test_surface": "Specific endpoints, methods, or capabilities to probe. \
1-2 sentences. Include URL patterns or HTTP verbs where applicable."
  }}
]

For `kind: "tool_idea"`:
[
  {{
    "kind": "tool_idea",
    "capability_name": "snake_case_identifier",
    "title": "Short human-readable title (one sentence)",
    "category": "e.g. auth-bypass, oob, race-condition, misconfiguration",
    "rationale": "What Molly cannot currently do, and why the existing \
built-in capabilities or forged tools don't cover it. 2-3 sentences.",
    "design_sketch": "What the tool would do (inputs/outputs) and which \
attack scenario it would newly unblock. 2-3 sentences."
  }}
]

The `target` field on a `generic` suggestion is a hint for which leaf to \
seed the test against, NOT a constraint that the playbook will only fire \
on that target.
"""


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", s.lower()).strip("-")


def _render_playbook_task_page(
    suggestion: dict, base_url: str, armory_repo: str
) -> tuple[str, str]:
    """Render a MeshWiki task page for a generic/target-specific playbook
    suggestion. Returns (page_name, content)."""
    kind = suggestion["kind"]
    target = suggestion["target"]
    vuln = suggestion["vuln_class"]
    title = suggestion["title"]
    rationale = suggestion.get("rationale", "(no rationale)")
    test_surface = suggestion.get("test_surface", "(no test surface)")
    scope = "generic" if kind == KIND_GENERIC else "target-specific"

    ts_short = datetime.now(timezone.utc).strftime("%m%d")
    safe_vuln = _sanitize(vuln)
    page_name = f"Task_Playbook_{_sanitize(target)}_{safe_vuln}_{ts_short}"

    slug = _slug(vuln)
    filename = f"playbooks/{slug}-{_sanitize(target).lower()}.md"

    frontmatter = (
        "---\n"
        "type: task\n"
        "assignee: factory\n"
        "status: planned\n"
        f'title: "Create playbook: {title}"\n'
        f"repo: {armory_repo}\n"
        "repo_root: playbooks\n"
        "artifact_type: playbook\n"
        "skip_decomposition: true\n"
        f"tags: [playbook, class-gap-researcher, {scope}, {safe_vuln}]\n"
        "estimation: m\n"
        "---\n\n"
        "<<TaskStatus>>\n\n"
    )

    scope_note = (
        f"Frontmatter MUST include `scope: {scope}`"
        + (f" and `target: {target}`" if scope == "target-specific" else "")
        + "."
    )

    body = (
        f"# Create playbook: {title}\n\n"
        "## Context\n\n"
        "This task was generated by the **class-gap-researcher** bot, which "
        "periodically surveys the molly-armory playbook inventory and the "
        "factory task queue and proposes attack-class gaps worth filling.\n\n"
        f"**Kind:** {kind}\n"
        f"**Target:** {target}\n"
        f"**Vulnerability class:** `{vuln}`\n"
        f"**Base URL:** {base_url}\n"
        f"**Created:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "## Why This Class Was Suggested\n\n"
        f"{rationale}\n\n"
        "## Test Surface\n\n"
        f"{test_surface}\n\n"
        "## Deliverable\n\n"
        f"Create `{filename}` in `{armory_repo}` with:\n\n"
        "1. Frontmatter using the strict Molly schema (`playbook`, `name`, "
        "`leaf_type`, `scope`, `applies_to`, `checks` in YAML frontmatter — "
        f"NOT in body fenced blocks). {scope_note}\n"
        "2. At least 3 checks, each with `id`, `name`, `mode` "
        "(`deterministic|analytical|idea|oob`, NOT `intruder|forge`), "
        f"`category: {vuln}`, `severity` from the valid set "
        "`critical|high|medium|low|info|unknown`, a non-trivial `technique` "
        "describing the attack and win condition, and `mutations` as a list "
        "of mappings with keys `body|header|value|url_override|note`.\n"
        "3. References — at least 1 H1 report, CVE, or research writeup.\n\n"
        "## Acceptance Criteria\n\n"
        f"- [ ] Playbook file at `{filename}` lints clean via "
        "`PlaybookLoader.load_all()`\n"
        f"- [ ] Frontmatter has `scope: {scope}`"
        + (f" and `target: {target}`" if scope == "target-specific" else "")
        + "\n"
        "- [ ] At least 3 checks with non-trivial `technique` text\n"
        "- [ ] All checks use valid `mode` and `severity` values\n"
        "- [ ] Mutations are mappings, not bare strings\n"
        "- [ ] References section cites at least 1 external source\n"
    )

    return page_name, frontmatter + body


def _render_toolspec_task_page(suggestion: dict, armory_repo: str) -> tuple[str, str]:
    """Render a MeshWiki task page for a tool_idea suggestion. Returns
    (page_name, content)."""
    capability_name = suggestion["capability_name"]
    title = suggestion["title"]
    category = suggestion.get("category", "misc")
    rationale = suggestion.get("rationale", "(no rationale)")
    design_sketch = suggestion.get("design_sketch", "(no design sketch)")

    ts_short = datetime.now(timezone.utc).strftime("%m%d")
    safe_cap = _sanitize(capability_name)
    page_name = f"Task_Toolspec_{safe_cap}_{ts_short}"

    slug = _slug(capability_name)
    filename = f"toolspecs/{slug}.md"

    frontmatter = (
        "---\n"
        "type: task\n"
        "assignee: factory\n"
        "status: planned\n"
        f'title: "Write toolspec: {title}"\n'
        f"repo: {armory_repo}\n"
        "repo_root: toolspecs\n"
        "artifact_type: toolspec\n"
        "skip_decomposition: true\n"
        f"tags: [toolspec, class-gap-researcher, {category}, {safe_cap}]\n"
        "estimation: s\n"
        "---\n\n"
        "<<TaskStatus>>\n\n"
    )

    body = (
        f"# Write toolspec: {title}\n\n"
        "## Context\n\n"
        "This task was generated by the **class-gap-researcher** bot, which "
        "periodically surveys the molly-armory capability manifest, "
        "toolspecs/ inventory, and factory task queue, and proposes tool "
        "capabilities Molly doesn't have yet.\n\n"
        f"**Kind:** tool_idea\n"
        f"**Proposed capability_name:** `{capability_name}`\n"
        f"**Category:** {category}\n"
        f"**Created:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        "## Why This Capability Was Suggested\n\n"
        f"{rationale}\n\n"
        "## Design Sketch\n\n"
        f"{design_sketch}\n\n"
        "## Deliverable\n\n"
        f"Create `{filename}` in `{armory_repo}` following the Toolspec "
        "Format — a PROPOSAL only, not a working implementation. No Python.\n\n"
        "## Acceptance Criteria\n\n"
        f"- [ ] File at `{filename}` with frontmatter `toolspec`, `name`, "
        f"`capability_name: {capability_name}`, `status: proposed`, `category`\n"
        "- [ ] Problem / Proposed Capability / Example Usage / References "
        "sections all present and non-empty\n"
        "- [ ] References section cites at least 1 external source\n"
        "- [ ] No Python implementation included\n"
    )

    return page_name, frontmatter + body


def _render_task_page(suggestion: dict, base_url: str, armory_repo: str) -> tuple[str, str]:
    """Dispatch to the right task-page renderer for suggestion["kind"]."""
    if suggestion["kind"] == KIND_TOOL_IDEA:
        return _render_toolspec_task_page(suggestion, armory_repo)
    return _render_playbook_task_page(suggestion, base_url, armory_repo)


class ClassGapResearcherBot(BaseBot):
    """Periodic gap-research bot. Surveys armory + open tasks, proposes new
    playbook/toolspec tasks via the configured LLM, and creates MeshWiki
    task pages.
    """

    name = "class-gap-researcher"
    pauses_on_anthropic_block = False  # default model is MiniMax-M2.7

    def __init__(
        self,
        interval_seconds: int | None = None,
        suggestions_per_run: int | None = None,
        model: str | None = None,
        armory_repo: str | None = None,
        allow_target_specific: bool | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.class_gap_researcher_interval_seconds
        )
        self._suggestions_per_run = (
            suggestions_per_run
            if suggestions_per_run is not None
            else settings.class_gap_researcher_suggestions_per_run
        )
        self._model = (
            model if model is not None else settings.class_gap_researcher_model
        )
        self._armory_repo = (
            armory_repo
            if armory_repo is not None
            else (settings.armory_repo or ARMORY_REPO_DEFAULT)
        )
        self._allow_target_specific = (
            allow_target_specific
            if allow_target_specific is not None
            else settings.class_gap_researcher_allow_target_specific
        )

    async def run(self) -> BotResult:
        started = time.monotonic()
        errors: list[str] = []
        actions = 0

        # 1. Inventory existing playbooks + tool ideas in the armory, and
        # open factory tasks.
        try:
            existing_target_specific, existing_generic_slugs = await self._fetch_armory_classes()
        except Exception as e:
            errors.append(f"fetch armory playbooks: {type(e).__name__}: {e}")
            existing_target_specific, existing_generic_slugs = set(), set()
        try:
            existing_tool_ideas = await self._fetch_armory_tool_ideas()
        except Exception as e:
            errors.append(f"fetch armory tool ideas: {type(e).__name__}: {e}")
            existing_tool_ideas = set()
        try:
            open_tasks = await self._fetch_open_tasks()
        except Exception as e:
            errors.append(f"fetch open tasks: {type(e).__name__}: {e}")
            open_tasks = []

        if errors:
            elapsed = time.monotonic() - started
            return BotResult(
                ran_at=started, actions_taken=0, errors=errors,
                details=f"survey failed elapsed={elapsed:.2f}s",
            )

        # 2. Ask the LLM for suggestions
        try:
            suggestions = await self._propose_gaps(
                existing_target_specific, existing_generic_slugs,
                existing_tool_ideas, open_tasks,
            )
        except Exception as e:
            return BotResult(
                ran_at=started, actions_taken=0,
                errors=[f"llm: {type(e).__name__}: {e}"],
                details=(
                    f"existing_target_specific={len(existing_target_specific)} "
                    f"existing_generic={len(existing_generic_slugs)} "
                    f"existing_tool_ideas={len(existing_tool_ideas)} "
                    f"open_tasks={len(open_tasks)}"
                ),
            )

        # `_parse_suggestions` already drops target_specific suggestions when
        # `self._allow_target_specific` is False (it's called with that flag
        # in `_propose_gaps`), even though the prompt also already asked the
        # LLM not to propose them — so `suggestions` here never contains a
        # disallowed kind.

        # 3. Filter out suggestions that duplicate already-known gaps
        seen_playbook = set(existing_target_specific)
        seen_generic = set(existing_generic_slugs)
        seen_tools = set(existing_tool_ideas)
        for t in open_tasks:
            if t["kind"] == KIND_TOOL_IDEA:
                seen_tools.add(t["capability_name"])
            elif t["kind"] == KIND_TARGET_SPECIFIC:
                seen_playbook.add((t["target"], t["vuln_class"]))
            else:
                seen_generic.add(_slug(t["vuln_class"]))

        unique: list[dict] = []
        for s in suggestions:
            kind = s.get("kind")
            if kind == KIND_TOOL_IDEA:
                if s.get("capability_name") not in seen_tools:
                    unique.append(s)
            elif kind == KIND_TARGET_SPECIFIC:
                if (s.get("target"), s.get("vuln_class")) not in seen_playbook:
                    unique.append(s)
            else:  # generic
                if _slug(s.get("vuln_class", "")) not in seen_generic:
                    unique.append(s)

        if not unique:
            elapsed = time.monotonic() - started
            return BotResult(
                ran_at=started, actions_taken=0, errors=[],
                details=(
                    f"all {len(suggestions)} suggestions were duplicates; "
                    f"existing_target_specific={len(existing_target_specific)} "
                    f"existing_generic={len(existing_generic_slugs)} "
                    f"existing_tool_ideas={len(existing_tool_ideas)} "
                    f"open_tasks={len(open_tasks)} elapsed={elapsed:.2f}s"
                ),
            )

        # 4. Create MeshWiki task pages
        for s in unique:
            base_url = self._target_base_url(s["target"]) if s.get("kind") != KIND_TOOL_IDEA else ""
            page_name, content = _render_task_page(s, base_url, self._armory_repo)
            try:
                async with MeshWikiClient() as wiki:
                    await wiki.create_page(page_name, content)
                actions += 1
                logger.info(
                    "class-gap-researcher: created task %s (kind=%s)",
                    page_name, s.get("kind"),
                )
            except Exception as e:
                errors.append(f"create {page_name}: {type(e).__name__}: {e}")

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=(
                f"existing_target_specific={len(existing_target_specific)} "
                f"existing_generic={len(existing_generic_slugs)} "
                f"existing_tool_ideas={len(existing_tool_ideas)} "
                f"open_tasks={len(open_tasks)} "
                f"suggestions={len(suggestions)} "
                f"new={len(unique)} "
                f"elapsed={elapsed:.2f}s"
            ),
        )

    # ------------------------------------------------------------------
    # Survey helpers
    # ------------------------------------------------------------------

    async def _github_get(self, path: str) -> httpx.Response | None:
        """GET a path from the armory repo's Contents API. Returns None on
        404 (missing dir/file — e.g. toolspecs/ before it has any content)
        and raises on any other error."""
        settings = get_settings()
        token = settings.github_token
        repo = self._armory_repo
        if not token or not repo:
            return None
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r

    async def _fetch_armory_classes(self) -> tuple[set[tuple[str, str]], set[str]]:
        """Survey ``<armory_repo>/playbooks/`` on the default branch.

        Returns ``(target_specific_pairs, generic_slugs)``:
        - ``target_specific_pairs``: ``(target, vulnerability_class)`` pairs
          inferred from the filename pattern ``<class>-<target>.md``.
        - ``generic_slugs``: filename stems (best-effort vuln_class slugs)
          for every playbook file NOT matching that per-target pattern —
          i.e. everything scope: generic classification also falls back to
          (see molly-armory's retrofit script for the same heuristic).
        """
        r = await self._github_get("playbooks")
        if r is None:
            return set(), set()
        entries = r.json()

        target_specific: set[tuple[str, str]] = set()
        generic: set[str] = set()
        for entry in entries:
            if entry.get("type") != "file":
                continue
            name = entry.get("name", "")
            if not name.endswith(".md"):
                continue
            stem = name[:-3]  # drop .md
            matched = False
            for t in KNOWN_TARGETS:
                suffix = f"-{t}"
                if stem.endswith(suffix):
                    target_specific.add((t, stem[: -len(suffix)]))
                    matched = True
                    break
            if not matched:
                generic.add(stem)
        return target_specific, generic

    async def _fetch_armory_tool_ideas(self) -> set[str]:
        """Existing/proposed capability names: forged tools + built-in
        capabilities (from ``capabilities.json``) + already-proposed
        toolspecs (from ``toolspecs/`` filenames)."""
        capability_names: set[str] = set()

        r = await self._github_get("capabilities.json")
        if r is not None:
            entry = r.json()
            content = entry.get("content", "")
            try:
                manifest = json.loads(base64.b64decode(content).decode("utf-8"))
                capability_names.update((manifest.get("capabilities") or {}).keys())
            except Exception as e:
                logger.warning(
                    "class-gap-researcher: failed to parse capabilities.json: %s", e
                )

        r = await self._github_get("toolspecs")
        if r is not None:
            for entry in r.json():
                if entry.get("type") != "file":
                    continue
                name = entry.get("name", "")
                if name.endswith(".md"):
                    capability_names.add(name[:-3].replace("-", "_"))

        return capability_names

    async def _fetch_open_tasks(self) -> list[dict]:
        """List factory tasks currently in planned/in_progress/review state.

        Returns records shaped as either
        ``{"kind": "generic"|"target_specific", "target": ..., "vuln_class": ..., "page": ...}``
        or ``{"kind": "tool_idea", "capability_name": ..., "page": ...}``,
        parsed from the page-name conventions
        ``Task_Playbook_<target>_<class>_<date>`` and
        ``Task_Toolspec_<capability>_<date>``. Tasks whose page name doesn't
        fit either pattern are ignored.
        """
        records: list[dict] = []
        playbook_re = re.compile(r"^Task_Playbook_([a-z0-9_-]+?)_([a-z0-9_-]+?)_\d+$")
        toolspec_re = re.compile(r"^Task_Toolspec_([a-z0-9_-]+?)_\d+$")
        async with MeshWikiClient() as wiki:
            for status in ("planned", "in_progress", "review"):
                try:
                    tasks = await wiki.list_tasks(status=status, assignee="factory")
                except Exception as e:
                    logger.warning(
                        "class-gap-researcher: list_tasks(%s) failed: %s", status, e
                    )
                    continue
                for t in tasks or []:
                    name = t.get("name") or ""
                    m = toolspec_re.match(name)
                    if m:
                        records.append({
                            "kind": KIND_TOOL_IDEA,
                            "capability_name": m.group(1).replace("-", "_"),
                            "page": name,
                        })
                        continue
                    m = playbook_re.match(name)
                    if not m:
                        continue
                    raw_target, raw_class = m.group(1), m.group(2)
                    target = raw_target.replace("_", "-")
                    vuln_class = raw_class.replace("_", "-")
                    kind = KIND_TARGET_SPECIFIC if target in KNOWN_TARGETS else KIND_GENERIC
                    records.append({
                        "kind": kind, "target": target, "vuln_class": vuln_class, "page": name,
                    })
        return records

    # ------------------------------------------------------------------
    # LLM + suggestion plumbing
    # ------------------------------------------------------------------

    async def _propose_gaps(
        self,
        existing_target_specific: set[tuple[str, str]],
        existing_generic_slugs: set[str],
        existing_tool_ideas: set[str],
        open_tasks: list[dict],
    ) -> list[dict]:
        target_specific_lines = "\n".join(
            f"- {t}: {c}" for t, c in sorted(existing_target_specific)
        ) or "(none)"
        generic_lines = "\n".join(f"- {s}" for s in sorted(existing_generic_slugs)) or "(none)"
        tool_idea_lines = "\n".join(f"- {c}" for c in sorted(existing_tool_ideas)) or "(none)"
        task_lines = "\n".join(
            f"- [{t['kind']}] "
            + (f"{t.get('target')}: {t.get('vuln_class')}" if t["kind"] != KIND_TOOL_IDEA
               else t.get("capability_name", ""))
            for t in open_tasks
        ) or "(none)"
        user_msg = (
            f"## Already covered by an existing target-specific playbook\n\n"
            f"{target_specific_lines}\n\n"
            f"## Already covered by an existing generic playbook (best-effort slugs)\n\n"
            f"{generic_lines}\n\n"
            f"## Already covered by an existing tool or toolspec\n\n"
            f"{tool_idea_lines}\n\n"
            f"## Already queued as a factory task (do not duplicate)\n\n{task_lines}\n\n"
            f"## Targets in scope (the target-dummy practice range)\n\n"
            f"- notekeeper — notes API; IDOR on GET /notes/{{id}} (no ownership check)\n"
            f"- taskboard — task tracker; mass assignment (role field on PUT /users/me) "
            f"+ privilege escalation payoff\n"
            f"- dashql — GraphQL dashboard; X-Role header trust bypass + introspection "
            f"exposing admin-only operations\n"
            f"- couponshop — coupon/orders API; TOCTOU race condition on redemption + "
            f"IDOR on orders\n"
            f"- ssrfbox — URL fetcher/importer; blind SSRF and blind XXE, both requiring "
            f"OOB-callback detection\n"
            f"- jwtlab — JWT auth service; algorithm confusion (alg:none, RS256→HS256 "
            f"key confusion)\n\n"
            f"Suggest {self._suggestions_per_run} additional gaps (mix of kinds as "
            "appropriate). Output ONLY the JSON array."
        )

        text, _toks = await self._call_llm(user_msg)
        return _parse_suggestions(text, self._suggestions_per_run, self._allow_target_specific)

    async def _call_llm(self, user_msg: str) -> tuple[str, int]:
        """Call the configured LLM via the right provider for this model name."""
        settings = get_settings()
        model = self._model
        system_prompt = _build_system_prompt(self._allow_target_specific)

        if model.lower().startswith("minimax") and settings.minimax_api_key:
            return await _openai_compat_call(
                base_url="https://api.minimax.io/v1",
                api_key=settings.minimax_api_key,
                model=model,
                system=system_prompt,
                user=user_msg,
                timeout=60.0,
            )
        if settings.openrouter_api_key:
            return await _openai_compat_call(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                model=model,
                system=system_prompt,
                user=user_msg,
                timeout=90.0,
                extra_headers={
                    "HTTP-Referer": settings.meshwiki_url,
                    "X-Title": "factory class-gap-researcher",
                },
            )
        raise RuntimeError(
            "class-gap-researcher: no provider configured for model "
            f"{model!r} (need MINIMAX_API_KEY or OPENROUTER_API_KEY)"
        )

    def _target_base_url(self, target: str) -> str:
        # Matches target-dummy/molly-config.json — internal Docker network
        # hostnames on the target-dummy_range network, not public URLs.
        mapping = {
            "notekeeper": "http://notekeeper:5000",
            "taskboard": "http://taskboard:5000",
            "dashql": "http://dashql:5000",
            "couponshop": "http://couponshop:5000",
            "ssrfbox": "http://ssrfbox:5000",
            "jwtlab": "http://jwtlab:5056",
        }
        return mapping.get(target, "")


# ---- helpers ----------------------------------------------------------


async def _openai_compat_call(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> tuple[str, int]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    body = {
        "model": model,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/chat/completions", headers=headers, json=body,
        )
        r.raise_for_status()
        data = r.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    out_tokens = int(usage.get("completion_tokens", 0) or 0)
    return text, out_tokens


def _parse_suggestions(text: str, cap: int, allow_target_specific: bool) -> list[dict]:
    """Extract the JSON array of suggestions from the LLM response.
    Tolerates surrounding markdown code fences and trailing prose.
    Validates the required keys per ``kind`` and drops anything malformed.
    """
    # Strip fenced markdown
    s = text.strip()
    if s.startswith("```"):
        # Drop opening fence + language tag
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()

    # Find first array bracket and try to parse from there
    start = s.find("[")
    if start == -1:
        return []
    # Find the matching closing bracket by depth-tracking
    depth = 0
    end = -1
    for i, ch in enumerate(s[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return []
    try:
        arr = json.loads(s[start:end])
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []

    allowed_kinds = _ALL_KINDS if allow_target_specific else (_ALL_KINDS - {KIND_TARGET_SPECIFIC})

    valid: list[dict] = []
    for item in arr[:cap]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", KIND_GENERIC)  # default for backward-compat with older prompts
        if kind not in allowed_kinds:
            continue
        if kind == KIND_TOOL_IDEA:
            if not all(k in item for k in ("capability_name", "title")):
                continue
        else:
            if not all(k in item for k in ("target", "vuln_class", "title")):
                continue
            if item["target"] not in KNOWN_TARGETS:
                continue
        item["kind"] = kind
        valid.append(item)
    return valid
