"""CI Fixer bot: detects failing CI on factory PRs and posts analysis.

On each tick the bot:

1. Lists wiki tasks in ``review`` status that have a ``pr_url`` field.
2. For each, fetches GitHub check runs on the PR head SHA.
3. Skips if all checks passed, or if ``ci_fix_attempts`` has reached the cap.
4. Fetches the failure text from the check run output (or job log as fallback).
5. Calls the LLM to classify the failure and suggest a fix.
6. Posts a structured comment on the PR.
7. Appends a ``## CI Failure`` section to the wiki task page and increments
   ``ci_fix_attempts`` so the bot does not re-analyze the same failure twice.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import anthropic

from ..config import get_settings
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_MAX_FAILURE_CHARS = 6000

_ANALYSIS_PROMPT = """\
You are reviewing a CI failure on a factory-generated pull request.

Check name: {check_name}
Failure output:
{failure_text}

Reply in this exact format (no extra text):
CATEGORY: <missing_dep | import_error | test_failure | lint_error | timeout | other>
RETRYABLE: <yes | no>
ROOT_CAUSE: <one sentence>
SUGGESTED_FIX: <one concrete sentence the grinder can act on>"""


def _parse_analysis(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip().lower()] = value.strip()
    return result


def _job_id_from_details_url(url: str) -> int | None:
    m = re.search(r"/jobs?/(\d+)", url)
    return int(m.group(1)) if m else None


class CIFixerBot(BaseBot):
    """Periodic bot that detects and annotates CI failures on factory PRs.

    Reads ``FACTORY_CI_FIXER_INTERVAL_SECONDS``, ``FACTORY_CI_FIXER_MAX_ATTEMPTS``,
    and ``FACTORY_CI_FIXER_MODEL`` from settings.
    """

    name = "ci-fixer"

    def __init__(self, interval_seconds: int | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.ci_fixer_interval_seconds
        )
        self._max_attempts = settings.ci_fixer_max_attempts
        self._model = settings.ci_fixer_model

    async def run(self) -> BotResult:
        started = time.monotonic()
        actions = 0
        errors: list[str] = []

        settings = get_settings()
        anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=60.0,
        )

        async with MeshWikiClient() as wiki, GitHubClient() as gh:
            candidates = await self._find_candidates(wiki, errors)
            for item in candidates:
                try:
                    acted = await self._process(item, wiki, gh, anthropic_client)
                    if acted:
                        actions += 1
                except Exception as exc:
                    err = f"ci-fixer: error on {item['task_name']}: {exc}"
                    logger.warning(err)
                    errors.append(err)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=f"candidates={len(candidates)} elapsed={elapsed:.2f}s",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find_candidates(
        self, wiki: MeshWikiClient, errors: list[str]
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        try:
            tasks = await wiki.list_tasks(status="review", assignee="factory")
        except Exception as exc:
            errors.append(f"list_tasks(review) failed: {exc}")
            return candidates

        for task in tasks:
            name: str = task.get("name", "")
            if not name:
                continue
            meta = task.get("metadata") or {}
            pr_url: str = meta.get("pr_url", "") or ""
            if not pr_url:
                continue
            pr_number = _extract_pr_number(pr_url)
            if pr_number is None:
                continue
            attempts = int(meta.get("ci_fix_attempts", 0) or 0)
            if attempts >= self._max_attempts:
                logger.debug("ci-fixer: %s reached max attempts (%d) — skip", name, attempts)
                continue
            candidates.append(
                {"task_name": name, "pr_number": pr_number, "attempts": attempts}
            )
        return candidates

    async def _process(
        self,
        item: dict[str, Any],
        wiki: MeshWikiClient,
        gh: GitHubClient,
        anthropic_client: anthropic.AsyncAnthropic,
    ) -> bool:
        task_name: str = item["task_name"]
        pr_number: int = item["pr_number"]

        pr = await gh.get_pr(pr_number)
        sha: str = pr.get("head", {}).get("sha", "")
        if not sha:
            return False

        check_runs = await gh.get_check_runs(sha)
        failed = [
            cr for cr in check_runs
            if cr.get("conclusion") in ("failure", "timed_out", "action_required")
        ]
        if not failed:
            return False

        # Use the first failed check
        check = failed[0]
        check_name: str = check.get("name", "unknown")
        failure_text = self._extract_failure_text(check)

        if not failure_text:
            # Fall back to job log
            job_id = _job_id_from_details_url(check.get("details_url", ""))
            if job_id:
                try:
                    failure_text = await gh.get_job_log(job_id)
                except Exception as exc:
                    logger.debug("ci-fixer: could not fetch job log for %s: %s", task_name, exc)

        if not failure_text:
            logger.debug("ci-fixer: no failure text for %s — skip", task_name)
            return False

        failure_text = failure_text[:_MAX_FAILURE_CHARS]
        analysis = await self._analyze(anthropic_client, check_name, failure_text)

        await self._post_pr_comment(gh, pr_number, check_name, analysis, failure_text)
        await self._annotate_wiki_page(wiki, task_name, item["attempts"], check_name, analysis)

        logger.info(
            "ci-fixer: annotated %s (pr=#%d check=%r category=%s retryable=%s)",
            task_name,
            pr_number,
            check_name,
            analysis.get("category", "?"),
            analysis.get("retryable", "?"),
        )
        return True

    def _extract_failure_text(self, check_run: dict) -> str:
        output = check_run.get("output") or {}
        parts = []
        if output.get("title"):
            parts.append(output["title"])
        if output.get("summary"):
            parts.append(output["summary"])
        if output.get("text"):
            parts.append(output["text"])
        return "\n".join(parts).strip()

    async def _analyze(
        self,
        client: anthropic.AsyncAnthropic,
        check_name: str,
        failure_text: str,
    ) -> dict[str, str]:
        prompt = _ANALYSIS_PROMPT.format(
            check_name=check_name, failure_text=failure_text
        )
        try:
            resp = await client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            text = next(
                (b.text for b in resp.content if hasattr(b, "text")), ""
            )
            return _parse_analysis(text)
        except Exception as exc:
            logger.warning("ci-fixer: LLM analysis failed: %s", exc)
            return {
                "category": "other",
                "retryable": "unknown",
                "root_cause": str(exc),
                "suggested_fix": "Check CI logs manually.",
            }

    async def _post_pr_comment(
        self,
        gh: GitHubClient,
        pr_number: int,
        check_name: str,
        analysis: dict[str, str],
        failure_text: str,
    ) -> None:
        snippet = failure_text[-2000:].strip()
        body = (
            f"## CI Failure Analysis\n\n"
            f"**Check:** `{check_name}`  \n"
            f"**Category:** {analysis.get('category', 'unknown')}  \n"
            f"**Retryable:** {analysis.get('retryable', 'unknown')}  \n"
            f"**Root cause:** {analysis.get('root_cause', '—')}  \n"
            f"**Suggested fix:** {analysis.get('suggested_fix', '—')}\n\n"
            f"<details><summary>Failure output (tail)</summary>\n\n"
            f"```\n{snippet}\n```\n</details>\n\n"
            f"*Posted by ci-fixer bot.*"
        )
        try:
            await gh.create_pr_comment(pr_number, body)
        except Exception as exc:
            logger.warning("ci-fixer: failed to post PR comment on #%d: %s", pr_number, exc)

    async def _annotate_wiki_page(
        self,
        wiki: MeshWikiClient,
        task_name: str,
        current_attempts: int,
        check_name: str,
        analysis: dict[str, str],
    ) -> None:
        section = (
            f"## CI Failure\n\n"
            f"**Check:** `{check_name}`  \n"
            f"**Category:** {analysis.get('category', 'unknown')}  \n"
            f"**Root cause:** {analysis.get('root_cause', '—')}  \n"
            f"**Suggested fix:** {analysis.get('suggested_fix', '—')}\n"
        )
        await wiki.append_to_page(
            task_name,
            section,
            frontmatter_updates={"ci_fix_attempts": current_attempts + 1},
        )
