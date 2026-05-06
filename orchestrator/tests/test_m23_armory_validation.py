"""Tests for M23: Armory grinder prompts + validation node.

Covers:
- armory_prompts module exports (FORBIDDEN_IMPORTS, get_armory_prompt)
- _check_tool_files — forbidden import detection in patch lines
- _check_playbook_files — YAML syntax and schema validation
- validate_armory_node — pass-through for non-armory, gate for armory types
- route_after_validate_armory — routing logic
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.armory_prompts import (
    FORBIDDEN_IMPORTS,
    get_armory_prompt,
)
from factory.nodes.validate_armory import (
    _check_playbook_files,
    _check_tool_files,
    _matches_forbidden_import,
    validate_armory_node,
)


# ---------------------------------------------------------------------------
# armory_prompts
# ---------------------------------------------------------------------------


def test_forbidden_imports_contains_expected_modules() -> None:
    for mod in ("urllib", "requests", "httpx", "aiohttp", "socket", "http.client"):
        assert mod in FORBIDDEN_IMPORTS


def test_get_armory_prompt_none_returns_empty() -> None:
    assert get_armory_prompt(None) == ""


def test_get_armory_prompt_code_returns_empty() -> None:
    assert get_armory_prompt("code") == ""


def test_get_armory_prompt_tool_mentions_toolbase() -> None:
    prompt = get_armory_prompt("tool")
    assert "ToolBase" in prompt
    assert "capability_name" in prompt


def test_get_armory_prompt_playbook_mentions_checks() -> None:
    prompt = get_armory_prompt("playbook")
    assert "checks" in prompt
    assert "leaf_type" in prompt


def test_get_armory_prompt_wordlist_mentions_format() -> None:
    prompt = get_armory_prompt("wordlist")
    assert "one entry per line" in prompt or "per line" in prompt


def test_get_armory_prompt_unknown_returns_empty() -> None:
    assert get_armory_prompt("unknown_future_type") == ""


# ---------------------------------------------------------------------------
# _matches_forbidden_import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,module,expected",
    [
        ("import urllib", "urllib", True),
        ("import urllib.request", "urllib", True),
        ("from urllib import request", "urllib", True),
        ("from urllib.request import urlopen", "urllib", True),
        ("import requests", "requests", True),
        ("from requests import Session", "requests", True),
        ("import httpx", "httpx", True),
        ("import aiohttp", "aiohttp", True),
        ("import socket", "socket", True),
        ("from http.client import HTTPConnection", "http.client", True),
        # Non-matches
        ("import os", "urllib", False),
        ("# import urllib", "urllib", False),
        ("my_urllib = something()", "urllib", False),
        ("import urllib_parse_something", "urllib", False),  # different module
    ],
)
def test_matches_forbidden_import(line: str, module: str, expected: bool) -> None:
    assert _matches_forbidden_import(line, module) == expected


# ---------------------------------------------------------------------------
# _check_tool_files
# ---------------------------------------------------------------------------


def _make_file(filename: str, patch: str) -> dict:
    return {"filename": filename, "patch": patch, "status": "added"}


def test_check_tool_files_clean_returns_empty() -> None:
    files = [
        _make_file(
            "python/molly/tools/my_tool.py",
            "+from molly.tools.base import ToolBase, ToolError\n+import json\n",
        )
    ]
    assert _check_tool_files(files) == []


def test_check_tool_files_detects_urllib() -> None:
    files = [
        _make_file(
            "python/molly/tools/my_tool.py",
            "+import urllib.request\n",
        )
    ]
    errors = _check_tool_files(files)
    assert len(errors) == 1
    assert "urllib" in errors[0]


def test_check_tool_files_detects_requests() -> None:
    files = [
        _make_file("tool.py", "+import requests\n"),
    ]
    errors = _check_tool_files(files)
    assert any("requests" in e for e in errors)


def test_check_tool_files_detects_from_httpx() -> None:
    files = [_make_file("tool.py", "+from httpx import AsyncClient\n")]
    errors = _check_tool_files(files)
    assert any("httpx" in e for e in errors)


def test_check_tool_files_ignores_removed_lines() -> None:
    # Lines starting with '-' are removals — should not trigger.
    files = [_make_file("tool.py", "-import requests  # old code removed\n")]
    assert _check_tool_files(files) == []


def test_check_tool_files_ignores_non_python_files() -> None:
    files = [_make_file("README.md", "+import requests\n")]
    assert _check_tool_files(files) == []


def test_check_tool_files_deduplicates_same_import() -> None:
    # Two added lines importing the same forbidden module → one error.
    files = [
        _make_file(
            "tool.py",
            "+import urllib\n+from urllib.parse import urlencode\n",
        )
    ]
    errors = _check_tool_files(files)
    assert len([e for e in errors if "urllib" in e]) == 1


# ---------------------------------------------------------------------------
# _check_playbook_files
# ---------------------------------------------------------------------------

_VALID_CHECKS_YAML = """\
```yaml
checks:
  - id: test_check
    name: Test Check
    mode: deterministic
    category: auth
    severity: high
```
"""

_INVALID_YAML_BLOCK = """\
```yaml
checks:
  - id: [unclosed bracket
```
"""

_VALID_FRONTMATTER = """\
---
playbook: my-playbook
name: My Playbook
leaf_type: auth_endpoint
applies_to:
  - auth
