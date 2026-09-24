"""Tests for the class-gap-researcher bot.

The bot does I/O (GitHub, MeshWiki, LLM), so the tests focus on the pure
helpers — page rendering, suggestion parsing, dedup logic — plus a full
run() flow with all I/O mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.class_gap_researcher import (
    KIND_GENERIC,
    KIND_TARGET_SPECIFIC,
    KIND_TOOL_IDEA,
    ClassGapResearcherBot,
    _parse_suggestions,
    _render_task_page,
    _slug,
)


# ---- pure helpers ----------------------------------------------------


def test_slug_kebab_case():
    assert _slug("Cross-Site Request Forgery") == "cross-site-request-forgery"
    assert _slug("JWT_Claim_Confusion") == "jwt-claim-confusion"
    assert _slug("-leading-trailing-") == "leading-trailing"


def test_render_task_page_generic_includes_required_frontmatter():
    suggestion = {
        "kind": KIND_GENERIC,
        "target": "dashql",
        "vuln_class": "host-header-injection",
        "title": "Host header injection on /api/v1/orders",
        "rationale": "Common bug on e-commerce APIs that build links from Host.",
        "test_surface": "Send altered Host: header to /fi/fi/api/v1/orders.",
    }
    name, content = _render_task_page(
        suggestion, base_url="http://dashql:5000", armory_repo="x/molly-armory",
    )
    assert name.startswith("Task_Playbook_dashql_host_header_injection_")
    for key in (
        "type: task",
        "assignee: factory",
        "status: planned",
        "skip_decomposition: true",
        "artifact_type: playbook",
        "repo: x/molly-armory",
    ):
        assert key in content, f"missing frontmatter: {key}"
    # Rationale + test_surface are echoed into body
    assert "Common bug on e-commerce APIs" in content
    assert "altered Host:" in content
    # Generic scope requirement is called out in the deliverable
    assert "scope: generic" in content
    assert "scope: target-specific" not in content


def test_render_task_page_target_specific_requires_target_field():
    suggestion = {
        "kind": KIND_TARGET_SPECIFIC,
        "target": "jwtlab",
        "vuln_class": "custom-kid-header-quirk",
        "title": "jwtlab-specific kid header quirk",
        "rationale": "jwtlab's fork of the JWT lib mishandles kid.",
        "test_surface": "Send a crafted kid header to /auth/verify.",
    }
    name, content = _render_task_page(
        suggestion, base_url="http://jwtlab:5056", armory_repo="x/molly-armory",
    )
    assert name.startswith("Task_Playbook_jwtlab_custom_kid_header_quirk_")
    assert "scope: target-specific" in content
    assert "target: jwtlab" in content


def test_render_task_page_tool_idea_produces_toolspec_task():
    suggestion = {
        "kind": KIND_TOOL_IDEA,
        "capability_name": "jwt_confusion_forge",
        "title": "Forge JWTs with attacker-controlled algorithm",
        "category": "auth-bypass",
        "rationale": "No existing capability can emit alg:none or key-confused tokens.",
        "design_sketch": "Given a JWT + public key, emit alg:none and RS256->HS256 variants.",
    }
    name, content = _render_task_page(
        suggestion, base_url="", armory_repo="x/molly-armory",
    )
    assert name.startswith("Task_Toolspec_jwt_confusion_forge_")
    for key in (
        "type: task",
        "assignee: factory",
        "status: planned",
        "skip_decomposition: true",
        "artifact_type: toolspec",
        "repo_root: toolspecs",
        "repo: x/molly-armory",
    ):
        assert key in content, f"missing frontmatter: {key}"
    assert "No existing capability can emit" in content
    assert "alg:none and RS256->HS256" in content
    assert "No Python implementation included" in content


# ---- LLM-output parser -----------------------------------------------


def test_parse_suggestions_plain_json():
    out = """[
      {"kind":"generic","target":"dashql","vuln_class":"host-header","title":"X","rationale":"y","test_surface":"z"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1
    assert parsed[0]["target"] == "dashql"


def test_parse_suggestions_defaults_missing_kind_to_generic():
    out = '[{"target":"notekeeper","vuln_class":"x","title":"y"}]'
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1
    assert parsed[0]["kind"] == KIND_GENERIC


def test_parse_suggestions_with_markdown_fence():
    out = "```json\n[{\"kind\":\"generic\",\"target\":\"notekeeper\",\"vuln_class\":\"x\",\"title\":\"y\"}]\n```"
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1
    assert parsed[0]["target"] == "notekeeper"


def test_parse_suggestions_drops_invalid_target():
    out = """[
      {"kind":"generic","target":"ssrfbox","vuln_class":"a","title":"good"},
      {"kind":"generic","target":"out-of-scope","vuln_class":"b","title":"bad"},
      {"kind":"generic","target":"notekeeper","vuln_class":"c","title":"also good"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert [p["target"] for p in parsed] == ["ssrfbox", "notekeeper"]


def test_parse_suggestions_drops_missing_keys():
    out = """[
      {"kind":"generic","target":"ssrfbox","vuln_class":"a","title":"good"},
      {"kind":"generic","target":"ssrfbox","title":"missing vuln_class"},
      {"kind":"generic","vuln_class":"c","title":"missing target"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1


def test_parse_suggestions_respects_cap():
    out = """[
      {"kind":"generic","target":"ssrfbox","vuln_class":"a","title":"1"},
      {"kind":"generic","target":"ssrfbox","vuln_class":"b","title":"2"},
      {"kind":"generic","target":"ssrfbox","vuln_class":"c","title":"3"},
      {"kind":"generic","target":"ssrfbox","vuln_class":"d","title":"4"}
    ]"""
    parsed = _parse_suggestions(out, cap=2, allow_target_specific=False)
    assert len(parsed) == 2


def test_parse_suggestions_handles_garbage():
    assert _parse_suggestions("not json at all", cap=5, allow_target_specific=False) == []
    assert _parse_suggestions("", cap=5, allow_target_specific=False) == []
    assert _parse_suggestions("[invalid json]", cap=5, allow_target_specific=False) == []
    # Top-level object instead of array
    assert _parse_suggestions('{"target":"ssrfbox"}', cap=5, allow_target_specific=False) == []


def test_parse_suggestions_extracts_array_amid_prose():
    out = (
        "Here are my suggestions for the team:\n\n"
        '[{"kind":"generic","target":"dashql","vuln_class":"x","title":"y"}]\n\n'
        "Hope that helps!"
    )
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1


def test_parse_suggestions_drops_target_specific_when_disallowed():
    out = """[
      {"kind":"generic","target":"ssrfbox","vuln_class":"a","title":"kept"},
      {"kind":"target_specific","target":"jwtlab","vuln_class":"quirk","title":"dropped"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "kept"


def test_parse_suggestions_keeps_target_specific_when_allowed():
    out = """[
      {"kind":"target_specific","target":"jwtlab","vuln_class":"quirk","title":"kept"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=True)
    assert len(parsed) == 1
    assert parsed[0]["kind"] == KIND_TARGET_SPECIFIC


def test_parse_suggestions_tool_idea_requires_capability_name():
    out = """[
      {"kind":"tool_idea","title":"missing capability_name"},
      {"kind":"tool_idea","capability_name":"jwt_confusion_forge","title":"good"}
    ]"""
    parsed = _parse_suggestions(out, cap=5, allow_target_specific=False)
    assert len(parsed) == 1
    assert parsed[0]["capability_name"] == "jwt_confusion_forge"


# ---- run() integration with all I/O mocked ---------------------------


def _mock_wiki_client() -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.create_page = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_run_deduplicates_against_existing_and_open():
    """Suggestions matching either existing armory or open tasks should be filtered out."""
    bot = ClassGapResearcherBot(
        interval_seconds=1, suggestions_per_run=3, model="MiniMax-M2.7",
    )

    bot._fetch_armory_classes = AsyncMock(return_value=(
        {("dashql", "cache-deception"), ("ssrfbox", "xxe-blind-oob")},
        set(),
    ))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[
        {"kind": KIND_GENERIC, "target": "notekeeper", "vuln_class": "prototype-pollution",
         "page": "Task_Playbook_notekeeper_prototype_pollution_0513"},
    ])
    # LLM returns 3 suggestions, two of which collide with existing/open
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"target_specific","target":"dashql","vuln_class":"cache-deception","title":"DUPE armory"},'
        '{"kind":"generic","target":"notekeeper","vuln_class":"prototype-pollution","title":"DUPE task"},'
        '{"kind":"generic","target":"ssrfbox","vuln_class":"smuggling","title":"NEW one",'
        '"rationale":"because","test_surface":"endpoint"}]',
        0,
    ))

    mock_client = _mock_wiki_client()
    with patch(
        "factory.bots.class_gap_researcher.MeshWikiClient", return_value=mock_client
    ):
        result = await bot.run()

    assert result.actions_taken == 1
    assert mock_client.create_page.await_count == 1
    created_name = mock_client.create_page.await_args.args[0]
    assert "smuggling" in created_name
    assert "ssrfbox" in created_name


@pytest.mark.asyncio
async def test_run_creates_toolspec_task_for_tool_idea():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"tool_idea","capability_name":"jwt_confusion_forge",'
        '"title":"Forge JWTs","category":"auth-bypass","rationale":"gap",'
        '"design_sketch":"sketch"}]',
        0,
    ))

    mock_client = _mock_wiki_client()
    with patch(
        "factory.bots.class_gap_researcher.MeshWikiClient", return_value=mock_client
    ):
        result = await bot.run()

    assert result.actions_taken == 1
    created_name = mock_client.create_page.await_args.args[0]
    assert created_name.startswith("Task_Toolspec_jwt_confusion_forge_")


