"""Tests for the class-gap-researcher bot.

The bot does I/O (GitHub, MeshWiki, LLM), so the tests focus on the pure
helpers — page rendering, suggestion parsing, dedup logic — plus a full
run() flow with all I/O mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.class_gap_researcher import (
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


def test_render_task_page_includes_required_frontmatter():
    suggestion = {
        "target": "boozt",
        "vuln_class": "host-header-injection",
        "title": "Host header injection on /api/v1/orders",
        "rationale": "Common bug on e-commerce APIs that build links from Host.",
        "test_surface": "Send altered Host: header to /fi/fi/api/v1/orders.",
    }
    name, content = _render_task_page(
        suggestion, base_url="https://www.boozt.com", armory_repo="x/molly-armory",
    )
    assert name.startswith("Task_Playbook_boozt_host_header_injection_")
    # Required frontmatter
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


# ---- LLM-output parser -----------------------------------------------


def test_parse_suggestions_plain_json():
    out = """[
      {"target":"boozt","vuln_class":"host-header","title":"X","rationale":"y","test_surface":"z"}
    ]"""
    parsed = _parse_suggestions(out, cap=5)
    assert len(parsed) == 1
    assert parsed[0]["target"] == "boozt"


def test_parse_suggestions_with_markdown_fence():
    out = "```json\n[{\"target\":\"whatnot\",\"vuln_class\":\"x\",\"title\":\"y\"}]\n```"
    parsed = _parse_suggestions(out, cap=5)
    assert len(parsed) == 1
    assert parsed[0]["target"] == "whatnot"


def test_parse_suggestions_drops_invalid_target():
    out = """[
      {"target":"doppler","vuln_class":"a","title":"good"},
      {"target":"out-of-scope","vuln_class":"b","title":"bad"},
      {"target":"whatnot","vuln_class":"c","title":"also good"}
    ]"""
    parsed = _parse_suggestions(out, cap=5)
    assert [p["target"] for p in parsed] == ["doppler", "whatnot"]


def test_parse_suggestions_drops_missing_keys():
    out = """[
      {"target":"doppler","vuln_class":"a","title":"good"},
      {"target":"doppler","title":"missing vuln_class"},
      {"vuln_class":"c","title":"missing target"}
    ]"""
    parsed = _parse_suggestions(out, cap=5)
    assert len(parsed) == 1


def test_parse_suggestions_respects_cap():
    out = """[
      {"target":"doppler","vuln_class":"a","title":"1"},
      {"target":"doppler","vuln_class":"b","title":"2"},
      {"target":"doppler","vuln_class":"c","title":"3"},
      {"target":"doppler","vuln_class":"d","title":"4"}
    ]"""
    parsed = _parse_suggestions(out, cap=2)
    assert len(parsed) == 2


def test_parse_suggestions_handles_garbage():
    assert _parse_suggestions("not json at all", cap=5) == []
    assert _parse_suggestions("", cap=5) == []
    assert _parse_suggestions("[invalid json]", cap=5) == []
    # Top-level object instead of array
    assert _parse_suggestions('{"target":"doppler"}', cap=5) == []


def test_parse_suggestions_extracts_array_amid_prose():
    out = (
        "Here are my suggestions for the team:\n\n"
        '[{"target":"boozt","vuln_class":"x","title":"y"}]\n\n'
        "Hope that helps!"
    )
    parsed = _parse_suggestions(out, cap=5)
    assert len(parsed) == 1


# ---- run() integration with all I/O mocked ---------------------------


@pytest.mark.asyncio
async def test_run_deduplicates_against_existing_and_open():
    """Suggestions matching either existing armory or open tasks should be filtered out."""
    bot = ClassGapResearcherBot(
        interval_seconds=1, suggestions_per_run=3, model="MiniMax-M2.7",
    )

    bot._fetch_armory_classes = AsyncMock(return_value=[
        ("boozt", "cache-deception"),
        ("doppler", "xxe-blind-oob"),
    ])
    bot._fetch_open_tasks = AsyncMock(return_value=[
        {"target": "whatnot", "vuln_class": "prototype-pollution",
         "page": "Task_Playbook_whatnot_prototype_pollution_0513"},
    ])
    # LLM returns 3 suggestions, two of which collide with existing/open
    bot._call_llm = AsyncMock(return_value=(
        '[{"target":"boozt","vuln_class":"cache-deception","title":"DUPE armory"},'
        '{"target":"whatnot","vuln_class":"prototype-pollution","title":"DUPE task"},'
        '{"target":"doppler","vuln_class":"smuggling","title":"NEW one",'
        '"rationale":"because","test_surface":"endpoint"}]',
        0,
    ))

    # Patch the meshwiki client used in run()
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.create_page = AsyncMock()
    with patch(
        "factory.bots.class_gap_researcher.MeshWikiClient", return_value=mock_client
    ):
        result = await bot.run()

    assert result.actions_taken == 1
    assert mock_client.create_page.await_count == 1
    # The single created page should be for the non-duplicate suggestion
    created_name = mock_client.create_page.await_args.args[0]
    assert "smuggling" in created_name
    assert "doppler" in created_name


@pytest.mark.asyncio
async def test_run_returns_zero_when_all_suggestions_are_duplicates():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=[("boozt", "x")])
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=(
        '[{"target":"boozt","vuln_class":"x","title":"dup"}]', 0,
    ))
    result = await bot.run()
    assert result.actions_taken == 0
    assert result.errors == []
    assert "duplicates" in result.details


@pytest.mark.asyncio
async def test_run_reports_llm_errors_in_botresult():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(return_value=[])
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(side_effect=RuntimeError("provider down"))
    result = await bot.run()
    assert result.actions_taken == 0
    assert any("provider down" in e for e in result.errors)


@pytest.mark.asyncio
async def test_run_reports_survey_errors_and_does_not_call_llm():
    bot = ClassGapResearcherBot(model="MiniMax-M2.7")
    bot._fetch_armory_classes = AsyncMock(side_effect=RuntimeError("github down"))
    bot._fetch_open_tasks = AsyncMock(return_value=[])
    bot._call_llm = AsyncMock(return_value=("[]", 0))
    result = await bot.run()
    assert result.actions_taken == 0
    assert any("github down" in e for e in result.errors)
    bot._call_llm.assert_not_awaited()