---
"""

_MISSING_LEAF_TYPE_FM = """\
---
playbook: my-playbook
name: My Playbook
---
"""


def _make_md_file(filename: str, added_content: str) -> dict:
    patch = "\n".join(f"+{line}" for line in added_content.splitlines())
    return {"filename": filename, "patch": patch, "status": "added"}


def test_check_playbook_files_valid_returns_empty() -> None:
    files = [_make_md_file("playbooks/test.md", _VALID_FRONTMATTER + _VALID_CHECKS_YAML)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_invalid_yaml_block() -> None:
    files = [_make_md_file("playbooks/bad.md", _INVALID_YAML_BLOCK)]
    errors = _check_playbook_files(files)
    assert any("invalid YAML" in e for e in errors)


def test_check_playbook_files_missing_leaf_type() -> None:
    files = [_make_md_file("playbooks/test.md", _MISSING_LEAF_TYPE_FM + _VALID_CHECKS_YAML)]
    errors = _check_playbook_files(files)
    assert any("leaf_type" in e for e in errors)


def test_check_playbook_files_check_missing_mode() -> None:
    bad_checks = """\
```yaml
checks:
  - id: test_check
    name: Test Check
    category: auth
    severity: high
```
"""
    files = [_make_md_file("playbooks/test.md", bad_checks)]
    errors = _check_playbook_files(files)
    assert any("mode" in e for e in errors)


def test_check_playbook_files_invalid_mode() -> None:
    bad_checks = """\
```yaml
checks:
  - id: test_check
    name: Test Check
    mode: invalid_mode
    category: auth
    severity: high
```
"""
    files = [_make_md_file("playbooks/test.md", bad_checks)]
    errors = _check_playbook_files(files)
    assert any("mode" in e for e in errors)


def test_check_playbook_files_invalid_severity() -> None:
    bad_checks = """\
```yaml
checks:
  - id: test_check
    name: Test Check
    mode: deterministic
    category: auth
    severity: extreme
```
"""
    files = [_make_md_file("playbooks/test.md", bad_checks)]
    errors = _check_playbook_files(files)
    assert any("severity" in e for e in errors)


def test_check_playbook_files_empty_checks_list() -> None:
    empty = """\
```yaml
checks: []
```
"""
    files = [_make_md_file("playbooks/test.md", empty)]
    errors = _check_playbook_files(files)
    assert any("empty" in e for e in errors)


def test_check_playbook_files_ignores_non_playbook_files() -> None:
    files = [_make_file("python/tool.py", "+import yaml\n")]
    assert _check_playbook_files(files) == []


# ---------------------------------------------------------------------------
# Regression: actual crash modes that have hit Molly in production.
# ---------------------------------------------------------------------------

# Production-shape playbook — has `checks:` IN the frontmatter, not in a
# fenced ```yaml block. Real Molly playbooks all look like this.
_FRONTMATTER_WITH_CHECKS = """\
---
playbook: my-playbook
name: My Playbook
leaf_type: rest_api
applies_to:
  - rest
