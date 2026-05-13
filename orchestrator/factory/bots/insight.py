"""Insight bot: synthesizes grinder observations and proposes improvement tasks.

On each tick the bot:
1. Finds done/merged tasks modified in the past week that have Grinder Observations.
2. Fetches existing draft/planned/in_progress tasks as a dedup guard.
3. Calls Haiku to identify at most 3 easy, high-value improvements.
4. Creates a planned task wiki page for each non-duplicate proposal.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

import anthropic

from ..agents.pm_agent import safe_messages_create
from ..config import get_settings
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_OBSERVATIONS_MARKER = "## Grinder Observations"

_SYNTHESIS_PROMPT = """You are a factory process improver reviewing grinder agent observations from the past week.

Identify at most 3 easy, high-value improvements to the grinder workflow.

Criteria for inclusion:
- Easy win: implementable in under 1 hour of grinder time (small prompt tweak, config change, or simple code fix)
- Clear benefit: directly prevents a recurring failure or removes wasted steps seen in the observations
- Specific: concrete enough that a grinder agent can implement it without ambiguity

Already planned or in-progress tasks (skip anything already covered):
{existing_tasks}

Observations from completed tasks:
{observations}

Respond with a JSON array only — no prose, no markdown fences. Each item:
{{"title": "Short imperative title under 60 chars", "slug": "lowercase_underscore_max_5_words", "description": "1-2 sentences describing the problem and the fix", "acceptance_criteria": "- criterion 1\\n- criterion 2\\n- criterion 3"}}

If there are no easy wins, or everything is already planned, respond with an empty array: []"""


def _extract_observations(content: str) -> str | None:
    """Extract the Grinder Observations section text from page content."""
    start = content.find(_OBSERVATIONS_MARKER)
    if start == -1:
        return None
    section = content[start + len(_OBSERVATIONS_MARKER) :].strip()
    next_heading = re.search(r"\n## ", section)
    if next_heading:
        section = section[: next_heading.start()]
    return section.strip() or None


def _parse_modified(value: Any) -> datetime | None:
    """Parse a modified timestamp from the API response to a naive datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            s = str(value)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
    # Strip timezone so comparisons work against naive datetime.now()
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _next_task_number(names: list[str]) -> int:
    """Find the next available Task_NNNN number from a list of page names."""
    max_num = 0
    for name in names:
        m = re.match(r"Task_(\d+)", name, re.IGNORECASE)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return max_num + 1


