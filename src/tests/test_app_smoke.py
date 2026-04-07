"""End-to-end smoke tests for the MeshWiki application.

Tests the full page lifecycle by starting the app with a temp directory
and exercising all routes: create, read, update, delete, list, graph.
"""

import importlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from meshwiki.core.models import Page, PageMetadata
from meshwiki.main import build_page_tree_sync

# ---------------------------------------------------------------------------
# build_page_tree_sync unit tests
# ---------------------------------------------------------------------------


def _make_pages(*names: str) -> list[Page]:
    return [Page(name=n, content="", metadata=PageMetadata()) for n in names]


def test_build_page_tree_flat():
    pages = _make_pages("Alpha", "Beta", "Gamma")
    tree = build_page_tree_sync(pages)
    assert [n["name"] for n in tree] == ["Alpha", "Beta", "Gamma"]


def test_build_page_tree_nested():
    pages = _make_pages("Factory", "Factory/Macros", "Factory/Macros/Include")
    tree = build_page_tree_sync(pages)
    assert len(tree) == 1
    macros = tree[0]["children"][0]
    assert macros["name"] == "Factory/Macros"
    assert macros["children"][0]["name"] == "Factory/Macros/Include"


def test_build_page_tree_done_folder_attaches_to_nearest_ancestor():
    """Tasks under Done/ (no wiki page) attach to the nearest existing ancestor."""
    pages = _make_pages(
        "Factory",
        "Factory/Macros",
        "Factory/Macros/Done/Task1",
        "Factory/Macros/Done/Task2",
    )
    tree = build_page_tree_sync(pages)
    assert len(tree) == 1
    macros = tree[0]["children"][0]
    assert macros["name"] == "Factory/Macros"
    child_names = [c["name"] for c in macros["children"]]
    assert "Factory/Macros/Done/Task1" in child_names
    assert "Factory/Macros/Done/Task2" in child_names


def test_build_page_tree_orphan_falls_back_to_root():
    """Pages with no existing ancestor appear at tree root."""
    pages = _make_pages("Orphan/Child")
    tree = build_page_tree_sync(pages)
    assert tree[0]["name"] == "Orphan/Child"


@pytest.fixture()
def wiki_app(tmp_path):
    """Create a fresh app instance pointing at a temp directory.

    Reloads config and main modules so the app picks up the temp
    data_dir. Yields the FastAPI app object.
    """
    os.environ["MESHWIKI_DATA_DIR"] = str(tmp_path)

    import meshwiki.config

    importlib.reload(meshwiki.config)
    import meshwiki.main

    importlib.reload(meshwiki.main)

    yield meshwiki.main.app

    # Cleanup env
    os.environ.pop("MESHWIKI_DATA_DIR", None)


@pytest_asyncio.fixture()
async def client(wiki_app):
    """Async HTTP client wired to the app (no lifespan)."""
    transport = ASGITransport(app=wiki_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=True,
    ) as c:
        yield c