checks:
  - id: test_check
    name: Test Check
    mode: deterministic
    category: auth
    severity: high
---
"""


def test_check_playbook_files_missing_playbook_key_caught() -> None:
    """Reproduces the May 2026 crash: Factory PRs landed without a top-level
    `playbook:` key, causing PlaybookLoader.from_doc() to raise KeyError on
    Molly startup. The validator must catch this BEFORE merge."""
    bad_fm = """\
---
id: my-playbook
name: My Playbook
leaf_type: rest_api
---
"""
    files = [_make_md_file("playbooks/test.md", bad_fm + _VALID_CHECKS_YAML)]
    errors = _check_playbook_files(files)
    assert any("`playbook`" in e for e in errors), errors


def test_check_playbook_files_validates_frontmatter_checks() -> None:
    """Real playbooks put `checks:` in the frontmatter (between --- markers),
    not in a fenced yaml block. The validator must apply the same per-check
    rules to frontmatter checks. Previously skipped — invalid checks landed."""
    bad_fm_checks = """\
---
playbook: my-playbook
name: My Playbook
leaf_type: rest_api
checks:
  - id: bad
    name: Bad
    mode: NotARealMode
    category: auth
    severity: high
---
"""
    files = [_make_md_file("playbooks/test.md", bad_fm_checks)]
    errors = _check_playbook_files(files)
    assert any("mode" in e and "NotARealMode" in e for e in errors), errors


def test_check_playbook_files_frontmatter_checks_missing_required() -> None:
    bad_fm = """\
---
playbook: my-playbook
name: My Playbook
leaf_type: rest_api
checks:
  - id: c1
    name: C1
    mode: deterministic
    severity: high
---
"""
    files = [_make_md_file("playbooks/test.md", bad_fm)]
    errors = _check_playbook_files(files)
    assert any("category" in e for e in errors), errors


def test_check_playbook_files_accepts_real_playbook_shape() -> None:
    """The shape every real Molly playbook uses must pass cleanly."""
    files = [_make_md_file("playbooks/test.md", _FRONTMATTER_WITH_CHECKS)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_accepts_idea_mode() -> None:
    """`idea` is a valid mode — dormant checks promoted during research."""
    fm = """\
---
playbook: ideas-only
name: Ideas
leaf_type: rest_api
checks:
  - id: dormant1
    name: Dormant
    mode: idea
    category: ssrf
    severity: medium
---
"""
    files = [_make_md_file("playbooks/test.md", fm)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_rejects_note_only_mutations_for_deterministic() -> None:
    """A deterministic check whose mutations are all note-only no-ops at
    runtime: Intruder fires the baseline and has nothing to compare it
    against. Reject so the grinder learns to add a real payload."""
    fm = """\
---
playbook: baseline-only
name: Baseline-only
leaf_type: rest_api
checks:
  - id: c1
    name: c1
    mode: deterministic
    category: idor-enum
    severity: medium
    mutations:
      - note: baseline request 1
      - note: baseline request 2
---
"""
    files = [_make_md_file("playbooks/baseline-only.md", fm)]
    errors = _check_playbook_files(files)
    assert errors, "expected at least one validation error"
    assert any("no mutation carries a payload" in e for e in errors), errors


def test_check_playbook_files_rejects_note_only_mutations_for_oob() -> None:
    """oob mode also routes through Intruder — note-only is equally broken
    there. The {{OOB_URL}} substitution needs a body/header/url_override
    string to live in."""
    fm = """\
---
playbook: oob-noop
name: OOB no-op
leaf_type: rest_api
checks:
  - id: c1
    name: c1
    mode: oob
    category: ssrf
    severity: high
    requires_capabilities:
      - oob_callback
    mutations:
      - note: would callback {{OOB_URL}} if we had a body
---
"""
    files = [_make_md_file("playbooks/oob-noop.md", fm)]
    errors = _check_playbook_files(files)
    assert any("no mutation carries a payload" in e for e in errors), errors


def test_check_playbook_files_accepts_note_only_mutations_for_analytical() -> None:
    """Analytical checks are driven by the brain LLM, not Intruder.
    note-only mutations are a legitimate way to leave inline reasoning
    hooks for the LLM to pick up."""
    fm = """\
