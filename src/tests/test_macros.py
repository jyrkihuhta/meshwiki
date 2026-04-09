"""Unit tests for the <<LastModified>> macro."""

from datetime import datetime, timedelta, timezone

import markdown
import pytest

from meshwiki.core.parser import LastModifiedExtension, parse_wiki_content


def _render(content: str, modified: datetime | None) -> str:
    """Render wiki content with LastModified macro."""
    ext = LastModifiedExtension(modified=modified)
    md = markdown.Markdown(extensions=[ext])
    return md.convert(content)


class TestLastModifiedBasic:
    """Basic rendering tests."""

    def test_last_modified_with_recent_datetime(self):
        """<<LastModified>> with recent datetime renders relative time."""
        dt = datetime.now(timezone.utc) - timedelta(minutes=30)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html
        assert "Last modified" in html
        assert "30m ago" in html

    def test_last_modified_with_hours_ago(self):
        """<<LastModified>> with hours ago renders hours."""
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html
        assert "Last modified" in html
        assert "3h ago" in html

    def test_last_modified_with_days_ago(self):
        """<<LastModified>> with days ago renders days."""
        dt = datetime.now(timezone.utc) - timedelta(days=2)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html
        assert "Last modified" in html
        assert "2d ago" in html

    def test_last_modified_with_old_date(self):
        """<<LastModified>> with very old date renders date string."""
        dt = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html
        assert "Last modified" in html
        assert "2020-01-01" in html

    def test_last_modified_with_just_now(self):
        """<<LastModified>> with very recent time renders just now."""
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html
        assert "Last modified" in html
        assert "just now" in html


class TestLastModifiedNone:
    """Tests for None modified datetime."""

    def test_last_modified_none(self):
        """<<LastModified>> with None renders em dash."""
        html = _render("<<LastModified>>", modified=None)
        assert '<span class="last-modified">—</span>' in html
        assert "Last modified" not in html


class TestLastModifiedNoMacro:
    """Tests for content without the macro."""

    def test_last_modified_no_macro(self):
        """Content without <<LastModified>> is unaffected."""
        html = _render("Hello world", modified=datetime.now(timezone.utc))
        assert "last-modified" not in html
        assert "Hello world" in html

    def test_last_modified_no_macro_with_other_content(self):
        """Content with other wiki syntax is unaffected."""
        html = _render("# Hello\n\nSome content", modified=datetime.now(timezone.utc))
        assert '<span class="last-modified">' not in html
        assert "<h1>Hello</h1>" in html


class TestLastModifiedEdgeCases:
    """Edge case handling."""

    def test_last_modified_in_text(self):
        """<<LastModified>> within text is replaced correctly."""
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        html = _render("This page was <<LastModified>>.", modified=dt)
        assert '<span class="last-modified">' in html
        assert "This page was" in html
        assert "." in html

    def test_last_modified_naive_datetime(self):
        """<<LastModified>> with naive datetime still works."""
        dt = datetime.now() - timedelta(hours=1)
        html = _render("<<LastModified>>", modified=dt)
        assert '<span class="last-modified">' in html

    def test_last_modified_not_replaced_in_code_block(self):
        """<<LastModified>> inside code blocks is escaped, not rendered."""
        content = "```\n<<LastModified>>\n```"
        html = _render(content, modified=datetime.now(timezone.utc))
        assert '<span class="last-modified">' not in html
        assert "&lt;&lt;LastModified&gt;&gt;" in html

    def test_last_modified_not_replaced_in_tilde_code_block(self):
        """<<LastModified>> inside ~~~ code blocks is escaped."""
        content = "~~~\n<<LastModified>>\n~~~"
        html = _render(content, modified=datetime.now(timezone.utc))
        assert '<span class="last-modified">' not in html


class TestLastModifiedViaParseWikiContent:
    """Test <<LastModified>> via parse_wiki_content function."""

    def test_via_parse_wiki_content_with_datetime(self):
        """<<LastModified>> works via parse_wiki_content."""
        dt = datetime.now(timezone.utc) - timedelta(minutes=45)
        html = parse_wiki_content("<<LastModified>>", page_modified=dt)
        assert '<span class="last-modified">' in html
        assert "45m ago" in html

    def test_via_parse_wiki_content_with_none(self):
        """<<LastModified>> with None works via parse_wiki_content."""
        html = parse_wiki_content("<<LastModified>>", page_modified=None)
        assert '<span class="last-modified">—</span>' in html


class TestLastModifiedViaApi:
    """Render <<LastModified>> through the real FastAPI route."""

    @pytest.mark.asyncio
    async def test_last_modified_renders_via_route(self, tmp_path):
        import meshwiki.main

        meshwiki.main.storage.base_path = tmp_path
        await meshwiki.main.storage.save_page(
            "LMMacroTest", "Modified: <<LastModified>>"
        )

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/page/LMMacroTest")

        assert resp.status_code == 200
        assert '<span class="last-modified">' in resp.text
