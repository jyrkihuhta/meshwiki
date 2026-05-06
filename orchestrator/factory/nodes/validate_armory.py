"""Validate armory node: deterministic quality gate for armory artifact PRs.

Runs between ``pm_review`` and ``human_review_code``.  For non-armory tasks
(``artifact_type`` is ``None`` or ``"code"``) this node is a transparent
pass-through.

For armory artifact types it checks:
- **tool**: no forbidden network-I/O imports in any changed ``.py`` file.
- **playbook**: all YAML blocks in changed ``.md``/``.yaml`` files parse cleanly.
- **wordlist**: no validation (structure is trivially valid).

On failure the node marks the offending subtask as ``changes_requested``,
posts a review comment on the PR, and sets ``graph_status`` to
``"armory_validation_failed"`` so the routing function re-dispatches the
grinder.  On success ``graph_status`` is set to ``"armory_validated"``.
"""

from __future__ import annotations

import logging
import re

import yaml

from ..armory_prompts import FORBIDDEN_IMPORTS
from ..config import get_settings
from ..integrations.github_client import GitHubClient, _extract_pr_number
from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)

ARMORY_TYPES: frozenset[str] = frozenset({"tool", "playbook", "wordlist"})

# The vocabulary that the contract test (molly-armory/contract-test/) validates.
# - "deterministic" — runs via Intruder mutation sweep
# - "analytical"    — runs via the 7-stage LLM pipeline
# - "idea"          — dormant; promoted during research
# - "oob"           — runs via OobGenerateTool; injects {{OOB_URL}} payloads
# Other words (intruder, forge, nuclei, jwt_forge, …) are rejected: nuclei/
# feroxbuster/jwt_forge/concurrent are routed via `requires_capabilities`,
# not the `mode:` field.
_VALID_MODES: frozenset[str] = frozenset({"deterministic", "analytical", "idea", "oob"})
_VALID_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low", "info", "unknown"}
)
# `playbook` is the top-level required key — Playbook.from_doc() crashes with
# KeyError if missing. `name` and `leaf_type` are needed for tech-fingerprint
# matching to actually fire the playbook.
_PLAYBOOK_REQUIRED_FM: tuple[str, ...] = ("playbook", "name", "leaf_type")
_CHECK_REQUIRED_KEYS: tuple[str, ...] = ("id", "name", "mode", "category", "severity")
# Modes whose checks are routed through Intruder and therefore need at least
# one mutation that actually mutates something (body/header+value/url_override).
# `analytical` and `idea` are exempt — Intruder doesn't run those.
_MUTATION_REQUIRED_MODES: frozenset[str] = frozenset({"deterministic", "oob"})
# A mutation is "real" when it carries at least one of these payload-bearing
# keys. `note` and `value` (without `header`) alone don't qualify — `value`
# without `header` has nothing to attach to and is silently dropped.
_MUTATION_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"body", "header", "url_override"}
)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def validate_armory_node(state: FactoryState) -> dict:
    """Deterministic quality gate for armory artifact PRs.

    Reads ``artifact_type`` from state.  If not an armory type, returns
    ``graph_status="armory_validated"`` immediately (pass-through).

    For armory types, inspects each PM-approved subtask's PR files via the
    GitHub API and runs the appropriate checks.  Subtasks that fail validation
    are re-queued for grinding (``status="changes_requested"``).

    Args:
        state: Current FactoryState.

    Returns:
        Partial state update with ``graph_status`` and (on failure) updated
        ``subtasks`` list.
    """
    artifact_type: str | None = state.get("artifact_type")

    if artifact_type not in ARMORY_TYPES:
        logger.debug(
            "validate_armory: artifact_type=%r is not an armory type — pass-through",
            artifact_type,
        )
        return {"graph_status": "armory_validated"}

    task_repo: str = state.get("task_repo") or get_settings().github_repo
    subtasks: list[SubTask] = list(state.get("subtasks") or [])

    updated_subtasks: list[SubTask] = []
    any_failed = False

    async with GitHubClient(repo=task_repo) as github_client:
        for subtask in subtasks:
            if subtask["status"] != "merged":
                updated_subtasks.append(subtask)
                continue

            pr_number: int | None = subtask.get("pr_number") or _extract_pr_number(
                subtask.get("pr_url") or ""
            )
            if not pr_number:
                logger.warning(
                    "validate_armory: no PR number for subtask %s — skipping",
                    subtask["id"],
                )
                updated_subtasks.append(subtask)
                continue

            try:
                pr_files = await github_client.get_pr_files(pr_number)
            except Exception as exc:
                logger.warning(
                    "validate_armory: failed to fetch PR #%d files for subtask %s: %s",
                    pr_number,
                    subtask["id"],
                    exc,
                )
                updated_subtasks.append(subtask)
                continue

            errors = validate_armory_pr_files(pr_files, artifact_type)

            if errors:
                any_failed = True
                feedback = "Armory validation failed:\n" + "\n".join(
                    f"- {e}" for e in errors
                )
                logger.info(
                    "validate_armory: subtask %s PR #%d failed validation: %s",
                    subtask["id"],
                    pr_number,
                    feedback,
                )
                updated: SubTask = SubTask(
                    **{
                        **subtask,
                        "status": "changes_requested",
                        "review_feedback": feedback,
                        "attempt": subtask.get("attempt", 0) + 1,
                    }
                )
                try:
                    await github_client.request_changes(pr_number, feedback)
                except Exception as exc:
                    logger.warning(
                        "validate_armory: failed to post review on PR #%d: %s",
                        pr_number,
                        exc,
                    )
                updated_subtasks.append(updated)
            else:
                logger.info(
                    "validate_armory: subtask %s PR #%d passed validation",
                    subtask["id"],
                    pr_number,
                )
                updated_subtasks.append(subtask)

    if any_failed:
        return {
            "subtasks": updated_subtasks,
            "graph_status": "armory_validation_failed",
        }
    return {"graph_status": "armory_validated"}


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------