---
playbook: analytical-notes
name: Analytical notes
leaf_type: rest_api
checks:
  - id: c1
    name: c1
    mode: analytical
    category: business-logic
    severity: medium
    mutations:
      - note: consider what happens when X
      - note: also probe Y
---
"""
    files = [_make_md_file("playbooks/analytical-notes.md", fm)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_accepts_mixed_mutations_with_one_real_payload() -> None:
    """At least one real mutation per check is enough — the rest can be
    note-only commentary."""
    fm = """\
---
playbook: mixed
name: Mixed
leaf_type: rest_api
checks:
  - id: c1
    name: c1
    mode: deterministic
    category: ssrf
    severity: high
    mutations:
      - note: baseline (no body)
      - body: '{"url": "http://169.254.169.254/"}'
        note: AWS IMDS attempt
---
"""
    files = [_make_md_file("playbooks/mixed.md", fm)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_accepts_url_override_only_mutation() -> None:
    """A url_override-only mutation is a real mutation (replaces the URL)."""
    fm = """\
---
playbook: url-override
name: URL override
leaf_type: rest_api
checks:
  - id: c1
    name: c1
    mode: deterministic
    category: ssrf
    severity: high
    mutations:
      - url_override: https://attacker.example/probe
        note: redirect target
---
"""
    files = [_make_md_file("playbooks/url-override.md", fm)]
    assert _check_playbook_files(files) == []


def test_check_playbook_files_accepts_unknown_severity() -> None:
    """Several factory-generated playbooks use severity: unknown — that's
    allowed by the underlying loader (defaults to "unknown") so the
    validator should accept it too."""
    fm = """\
---
playbook: legacy
name: Legacy
leaf_type: rest_api
checks:
  - id: c1
    name: C1
    mode: analytical
    category: misc
    severity: unknown
