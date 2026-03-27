"""Tests for authentication middleware and login flow."""

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.config as cfg
import meshwiki.main


@pytest.fixture
def auth_settings(tmp_path):
    """Override settings with auth enabled."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        auth_enabled=True,
        auth_password="correct-horse",
        session_secret="test-secret-key-32-chars-minimum!",
        graph_watch=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def client(auth_settings):
    """Async test client with auth enabled, no redirect following."""
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


# ── Unit tests for auth helpers ───────────────────────────────────────────────


def test_verify_password_correct():
    from meshwiki.auth import verify_password

    assert verify_password("secret", "secret") is True


def test_verify_password_wrong():
    from meshwiki.auth import verify_password

    assert verify_password("wrong", "secret") is False


def test_rate_limiter_lockout():
    from meshwiki.auth import is_rate_limited, record_failed_attempt, reset_attempts

    ip = "10.0.0.1"
    reset_attempts(ip)
    assert not is_rate_limited(ip)
    for _ in range(5):
        record_failed_attempt(ip)
    assert is_rate_limited(ip)
    reset_attempts(ip)
    assert not is_rate_limited(ip)


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_page_accessible(client):
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Sign in" in resp.text


@pytest.mark.asyncio
async def test_health_live_exempt(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_redirects_to_login(client):
    resp = await client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    resp = await client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Incorrect password" in resp.text


@pytest.mark.asyncio
async def test_login_success_sets_session(client):
    resp = await client.post("/login", data={"password": "correct-horse"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "session" in resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_authenticated_can_access_home(client):
    # Login first
    await client.post("/login", data={"password": "correct-horse"})
    resp = await client.get("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_logout_clears_session(client):
    await client.post("/login", data={"password": "correct-horse"})
    resp = await client.post("/logout")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
    # Now protected routes should redirect again
    resp = await client.get("/")
    assert resp.status_code == 302