def validate_armory_pr_files(
    pr_files: list[dict], artifact_type: str
) -> list[str]:
    """Run the armory schema/safety checks against a PR's changed files.

    Public helper used by both ``validate_armory_node`` (post-merge gate)
    and ``pm_review_node`` (pre-merge gate) so a single source of truth
    decides what counts as a structurally-broken artifact.

    Args:
        pr_files: List of PR file objects from the GitHub API
            (``filename`` and ``patch`` keys).
        artifact_type: One of ``"tool"``, ``"playbook"``, ``"wordlist"``.
            Anything else returns an empty list.

    Returns:
        List of human-readable error strings; empty means the PR passes.
    """
    if artifact_type == "tool":
        return _check_tool_files(pr_files)
    if artifact_type == "playbook":
        return _check_playbook_files(pr_files)
    return []  # wordlist + non-armory: no structural checks


def _check_tool_files(pr_files: list[dict]) -> list[str]:
    """Check added Python lines for forbidden network-I/O imports.

    Args:
        pr_files: List of PR file objects from the GitHub API.

    Returns:
        List of error strings (empty means all clear).
    """
    errors: list[str] = []
    seen: set[str] = set()  # (filename, import) dedup

    for f in pr_files:
        filename: str = f.get("filename", "")
        if not filename.endswith(".py"):
            continue
        patch: str = f.get("patch", "") or ""
        for raw_line in patch.splitlines():
            if not raw_line.startswith("+") or raw_line.startswith("+++"):
                continue
            line = raw_line[1:].strip()
            for bad in FORBIDDEN_IMPORTS:
                if _matches_forbidden_import(line, bad):
                    key = (filename, bad)
                    if key not in seen:
                        seen.add(key)
                        errors.append(
                            f"`{filename}`: forbidden import `{bad}` — "
                            "use the injected `client` parameter for HTTP instead"
                        )
                    break

    return errors


def _matches_forbidden_import(line: str, module: str) -> bool:
    """Return True if *line* is an import statement for *module*.

    Matches both ``import urllib`` / ``import urllib.request`` and
    ``from urllib import ...`` / ``from urllib.request import ...``.

    Args:
        line: A single stripped Python source line.
        module: Module name to check (e.g. ``"urllib"``).

    Returns:
        True if the line imports the given module.
    """
    escaped = re.escape(module)
    return bool(
        re.match(rf"^import\s+{escaped}(\s|$|\.)", line)
        or re.match(rf"^from\s+{escaped}(\s|\.|$)", line)
    )