class InsightBot(BaseBot):
    """Weekly bot that synthesizes grinder observations and proposes improvement tasks."""

    name = "insight"
    pauses_on_anthropic_block = True

    def __init__(
        self,
        interval_seconds: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.insight_interval_seconds
        )
        self._model = model if model is not None else settings.insight_model

    async def run(self) -> BotResult:
        """Execute one insight synthesis cycle."""
        started = time.monotonic()
        errors: list[str] = []

        settings = get_settings()
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=60.0,
        )

        async with MeshWikiClient() as wiki:
            cutoff = datetime.now() - timedelta(days=7)

            observations = await self._collect_observations(wiki, cutoff, errors)
            if not observations:
                elapsed = time.monotonic() - started
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=errors,
                    details=f"no observations in past week elapsed={elapsed:.2f}s",
                )

            existing_titles = await self._existing_active_titles(wiki, errors)
            proposals = await self._synthesize(client, observations, existing_titles)

            if not proposals:
                elapsed = time.monotonic() - started
                return BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=errors,
                    details=(
                        f"observations={len(observations)} no proposals generated "
                        f"elapsed={elapsed:.2f}s"
                    ),
                )

            all_names = await self._all_task_names(wiki)
            next_num = _next_task_number(all_names)
            actions = 0

            for proposal in proposals:
                try:
                    page_name = await self._create_task(wiki, proposal, next_num)
                    next_num += 1
                    actions += 1
                    logger.info("insight-bot: created task %s", page_name)
                except Exception as exc:
                    err = f"failed to create task '{proposal.get('title')}': {exc}"
                    logger.warning("insight-bot: %s", err)
                    errors.append(err)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=(
                f"observations={len(observations)} proposals={len(proposals)} "
                f"created={actions} elapsed={elapsed:.2f}s"
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _collect_observations(
        self,
        wiki: MeshWikiClient,
        cutoff: datetime,
        errors: list[str],
    ) -> list[dict[str, str]]:
        """Return {page_name, observations} for tasks completed since cutoff."""
        results: list[dict[str, str]] = []
        for status in ("done", "merged"):
            try:
                tasks = await wiki.list_tasks(status=status)
            except Exception as exc:
                errors.append(f"list_tasks({status!r}) failed: {exc}")
                continue

            for task in tasks:
                name = task.get("name", "")
                if not name:
                    continue

                modified = _parse_modified(task.get("metadata", {}).get("modified"))
                if modified is not None and modified < cutoff:
                    continue  # too old; skip

                try:
                    page = await wiki.get_page(name)
                except Exception as exc:
                    errors.append(f"get_page({name!r}) failed: {exc}")
                    continue

                if page is None:
                    continue

                obs = _extract_observations(page.get("content", ""))
                if obs:
                    results.append({"page_name": name, "observations": obs})

        return results

    async def _existing_active_titles(
        self,
        wiki: MeshWikiClient,
        errors: list[str],
    ) -> list[str]:
        """Return titles of currently active tasks for dedup."""
        titles: list[str] = []
        for status in ("draft", "planned", "in_progress"):
            try:
                tasks = await wiki.list_tasks(status=status)
                for t in tasks:
                    title = t.get("metadata", {}).get("title") or t.get("name", "")
                    if title:
                        titles.append(title)
            except Exception as exc:
                errors.append(f"list_tasks({status!r}) for dedup failed: {exc}")
        return titles

    async def _all_task_names(self, wiki: MeshWikiClient) -> list[str]:
        """Return all task/epic page names to determine the next available number."""
        names: list[str] = []
        for status in ("draft", "planned", "in_progress", "done", "merged", "failed"):
            try:
                tasks = await wiki.list_tasks(status=status)
                names.extend(t.get("name", "") for t in tasks if t.get("name"))
            except Exception:
                pass
        return names

    async def _synthesize(
        self,
        client: anthropic.AsyncAnthropic,
        observations: list[dict[str, str]],
        existing_titles: list[str],
    ) -> list[dict[str, str]]:
        """Call the LLM to synthesize observations into actionable task proposals."""
        obs_text = "\n\n".join(
            f"### {o['page_name']}\n{o['observations']}" for o in observations
        )
        existing_text = (
            "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "(none)"
        )
        prompt = _SYNTHESIS_PROMPT.format(
            observations=obs_text,
            existing_tasks=existing_text,
        )

        try:
            response = await safe_messages_create(
                client,
                model=self._model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.error("insight-bot: LLM call failed: %s", exc)
            return []

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
                break

        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            logger.warning("insight-bot: no JSON array in response: %r", text[:300])
            return []

        try:
            proposals = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            logger.warning("insight-bot: failed to parse proposals JSON: %s", exc)
            return []

        valid = [
            p
            for p in proposals
            if isinstance(p, dict) and p.get("title") and p.get("slug") and p.get("description")
        ]
        return valid[:3]

    async def _create_task(
        self,
        wiki: MeshWikiClient,
        proposal: dict[str, str],
        task_num: int,
    ) -> str:
        """Create a planned factory task page from a proposal. Returns the page name."""
        slug = re.sub(r"[^a-z0-9]+", "_", proposal["slug"].lower()).strip("_")
        page_name = f"Task_{task_num:04d}_{slug}"
        criteria = proposal.get("acceptance_criteria", "- Improvement implemented")

        content = (
            f"---\n"
            f"title: {proposal['title']}\n"
            f"type: task\n"
            f"status: planned\n"
            f"assignee: factory\n"
            f"priority: normal\n"
            f"skip_decomposition: true\n"
            f"tags:\n"
            f"  - factory\n"
            f"  - grinder-improvement\n"
            f"---\n\n"
            f"# {proposal['title']}\n\n"
            f"{proposal['description']}\n\n"
            f"## Acceptance Criteria\n\n"
            f"{criteria}\n\n"
            f"## Source\n\n"
            f"Proposed by InsightBot based on grinder observations from the past week.\n"
        )
        await wiki.create_page(page_name, content)
        return page_name