@pytest.mark.asyncio
async def test_run_dedupes_tool_idea_against_existing_capability():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value={"jwt_confusion_forge"})
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"tool_idea","capability_name":"jwt_confusion_forge","title":"dup"}]', 0,
    ))
    result = await bot.run()
    assert result.actions_taken == 0
    assert "duplicates" in result.details


@pytest.mark.asyncio
async def test_run_default_disallows_target_specific():
    """With allow_target_specific left at the default (False), a
    target_specific suggestion from the LLM never reaches task creation —
    it's dropped in _parse_suggestions before run() even sees it."""
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    assert bot._allow_target_specific is False
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"target_specific","target":"jwtlab","vuln_class":"quirk","title":"nope"}]', 0,
    ))
    result = await bot.run()
    assert result.actions_taken == 0
    assert "all 0 suggestions were duplicates" in result.details


@pytest.mark.asyncio
async def test_run_allow_target_specific_true_creates_task():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7", allow_target_specific=True)
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"target_specific","target":"jwtlab","vuln_class":"quirk","title":"ok",'
        '"rationale":"r","test_surface":"s"}]',
        0,
    ))
    mock_client = _mock_wiki_client()
    with patch(
        "factory.bots.class_gap_researcher.MeshWikiClient", return_value=mock_client
    ):
        result = await bot.run()
    assert result.actions_taken == 1