@pytest_asyncio.fixture()
async def client_no_redirect(wiki_app):
    """Async HTTP client that does NOT follow redirects."""
    transport = ASGITransport(app=wiki_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c


# ============================================================
# Page listing
# ============================================================


class TestPageList:
    @pytest.mark.asyncio
    async def test_empty_wiki_shows_no_pages(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "No pages yet" in resp.text

    @pytest.mark.asyncio
    async def test_list_shows_created_pages(self, client):
        await client.post("/page/Alpha", data={"content": "# Alpha"})
        await client.post("/page/Beta", data={"content": "# Beta"})

        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Alpha" in resp.text
        assert "Beta" in resp.text


# ============================================================
# Page creation and viewing
# ============================================================


class TestPageCreateView:
    @pytest.mark.asyncio
    async def test_create_and_view_page(self, client):
        resp = await client.post(
            "/page/HelloWorld", data={"content": "# Hello\n\nWorld!"}
        )
        assert resp.status_code == 200
        assert "Hello" in resp.text

        resp = await client.get("/page/HelloWorld")
        assert resp.status_code == 200
        assert "Hello" in resp.text
        assert "World!" in resp.text

    @pytest.mark.asyncio
    async def test_create_page_with_empty_content(self, client):
        resp = await client.post("/page/EmptyPage", data={"content": ""})
        assert resp.status_code == 200

        resp = await client.get("/page/EmptyPage")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_view_nonexistent_redirects_to_edit(self, client_no_redirect):
        resp = await client_no_redirect.get("/page/DoesNotExist")
        assert resp.status_code == 302
        assert "/page/DoesNotExist/edit" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_create_page_with_markdown(self, client):
        content = (
            "# Heading\n\n"
            "**bold** and *italic*\n\n"
            "- item 1\n"
            "- item 2\n\n"
            "```python\nprint('hello')\n```\n"
        )
        resp = await client.post("/page/MarkdownPage", data={"content": content})
        assert resp.status_code == 200
        body = resp.text
        assert "<strong>bold</strong>" in body
        assert "<em>italic</em>" in body


# ============================================================
# Wiki links
# ============================================================


class TestWikiLinks:
    @pytest.mark.asyncio
    async def test_wiki_link_to_existing_page(self, client):
        await client.post("/page/TargetPage", data={"content": "# Target"})
        await client.post(
            "/page/SourcePage",
            data={"content": "Link to [[TargetPage]]"},
        )

        resp = await client.get("/page/SourcePage")
        assert resp.status_code == 200
        assert 'href="/page/TargetPage"' in resp.text
        assert "wiki-link" in resp.text

    @pytest.mark.asyncio
    async def test_wiki_link_to_missing_page(self, client):
        await client.post(
            "/page/SourcePage",
            data={"content": "Link to [[MissingPage]]"},
        )

        resp = await client.get("/page/SourcePage")
        assert resp.status_code == 200
        assert "wiki-link-missing" in resp.text

    @pytest.mark.asyncio
    async def test_wiki_link_with_display_text(self, client):
        await client.post(
            "/page/LinkPage",
            data={"content": "See [[OtherPage|click here]]"},
        )

        resp = await client.get("/page/LinkPage")
        assert resp.status_code == 200
        assert "click here" in resp.text
        assert 'href="/page/OtherPage"' in resp.text


# ============================================================
# Page editing (update)
# ============================================================


class TestPageEdit:
    @pytest.mark.asyncio
    async def test_edit_form_for_existing_page(self, client):
        await client.post("/page/EditMe", data={"content": "original"})

        resp = await client.get("/page/EditMe/edit")
        assert resp.status_code == 200
        assert "original" in resp.text
        assert "<textarea" in resp.text

    @pytest.mark.asyncio
    async def test_edit_form_for_new_page(self, client):
        resp = await client.get("/page/BrandNew/edit")
        assert resp.status_code == 200
        assert "Create" in resp.text
        assert "<textarea" in resp.text

    @pytest.mark.asyncio
    async def test_update_page_content(self, client):
        await client.post("/page/UpdateMe", data={"content": "v1"})
        await client.post("/page/UpdateMe", data={"content": "v2 updated"})

        resp = await client.get("/page/UpdateMe")
        assert resp.status_code == 200
        assert "v2 updated" in resp.text

    @pytest.mark.asyncio
    async def test_raw_endpoint_returns_current_content(self, client):
        await client.post("/page/RawTest", data={"content": "raw content here"})

        resp = await client.get("/page/RawTest/raw")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "raw content here"


# ============================================================
# Page deletion
# ============================================================


class TestPageDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_page(self, client_no_redirect):
        # Create first (follow redirect manually)
        await client_no_redirect.post("/page/DeleteMe", data={"content": "bye"})

        resp = await client_no_redirect.post("/page/DeleteMe/delete")
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client):
        resp = await client.post("/page/NoSuchPage/delete")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deleted_page_no_longer_viewable(self, client, client_no_redirect):
        await client.post("/page/GoingAway", data={"content": "temp"})
        await client_no_redirect.post("/page/GoingAway/delete")

        # Viewing deleted page should redirect to edit (create)
        resp = await client_no_redirect.get("/page/GoingAway")
        assert resp.status_code == 302

    @pytest.mark.asyncio
    async def test_deleted_page_not_in_list(self, client, client_no_redirect):
        await client.post("/page/Removable", data={"content": "temp"})
        await client_no_redirect.post("/page/Removable/delete")

        resp = await client.get("/")
        assert "Removable" not in resp.text

    @pytest.mark.asyncio
    async def test_raw_deleted_page_returns_404(self, client, client_no_redirect):
        await client.post("/page/RawDel", data={"content": "temp"})
        await client_no_redirect.post("/page/RawDel/delete")

        resp = await client.get("/page/RawDel/raw")
        assert resp.status_code == 404


# ============================================================
# Frontmatter
# ============================================================


class TestFrontmatter:
    @pytest.mark.asyncio
    async def test_save_with_frontmatter(self, client):
        content = "---\nstatus: draft\ntags:\n  - test\n---\n# FM Page\n\nBody."
        resp = await client.post("/page/FMPage", data={"content": content})
        assert resp.status_code == 200

        # Raw should return body without frontmatter
        resp = await client.get("/page/FMPage/raw")
        data = resp.json()
        assert "# FM Page" in data["content"]

    @pytest.mark.asyncio
    async def test_tags_displayed_in_view(self, client):
        content = "---\ntags:\n  - alpha\n  - beta\n---\n# Tagged"
        await client.post("/page/TaggedPage", data={"content": content})

        resp = await client.get("/page/TaggedPage")
        assert resp.status_code == 200
        assert "alpha" in resp.text
        assert "beta" in resp.text


# ============================================================
# HTMX save flow
# ============================================================


class TestHtmxSave:
    @pytest.mark.asyncio
    async def test_htmx_save_returns_view_html(self, client):
        resp = await client.post(
            "/page/HtmxPage",
            data={"content": "# HTMX Content"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Should return the view template, not a redirect
        assert "HTMX Content" in resp.text
        assert "<article" in resp.text

    @pytest.mark.asyncio
    async def test_regular_save_redirects(self, client_no_redirect):
        resp = await client_no_redirect.post(
            "/page/RegularPage",
            data={"content": "# Regular"},
        )
        assert resp.status_code == 302
        assert "/page/RegularPage" in resp.headers["location"]


# ============================================================
# Graph & API routes
# ============================================================


class TestGraphRoutes:
    @pytest.mark.asyncio
    async def test_graph_page_renders(self, client):
        resp = await client.get("/graph")
        assert resp.status_code == 200
        assert "graph" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_graph_api_returns_json(self, client):
        resp = await client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "links" in data


# ============================================================
# Full lifecycle scenario
# ============================================================


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_complete_wiki_workflow(self, client, client_no_redirect):
        """Simulate a real user session: create pages, link them,
        edit, view, delete — verifying each step."""

        # 1. Wiki starts empty
        resp = await client.get("/")
        assert "No pages yet" in resp.text

        # 2. Create HomePage
        resp = await client.post(
            "/page/HomePage",
            data={"content": "# Welcome\n\nSee [[About]] and [[Projects]]."},
        )
        assert resp.status_code == 200

        # 3. Create About page
        resp = await client.post(
            "/page/About",
            data={"content": "# About\n\nBack to [[HomePage]]."},
        )
        assert resp.status_code == 200

        # 4. List now has both pages
        resp = await client.get("/")
        assert "HomePage" in resp.text
        assert "About" in resp.text

        # 5. HomePage links: About exists, Projects missing
        resp = await client.get("/page/HomePage")
        assert resp.status_code == 200
        body = resp.text
        assert 'href="/page/About"' in body
        assert "wiki-link-missing" in body  # Projects doesn't exist

        # 6. Create Projects page — missing link becomes real
        await client.post(
            "/page/Projects",
            data={"content": "# Projects\n\nReturn to [[HomePage]]."},
        )
        resp = await client.get("/page/HomePage")
        # Now both links should be regular (not missing)
        body = resp.text
        assert body.count("wiki-link-missing") == 0

        # 7. Edit About page
        resp = await client.get("/page/About/edit")
        assert resp.status_code == 200
        assert "About" in resp.text

        resp = await client.post(
            "/page/About",
            data={"content": "# About Us\n\nUpdated content.\n\n[[HomePage]]"},
        )
        assert resp.status_code == 200
        assert "Updated content" in resp.text

        # 8. Raw endpoint
        resp = await client.get("/page/About/raw")
        data = resp.json()
        assert "Updated content" in data["content"]

        # 9. Delete Projects page
        resp = await client_no_redirect.post("/page/Projects/delete")
        assert resp.status_code == 302

        # 10. List no longer includes Projects
        resp = await client.get("/")
        assert "Projects" not in resp.text
        assert "HomePage" in resp.text
        assert "About" in resp.text

        # 11. HomePage wiki link to Projects is now missing again
        resp = await client.get("/page/HomePage")
        assert "wiki-link-missing" in resp.text
