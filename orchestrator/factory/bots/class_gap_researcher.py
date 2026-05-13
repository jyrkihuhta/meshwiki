"""Class-gap researcher bot.

Periodic bot that surveys Molly's playbook armory + the factory's open
task queue, then asks an LLM to identify attack-class gaps worth filling.
For each suggestion it creates a MeshWiki task page assigned to the
factory, with ``skip_decomposition: true`` so the grinder picks it up
directly.

The default model is non-Anthropic (``MiniMax-M2.7``) so the bot keeps
running even when the Anthropic monthly cap is engaged — making this
useful exactly when human bandwidth is most constrained.

The bot is idempotent: it persists already-suggested ``(target,
vulnerability_class)`` pairs to ``Factory/Bots/class-gap-researcher`` so
the next run doesn't re-suggest the same gaps, even if the previous PRs
haven't merged yet.
"""

from __future__ import annotations

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
KNOWN_TARGETS = ("whatnot", "boozt", "doppler")

_SYSTEM_PROMPT = """You are a security research bot identifying gaps in \
Molly's playbook armory.

Molly is an offensive security testing agent. Playbooks are Markdown files \
with YAML frontmatter that describe ONE attack class via a set of HTTP \
mutations and detection criteria. Playbooks match LEAVES (discovered \
endpoints) via a tag intersection between the playbook's `applies_to` \
list and the leaf's `tech_fingerprint` — so a playbook with \
`applies_to: [rest-api, json-body]` fires against EVERY REST endpoint \
that accepts JSON, on EVERY target, not just one.

**Strongly prefer GENERIC playbooks.** A class-level playbook with \
tech-only applies_to tags (e.g. [rest-api, webhook, integration]) and \
relative paths in url_override (`/oauth/authorize`, `/auth/saml/acs`) \
fires across all matching leaves at zero per-target cost. Target-specific \
playbooks (with `whatnot` / `boozt` / `doppler` in applies_to, or absolute \
URLs hard-coded into mutations) only help one target and lock the playbook \
to assumptions that may not hold elsewhere. Suggest target-specific \
playbooks ONLY when the attack literally depends on a target-specific \
quirk (e.g. a specific framework version, a known custom endpoint).

Your job: find attack classes NOT yet covered (by either an existing \
playbook OR an open factory task) and would meaningfully expand Molly's \
testing capability across the listed targets.

Constraints on every suggestion:
- DISTINCT from anything in the existing list (not a sub-variant or renaming)
- Reportable on HackerOne or similar programs (not information-only)
- Specific, testable detection criteria (status code, body pattern, OOB \
callback, timing delta, differential response)
- Prefer classes with public H1 disclosures or CVEs as evidence
- Generic-where-possible: write `applies_to` with tech tags, not target names

Output ONLY a JSON array, no surrounding markdown or prose:

[
  {
    "target": "whatnot" or "boozt" or "doppler",
    "vuln_class": "kebab-case-class-name",
    "title": "Short human-readable title (one sentence)",
    "rationale": "Why this gap is worth filling - reportability, \
prevalence on similar targets, recent H1 trends. 2-3 sentences.",
    "test_surface": "Specific endpoints, methods, or capabilities to probe. \
1-2 sentences. Include URL patterns or HTTP verbs where applicable.",
    "generic_friendly": true
  }
]

The `target` field is a hint for which leaf to seed the test against, NOT a \
constraint that the playbook will only fire on that target. Set \
`generic_friendly: true` when the attack class applies to any matching \
leaf regardless of target; `false` only for genuinely target-specific quirks.
"""


