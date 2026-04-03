"""Tests for automatic parent→child edges for subpages in the graph API."""

import os

import pytest

from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE, init_engine, shutdown_engine


@pytest.fixture(autouse=True)
def cleanup_engine():
    yield
    shutdown_engine()


@pytest.fixture
def wiki_dir(tmp_path):
    (tmp_path / "Parent.md").write_text("# Parent\n")
    (tmp_path / "Parent").mkdir()
    (tmp_path / "Parent" / "Child1.md").write_text("# Child 1\n")
    (tmp_path / "Parent" / "Child2.md").write_text("# Child 2\n[[Parent/Child1]]\n")
    (tmp_path / "Orphan").mkdir()
    (tmp_path / "Orphan" / "Sub.md").write_text("# Orphan sub (parent page missing)\n")
    (tmp_path / "Standalone.md").write_text("# Standalone\n")
    return tmp_path


class TestSubpageEdges:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_parent_edges_added_for_subpages(self, wiki_dir):
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)
        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            assert response.status_code == 200
            data = response.json()

        parent_links = [lnk for lnk in data["links"] if lnk.get("type") == "parent"]
        parent_link_pairs = {(lnk["source"], lnk["target"]) for lnk in parent_links}

        assert ("Parent", "Parent/Child1") in parent_link_pairs
        assert ("Parent", "Parent/Child2") in parent_link_pairs

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_no_parent_edge_when_parent_page_missing(self, wiki_dir):
        """Subpages whose parent page doesn't exist get no parent edge."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)
        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            data = response.json()

        parent_links = [lnk for lnk in data["links"] if lnk.get("type") == "parent"]
        targets = {lnk["target"] for lnk in parent_links}
        assert "Orphan/Sub" not in targets

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_wiki_links_have_no_type(self, wiki_dir):
        """Regular wiki links should not have a type field."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)
        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            data = response.json()

        wiki_links = [lnk for lnk in data["links"] if "type" not in lnk]
        assert any(
            lnk["source"] == "Parent/Child2" and lnk["target"] == "Parent/Child1"
            for lnk in wiki_links
        )