def _check_playbook_files(pr_files: list[dict]) -> list[str]:
    """Validate YAML syntax and required fields in changed playbook files.

    Args:
        pr_files: List of PR file objects from the GitHub API.

    Returns:
        List of error strings (empty means all clear).
    """
    errors: list[str] = []

    for f in pr_files:
        filename: str = f.get("filename", "")
        if not any(filename.endswith(ext) for ext in (".md", ".yaml", ".yml")):
            continue

        patch: str = f.get("patch", "") or ""
        added_lines = [
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        added_text = "\n".join(added_lines)

        # Validate all fenced YAML blocks (```yaml ... ```) in added text.
        for block in re.findall(r"```ya?ml\n(.*?)```", added_text, re.DOTALL):
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError as exc:
                errors.append(f"`{filename}`: invalid YAML block — {exc}")
                continue

            # If the block looks like a checks list, validate each check entry.
            if isinstance(data, dict) and "checks" in data:
                errs = _validate_checks(filename, data["checks"])
                errors.extend(errs)

        # Validate YAML frontmatter in Markdown files (--- ... ---).
        if filename.endswith(".md"):
            fm_match = re.match(r"^---\n(.*?)^---", added_text, re.DOTALL | re.MULTILINE)
            if fm_match:
                try:
                    fm = yaml.safe_load(fm_match.group(1)) or {}
                except yaml.YAMLError as exc:
                    errors.append(f"`{filename}`: invalid frontmatter YAML — {exc}")
                    fm = {}

                if isinstance(fm, dict):
                    for field in _PLAYBOOK_REQUIRED_FM:
                        if field not in fm:
                            errors.append(
                                f"`{filename}`: missing required frontmatter field `{field}`"
                            )
                    # Real playbooks put `checks:` IN the frontmatter, not a
                    # fenced block. Validate them with the same rules.
                    if "checks" in fm:
                        errors.extend(_validate_checks(filename, fm["checks"]))

    return errors


def _validate_checks(filename: str, checks: object) -> list[str]:
    """Validate a ``checks:`` list from a playbook YAML block.

    Args:
        filename: Source file name (for error messages).
        checks: Parsed value of the ``checks`` key (should be a list).

    Returns:
        List of error strings.
    """
    errors: list[str] = []

    if not isinstance(checks, list):
        return [f"`{filename}`: `checks` must be a list, got {type(checks).__name__}"]

    if not checks:
        return [f"`{filename}`: `checks` list must not be empty"]

    for idx, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"`{filename}`: check[{idx}] must be a mapping")
            continue
        for key in _CHECK_REQUIRED_KEYS:
            if key not in check:
                errors.append(
                    f"`{filename}`: check[{idx}] missing required field `{key}`"
                )
        mode = check.get("mode")
        if mode and mode not in _VALID_MODES:
            errors.append(
                f"`{filename}`: check[{idx}] invalid mode `{mode}` "
                f"(must be one of: {', '.join(sorted(_VALID_MODES))})"
            )
        severity = check.get("severity")
        if severity and severity not in _VALID_SEVERITIES:
            errors.append(
                f"`{filename}`: check[{idx}] invalid severity `{severity}` "
                f"(must be one of: {', '.join(sorted(_VALID_SEVERITIES))})"
            )

        # Mutation shape — every entry under `mutations:` must be a mapping
        # ({header, value, body, url_override, note} keys recognized by
        # PlaybookMutation.from_dict). Bare strings get silently dropped at
        # load time and the check no-ops at runtime.
        #
        # For deterministic/oob modes we additionally require at least one
        # mutation to actually mutate something — i.e. carry at least one of
        # body/header/url_override (note alone is just a comment; a check
        # whose mutations are all "- note: baseline X" sends only the
        # baseline request and Intruder has nothing to compare against, so
        # the check no-ops at runtime). Analytical/idea modes are exempt
        # because Intruder doesn't run them; the brain LLM can use note-only
        # entries as inline reasoning hooks.
        muts = check.get("mutations")
        if muts is not None:
            if not isinstance(muts, list):
                errors.append(
                    f"`{filename}`: check[{idx}] `mutations` must be a list, "
                    f"got {type(muts).__name__}"
                )
            else:
                any_real_mutation = False
                for mi, m in enumerate(muts):
                    if not isinstance(m, dict):
                        errors.append(
                            f"`{filename}`: check[{idx}].mutations[{mi}] must be a "
                            f"mapping with keys like body/header/value/url_override/note, "
                            f"got {type(m).__name__}: {str(m)[:60]}"
                        )
                        continue
                    if any(k in m for k in _MUTATION_PAYLOAD_KEYS):
                        any_real_mutation = True
                if (
                    mode in _MUTATION_REQUIRED_MODES
                    and muts  # non-empty list
                    and not any_real_mutation
                ):
                    errors.append(
                        f"`{filename}`: check[{idx}] mode={mode!r} but no mutation "
                        f"carries a payload — every entry only has `note:` (or other "
                        f"non-payload keys). Add at least one mutation with `body:`, "
                        f"`header:` (with `value:`), or `url_override:` so Intruder "
                        f"has something to send beyond the baseline request."
                    )

    return errors