---
"""
    files = [_make_md_file("playbooks/test.md", fm)]
    assert _check_playbook_files(files) == []


# ---------------------------------------------------------------------------
# validate_armory_node — pass-through for non-armory
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> dict:
    defaults: dict = {
        "thread_id": "task-0099",
        "task_wiki_page": "Task_0099_test",
        "title": "",
        "requirements": "",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": [],
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "incremental_costs_usd": [],
        "graph_status": "reviewing",
        "error": None,
        "escalation_decision": None,
        "artifact_type": None,
        "task_repo_root": None,
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.asyncio
async def test_validate_armory_pass_through_for_code() -> None:
    state = _make_state(artifact_type=None)
    result = await validate_armory_node(state)
    assert result.get("graph_status") == "armory_validated"


@pytest.mark.asyncio
async def test_validate_armory_pass_through_for_explicit_code() -> None:
    state = _make_state(artifact_type="code")
    result = await validate_armory_node(state)
    assert result.get("graph_status") == "armory_validated"


# ---------------------------------------------------------------------------
# validate_armory_node — tool validation via mocked GitHub API
# ---------------------------------------------------------------------------


def _make_subtask(subtask_id: str, status: str = "merged", pr_url: str = "https://github.com/owner/repo/pull/42") -> dict:
    return {
        "id": subtask_id,
        "wiki_page": subtask_id,
        "parent_task": "Task_0099",
        "title": "Add tool",
        "description": "",
        "status": status,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": [],
        "acceptance_criteria": [],
        "token_budget": 50000,
        "tokens_used": 0,
        "assigned_grinder": None,
        "branch_name": "factory/task-0099",
        "pr_url": pr_url,
        "pr_number": 42,
        "review_feedback": None,
        "code_skeleton": None,
    }


def _mock_github(pr_files: list[dict]) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get_pr_files = AsyncMock(return_value=pr_files)
    mock_client.request_changes = AsyncMock(return_value={})
    return mock_client


@pytest.mark.asyncio
async def test_validate_armory_tool_clean_passes() -> None:
    state = _make_state(
        artifact_type="tool",
        task_repo="jyrkihuhta/molly-armory",
        subtasks=[_make_subtask("task-0099")],
    )
    clean_files = [_make_file("python/molly/tools/my_tool.py", "+from molly.tools.base import ToolBase\n")]
    mock_gh = _mock_github(clean_files)

    with patch("factory.nodes.validate_armory.GitHubClient", return_value=mock_gh):
        result = await validate_armory_node(state)

    assert result.get("graph_status") == "armory_validated"
    assert "subtasks" not in result


@pytest.mark.asyncio
async def test_validate_armory_tool_forbidden_import_fails() -> None:
    state = _make_state(
        artifact_type="tool",
        task_repo="jyrkihuhta/molly-armory",
        subtasks=[_make_subtask("task-0099")],
    )
    bad_files = [_make_file("python/molly/tools/my_tool.py", "+import requests\n")]
    mock_gh = _mock_github(bad_files)

    with patch("factory.nodes.validate_armory.GitHubClient", return_value=mock_gh):
        result = await validate_armory_node(state)

    assert result.get("graph_status") == "armory_validation_failed"
    updated = result["subtasks"]
    assert updated[0]["status"] == "changes_requested"
    assert "requests" in (updated[0]["review_feedback"] or "")


@pytest.mark.asyncio
async def test_validate_armory_skips_non_merged_subtasks() -> None:
    state = _make_state(
        artifact_type="tool",
        task_repo="jyrkihuhta/molly-armory",
        subtasks=[_make_subtask("task-0099", status="failed")],
    )
    mock_gh = _mock_github([])

    with patch("factory.nodes.validate_armory.GitHubClient", return_value=mock_gh):
        result = await validate_armory_node(state)

    # Non-merged subtasks are skipped; no PR files fetched, passes through.
    assert result.get("graph_status") == "armory_validated"
    mock_gh.get_pr_files.assert_not_called()


@pytest.mark.asyncio
async def test_validate_armory_wordlist_always_passes() -> None:
    state = _make_state(
        artifact_type="wordlist",
        task_repo="jyrkihuhta/molly-armory",
        subtasks=[_make_subtask("task-0099")],
    )
    txt_files = [_make_file("wordlists/paths.txt", "+.env\n+.git/config\n")]
    mock_gh = _mock_github(txt_files)

    with patch("factory.nodes.validate_armory.GitHubClient", return_value=mock_gh):
        result = await validate_armory_node(state)

    assert result.get("graph_status") == "armory_validated"


# ---------------------------------------------------------------------------
# route_after_validate_armory
# ---------------------------------------------------------------------------


def test_route_after_validate_armory_passed_auto_merge_false() -> None:
    from factory.graph import route_after_validate_armory

    state = _make_state(graph_status="armory_validated")
    with patch("factory.graph.get_settings") as mock_settings:
        s = AsyncMock()
        s.auto_merge = False
        mock_settings.return_value = s
        result = route_after_validate_armory(state)

    assert result == "all_approved"


def test_route_after_validate_armory_passed_auto_merge_true() -> None:
    from factory.graph import route_after_validate_armory

    state = _make_state(graph_status="armory_validated")
    with patch("factory.graph.get_settings") as mock_settings:
        s = AsyncMock()
        s.auto_merge = True
        mock_settings.return_value = s
        result = route_after_validate_armory(state)

    assert result == "skip_human_review"


def test_route_after_validate_armory_failed_routes_to_grind() -> None:
    from langgraph.types import Send

    from factory.graph import route_after_validate_armory

    subtask = _make_subtask("task-0099", status="changes_requested")
    state = _make_state(
        graph_status="armory_validation_failed",
        subtasks=[subtask],
    )
    with patch("factory.graph.get_settings"):
        result = route_after_validate_armory(state)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Send)
    assert result[0].node == "grind"


def test_route_after_validate_armory_failed_no_changes_requested_escalates() -> None:
    from factory.graph import route_after_validate_armory

    state = _make_state(
        graph_status="armory_validation_failed",
        subtasks=[_make_subtask("task-0099", status="merged")],  # no changes_requested
    )
    with patch("factory.graph.get_settings"):
        result = route_after_validate_armory(state)

    assert result == "escalate"
