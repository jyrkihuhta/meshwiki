"""Tests for graph visualization (Milestone 6)."""

import asyncio
from unittest.mock import patch

import pytest

from meshwiki.core.graph import (
    GRAPH_ENGINE_AVAILABLE,
    init_engine,
    shutdown_engine,
)
from meshwiki.core.ws_manager import ConnectionManager, _event_to_dict

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def wiki_dir(tmp_path):
    """Create a temporary wiki directory with test pages."""
    pages = {
        "HomePage.md": (
            "---\nstatus: published\ntags:\n  - main\n---\n"
            "# Home\n\nWelcome to [[About]] and [[Contact]].\n"
        ),
        "About.md": ("---\nstatus: draft\n---\n# About\n\nSee [[HomePage]].\n"),
        "Contact.md": "# Contact\n\nReturn to [[HomePage]].\n",
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_engine():
    """Ensure engine is shut down after each test."""
    yield
    shutdown_engine()


# ============================================================
# ConnectionManager unit tests
# ============================================================


class TestConnectionManager:
    def test_connect_and_disconnect(self):
        mgr = ConnectionManager()
        cid, q = mgr.connect()
        assert mgr.client_count == 1
        mgr.disconnect(cid)
        assert mgr.client_count == 0

    def test_multiple_clients(self):
        mgr = ConnectionManager()
        cid1, _ = mgr.connect()
        cid2, _ = mgr.connect()
        assert mgr.client_count == 2
        mgr.disconnect(cid1)
        assert mgr.client_count == 1
        mgr.disconnect(cid2)
        assert mgr.client_count == 0

    def test_disconnect_nonexistent(self):
        mgr = ConnectionManager()
        mgr.disconnect(999)  # Should not raise

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self):
        mgr = ConnectionManager()
        _, q1 = mgr.connect()
        _, q2 = mgr.connect()
        await mgr._broadcast({"type": "page_created", "page": "Test"})
        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert msg1["type"] == "page_created"
        assert msg1["page"] == "Test"
        assert msg2["type"] == "page_created"

    @pytest.mark.asyncio
    async def test_broadcast_drops_on_full_queue(self):
        mgr = ConnectionManager()
        _, q = mgr.connect()
        # Fill the queue (maxsize=256)
        for i in range(256):
            q.put_nowait({"i": i})
        # Should not raise, just drops the message
        await mgr._broadcast({"type": "test"})
        assert q.qsize() == 256

    @pytest.mark.asyncio
    async def test_broadcast_to_no_clients(self):
        mgr = ConnectionManager()
        await mgr._broadcast({"type": "test"})  # Should not raise

    def test_start_stop_polling(self):
        mgr = ConnectionManager()
        # start_polling requires an event loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._start_and_stop(mgr))
        finally:
            loop.close()

    async def _start_and_stop(self, mgr):
        mgr.start_polling(interval=0.1)
        assert mgr._poll_task is not None
        await asyncio.sleep(0.05)
        mgr.stop_polling()
        assert mgr._poll_task is None


# ============================================================
# _event_to_dict tests
# ============================================================


class TestEventToDict:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_page_event(self, wiki_dir):
        """Page events should serialize with type and page fields."""
        init_engine(wiki_dir, watch=True)
        import time

        from meshwiki.core.graph import get_engine

        engine = get_engine()
        # Create a new file to trigger a page_created event
        (wiki_dir / "NewPage.md").write_text("# New\n")
        time.sleep(1.5)  # Wait for debounced watcher

        events = engine.poll_events()
        page_events = [e for e in events if e.event_type() == "page_created"]
        if page_events:
            d = _event_to_dict(page_events[0])
            assert d["type"] == "page_created"
            assert "page" in d
            assert "from" not in d

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_link_event(self, wiki_dir):
        """Link events should serialize with type, from, and to fields."""
        init_engine(wiki_dir, watch=True)
        import time

        from meshwiki.core.graph import get_engine

        engine = get_engine()
        # Create a page with a link to trigger link_created event
        (wiki_dir / "LinkTest.md").write_text("# Test\n\n[[HomePage]]\n")
        time.sleep(1.5)

        events = engine.poll_events()
        link_events = [e for e in events if e.event_type() == "link_created"]
        if link_events:
            d = _event_to_dict(link_events[0])
            assert d["type"] == "link_created"
            assert "from" in d
            assert "to" in d


# ============================================================
# /api/graph endpoint tests
# ============================================================


class TestGraphAPI:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_api_graph_returns_nodes_and_links(self, wiki_dir):
        import importlib
        import os

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
            assert "nodes" in data
            assert "links" in data
            node_ids = [n["id"] for n in data["nodes"]]
            assert "HomePage" in node_ids
            assert "About" in node_ids
            assert "Contact" in node_ids
            # HomePage links to About and Contact
            assert len(data["links"]) > 0

    @pytest.mark.asyncio
    async def test_api_graph_without_engine(self):
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        with patch("meshwiki.main.get_engine", return_value=None):
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=meshwiki.main.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/graph")
                assert response.status_code == 200
                data = response.json()
                assert data == {"nodes": [], "links": []}


# ============================================================
# /graph page tests
# ============================================================


class TestGraphPage:
    @pytest.mark.asyncio
    async def test_graph_page_renders(self):
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            body = response.text
            assert "d3.v7" in body
            assert "graph-container" in body
            assert "graph.js" in body

    @pytest.mark.asyncio
    async def test_nav_has_graph_link(self):
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            assert 'href="/graph"' in response.text