def _sanitize(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", s.lower()).strip("-")


def _render_task_page(suggestion: dict, base_url: str, armory_repo: str) -> tuple[str, str]:
    """Render a MeshWiki task page for one suggestion. Returns (page_name, content)."""
    target = suggestion["target"]
    vuln = suggestion["vuln_class"]
    title = suggestion["title"]
    rationale = suggestion.get("rationale", "(no rationale)")
    test_surface = suggestion.get("test_surface", "(no test surface)")

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
        f"tags: [playbook, class-gap-researcher, {safe_vuln}]\n"
        "estimation: m\n"
        "---\n\n"
        "<<TaskStatus>>\n\n"
    )

    body = (
        f"# Create playbook: {title}\n\n"
        "## Context\n\n"
        "This task was generated by the **class-gap-researcher** bot, which "
        "periodically surveys the molly-armory playbook inventory and the "
        "factory task queue and proposes attack-class gaps worth filling.\n\n"
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
        "`leaf_type`, `applies_to`, `checks` in YAML frontmatter — NOT in "
        "body fenced blocks).\n"
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
        "- [ ] At least 3 checks with non-trivial `technique` text\n"
        "- [ ] All checks use valid `mode` and `severity` values\n"
        "- [ ] Mutations are mappings, not bare strings\n"
        "- [ ] References section cites at least 1 external source\n"
    )

    return page_name, frontmatter + body


