"""Tests for M0.4 (structured logging) and M0.5 (Prometheus metrics)."""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.config as cfg
import meshwiki.main


@pytest.fixture
def no_auth_settings(tmp_path):
    """Override settings with auth disabled (simplest possible setup)."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        auth_enabled=False,
        graph_watch=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def client(no_auth_settings):
    """Async test client with no auth, no redirect following."""
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(no_auth_settings.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


# ── M0.5: /metrics endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_endpoint_accessible(client):
    """GET /metrics must return 200 without authentication."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_content_type(client):
    """GET /metrics must return Prometheus text format content-type."""
    resp = await client.get("/metrics")
    # prometheus_client uses text/plain; version=0.0.4; charset=utf-8
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contains_expected_metrics(client):
    """Response body must contain the meshwiki_ metric names."""
    resp = await client.get("/metrics")
    body = resp.text
    assert "meshwiki_http_requests_total" in body
    assert "meshwiki_http_request_duration_seconds" in body
    assert "meshwiki_page_views_total" in body
    assert "meshwiki_page_writes_total" in body
    assert "meshwiki_graph_pages_total" in body
    assert "meshwiki_graph_links_total" in body


@pytest.mark.asyncio
async def test_metrics_increments_after_request(client):
    """After a request, http_requests_total counter must be > 0."""
    # Trigger a health request so the counter has at least one entry
    await client.get("/health/live")
    resp = await client.get("/metrics")
    body = resp.text
    # The counter metric should appear with a value line
    assert "meshwiki_http_requests_total{" in body


# ── M0.4: Health / regression ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_live_still_works(client):
    """/health/live must still return 200 after adding middleware."""
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ── M0.5: /metrics exempt from auth ──────────────────────────────────────────


@pytest.fixture
def auth_settings(tmp_path):
    """Override settings with auth enabled."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        auth_enabled=True,
        auth_password="hunter2",
        session_secret="test-secret-key-32-chars-minimum!",
        graph_watch=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def auth_client(auth_settings):
    """Async test client with auth enabled."""
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(auth_settings.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


@pytest.mark.asyncio
async def test_metrics_exempt_from_auth(auth_client):
    """/metrics must be accessible even when auth is enabled."""
    resp = await auth_client.get("/metrics")
    # Should NOT redirect to /login
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_still_redirects_with_auth(auth_client):
    """Sanity-check: normal routes still redirect when auth is on."""
    resp = await auth_client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


# ── M0.4: Structured logging fields ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_logging_module_importable():
    """core.logging must be importable and configure_logging callable."""
    from meshwiki.core.logging import configure_logging, get_logger

    # Should not raise
    configure_logging()
    logger = get_logger("test")
    assert logger is not None


@pytest.mark.asyncio
async def test_logging_produces_structured_events():
    """get_logger().info() must emit structured log entries with correct fields."""
    import structlog.testing

    from meshwiki.core.logging import get_logger

    logger = get_logger("test_json")
    with structlog.testing.capture_logs() as cap:
        logger.info("test_event", key="value")

    assert len(cap) >= 1
    record = cap[-1]
    assert record.get("event") == "test_event"
    assert record.get("key") == "value"
    assert record.get("log_level") == "info"


# ── M0.2: /health/ready endpoint ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_ready_returns_200_when_healthy(client):
    """/health/ready must return 200 with storage=ok when data dir is accessible."""
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checks"]["storage"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_returns_503_when_data_dir_broken(client):
    """/health/ready must return 503 when the data directory cannot be accessed."""
    broken = Path("/nonexistent_meshwiki_test_dir/pages")
    with patch.object(meshwiki.main.settings, "data_dir", broken):
        resp = await client.get("/health/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert "error" in data["checks"]["storage"]


@pytest.mark.asyncio
async def test_health_ready_exempt_from_auth(auth_client):
    """/health/ready must be accessible without authentication."""
    resp = await auth_client.get("/health/ready")
    assert resp.status_code == 200


# ── M0.5: Security headers middleware ────────────────────────────────────────


@pytest.mark.asyncio
async def test_security_headers_present(client):
    """Core security headers must be present on every response."""
    resp = await client.get("/health/live")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers.get("permissions-policy", "")


@pytest.mark.asyncio
async def test_hsts_absent_in_debug_mode(no_auth_settings):
    """HSTS must NOT be set when debug=True (we're not on HTTPS in dev)."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=no_auth_settings.data_dir,
        auth_enabled=False,
        graph_watch=False,
        debug=True,
    )
    importlib.reload(meshwiki.main)
    try:
        from meshwiki.core.graph import init_engine, shutdown_engine

        init_engine(no_auth_settings.data_dir, watch=False)
        meshwiki.main.manager.start_polling()
        async with AsyncClient(
            transport=ASGITransport(app=meshwiki.main.app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            resp = await c.get("/health/live")
        meshwiki.main.manager.stop_polling()
        shutdown_engine()
    finally:
        cfg.settings = original
        importlib.reload(meshwiki.main)

    assert "strict-transport-security" not in resp.headers


@pytest.mark.asyncio
async def test_csp_header_present(client):
    """Content-Security-Policy must include self and CDN sources."""
    resp = await client.get("/health/live")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "unpkg.com" in csp
    assert "cdnjs.cloudflare.com" in csp
    assert "wss:" in csp