@pytest.mark.asyncio
async def test_run_returns_zero_when_all_suggestions_are_duplicates():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7", allow_target_specific=True)
    bot._fetch_armory_classes = AsyncMock(return_value=({("dashql", "x")}, set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"target_specific","target":"dashql","vuln_class":"x","title":"dup"}]', 0,
    ))
    result = await bot.run()
    assert result.actions_taken == 0
    assert result.errors == []
    assert "duplicates" in result.details


@pytest.mark.asyncio
async def test_run_generic_dedup_uses_slug_not_target():
    """Generic suggestions are deduped by vuln_class slug alone (target is
    just a seeding hint), so a slug already present as a bare playbook
    filename should be treated as covered regardless of which target the
    LLM attached to the new suggestion."""
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), {"cache-deception"}))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"kind":"generic","target":"notekeeper","vuln_class":"cache-deception","title":"dup"}]',
        0,
    ))
    result = await bot.run()
    assert result.actions_taken == 0
    assert "duplicates" in result.details


@pytest.mark.asyncio
async def test_run_reports_llm_errors_in_botresult():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=(set(), set()))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(side_effect=RuntimeError("provider down"))
    result = await bot.run()
    assert result.actions_taken == 0
    assert any("provider down" in e for e in result.errors)


@pytest.mark.asyncio
async def test_run_reports_survey_errors_and_does_not_call_llm():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(side_effect=RuntimeError("github down"))
    bot._fetch_armory_tool_ideas = AsyncMock(return_value=set())
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=("[]", 0))
    result = await bot.run()
    assert result.actions_taken == 0
    assert any("github down" in e for e in result.errors)
    bot._call_llm.assert_not_awaited()