class ClassGapResearcherBot(BaseBot):
    """Periodic gap-research bot. Surveys armory + open tasks, proposes new
    playbook tasks via the configured LLM, and creates MeshWiki task pages.
    """

    name = "class-gap-researcher"
    pauses_on_anthropic_block = False  # default model is MiniMax-M2.7

    def __init__(
        self,
        interval_seconds: int | None = None,
        suggestions_per_run: int | None = None,
        model: str | None = None,
        armory_repo: str | None = None,
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

    async def run(self) -> BotResult:
        started = time.monotonic()
        errors: list[str] = []
        actions = 0

        # 1. Inventory existing playbooks in the armory + open factory tasks
        try:
            existing_classes = await self._fetch_armory_classes()
        except Exception as e:
            errors.append(f"fetch armory: {type(e).__name__}: {e}")
            existing_classes = []
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
            suggestions = await self._propose_gaps(existing_classes, open_tasks)
        except Exception as e:
            return BotResult(
                ran_at=started, actions_taken=0,
                errors=[f"llm: {type(e).__name__}: {e}"],
                details=f"existing_classes={len(existing_classes)} open_tasks={len(open_tasks)}",
            )

        # 3. Filter out suggestions that duplicate already-known classes
        seen = {(t, c) for t, c in existing_classes}
        seen.update({(t["target"], t["vuln_class"]) for t in open_tasks})
        unique = [s for s in suggestions if (s.get("target"), s.get("vuln_class")) not in seen]
        if not unique:
            return BotResult(
                ran_at=started, actions_taken=0, errors=[],
                details=(
                    f"all {len(suggestions)} suggestions were duplicates; "
                    f"existing_classes={len(existing_classes)} "
                    f"open_tasks={len(open_tasks)}"
                ),
            )

        # 4. Create MeshWiki task pages
        for s in unique:
            base_url = self._target_base_url(s["target"])
            page_name, content = _render_task_page(s, base_url, self._armory_repo)
            try:
                async with MeshWikiClient() as wiki:
                    await wiki.create_page(page_name, content)
                actions += 1
                logger.info(
                    "class-gap-researcher: created task %s (target=%s class=%s)",
                    page_name, s["target"], s["vuln_class"],
                )
            except Exception as e:
                errors.append(f"create {page_name}: {type(e).__name__}: {e}")

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=(
                f"existing_classes={len(existing_classes)} "
                f"open_tasks={len(open_tasks)} "
                f"suggestions={len(suggestions)} "
                f"new={len(unique)} "
                f"elapsed={elapsed:.2f}s"
            ),
        )

    # ------------------------------------------------------------------
    # Survey helpers
    # ------------------------------------------------------------------

    async def _fetch_armory_classes(self) -> list[tuple[str, str]]:
        """List ``(target, vulnerability_class)`` pairs already covered by an
        armory playbook. Source: the GitHub Contents API for
        ``<armory_repo>/playbooks/`` on the default branch. Class+target are
        inferred from the filename pattern ``<class>-<target>.md``.
        """
        settings = get_settings()
        token = settings.github_token
        repo = self._armory_repo
        if not token or not repo:
            return []

        url = f"https://api.github.com/repos/{repo}/contents/playbooks"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            entries = r.json()

        classes: list[tuple[str, str]] = []
        for entry in entries:
            if entry.get("type") != "file":
                continue
            name = entry.get("name", "")
            if not name.endswith(".md"):
                continue
            stem = name[:-3]  # drop .md
            # Match ``<class>-<target>.md`` where target is one of KNOWN_TARGETS
            for t in KNOWN_TARGETS:
                suffix = f"-{t}"
                if stem.endswith(suffix):
                    classes.append((t, stem[: -len(suffix)]))
                    break
        return classes

    async def _fetch_open_tasks(self) -> list[dict]:
        """List factory tasks currently in planned/in_progress/review state.
        Returns a list of ``{"target": ..., "vuln_class": ..., "page": ...}``
        records when parseable, ignoring tasks whose page name doesn't fit
        the ``Task_Playbook_<target>_<class>_<date>`` pattern.
        """
        records: list[dict] = []
        page_re = re.compile(r"^Task_Playbook_([a-z0-9_-]+?)_([a-z0-9_-]+?)_\d+$")
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
                    m = page_re.match(name)
                    if not m:
                        continue
                    raw_target, raw_class = m.group(1), m.group(2)
                    target = raw_target.replace("_", "-")
                    vuln_class = raw_class.replace("_", "-")
                    records.append({
                        "target": target, "vuln_class": vuln_class, "page": name,
                    })
        return records

    # ------------------------------------------------------------------
    # LLM + suggestion plumbing
    # ------------------------------------------------------------------

    async def _propose_gaps(
        self, existing: list[tuple[str, str]], open_tasks: list[dict]
    ) -> list[dict]:
        existing_lines = "\n".join(
            f"- {t}: {c}" for t, c in sorted(existing)
        ) or "(none)"
        task_lines = "\n".join(
            f"- {t['target']}: {t['vuln_class']}" for t in open_tasks
        ) or "(none)"
        user_msg = (
            f"## Already covered by an existing playbook\n\n{existing_lines}\n\n"
            f"## Already queued as a factory task (do not duplicate)\n\n{task_lines}\n\n"
            f"## Targets in scope\n\n"
            f"- whatnot — live-commerce auction platform (HackerOne)\n"
            f"- boozt — Nordic e-commerce (HackerOne)\n"
            f"- doppler — secrets management API (HackerOne)\n\n"
            f"Suggest {self._suggestions_per_run} additional attack-class gaps. "
            "Output ONLY the JSON array."
        )

        text, _toks = await self._call_llm(user_msg)
        return _parse_suggestions(text, self._suggestions_per_run)

    async def _call_llm(self, user_msg: str) -> tuple[str, int]:
        """Call the configured LLM via the right provider for this model name."""
        settings = get_settings()
        model = self._model

        if model.lower().startswith("minimax") and settings.minimax_api_key:
            return await _openai_compat_call(
                base_url="https://api.minimax.io/v1",
                api_key=settings.minimax_api_key,
                model=model,
                system=_SYSTEM_PROMPT,
                user=user_msg,
                timeout=60.0,
            )
        if settings.openrouter_api_key:
            return await _openai_compat_call(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                model=model,
                system=_SYSTEM_PROMPT,
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
        mapping = {
            "whatnot": "https://api.whatnot.com",
            "boozt":   "https://www.boozt.com",
            "doppler": "https://api.doppler.com",
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


def _parse_suggestions(text: str, cap: int) -> list[dict]:
    """Extract the JSON array of suggestions from the LLM response.
    Tolerates surrounding markdown code fences and trailing prose.
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
    # Validate each entry has the required keys
    valid: list[dict] = []
    for item in arr[:cap]:
        if not isinstance(item, dict):
            continue
        if not all(k in item for k in ("target", "vuln_class", "title")):
            continue
        if item["target"] not in KNOWN_TARGETS:
            continue
        valid.append(item)
    return valid
