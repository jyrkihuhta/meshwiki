"""Tests for authentication middleware and login flow."""

import importlib
import json
from base64 import b64encode

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

import meshwiki.config as cfg
import meshwiki.main

TEST_SESSION_SECRET = "test-secret-key-32-chars-minimum!"
# The key every deployment used to fall back to when no secret was configured.
PUBLIC_DEV_SECRET = "dev-secret-change-in-production"


def _forge_session_cookie(secret_key: str) -> str:
    """Sign an authenticated session cookie the way Starlette does."""
    payload = b64encode(json.dumps({"authenticated": True}).encode())
    return itsdangerous.TimestampSigner(secret_key).sign(payload).decode()


def _session_secret_in_use(app) -> str:
    """Return the key the app's SessionMiddleware actually signs with."""
    for middleware in app.user_middleware:
        if middleware.cls is SessionMiddleware:
            return str(middleware.kwargs["secret_key"])
    raise AssertionError("SessionMiddleware is not installed")


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


@pytest.mark.parametrize("candidate", ["", "anything"])
def test_verify_password_never_matches_empty_expected(candidate):
    """An unset password must not be satisfiable, least of all by ''."""
    from meshwiki.auth import verify_password

    assert verify_password(candidate, "") is False


def test_verify_password_empty_candidate_rejected():
    from meshwiki.auth import verify_password

    assert verify_password("", "secret") is False


def test_verify_password_handles_non_ascii():
    from meshwiki.auth import verify_password

    assert verify_password("pässwörd", "pässwörd") is True
    assert verify_password("passwörd", "pässwörd") is False


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


# ── Fail-closed credentials (deep review A1) ──────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """Keep failed logins in one test from locking out the next."""
    from meshwiki.auth import _login_attempts

    _login_attempts.clear()
    yield
    _login_attempts.clear()


@pytest.mark.asyncio
async def test_login_empty_password_rejected(client):
    resp = await client.post("/login", data={"password": ""})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers

    resp = await client.post("/login")
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers

    resp = await client.get("/")
    assert resp.status_code == 302


@pytest.mark.asyncio
async def test_login_empty_password_rejected_even_if_password_is_blanked(client):
    """Defence in depth: a blank expected password never lets '' log in.

    Settings refuses to build this configuration, so blank the password on the
    live object to prove the login route does not rely on that alone.
    """
    cfg.settings.auth_password = ""

    resp = await client.post("/login", data={"password": ""})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers

    resp = await client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_empty_password_counts_towards_rate_limit(client):
    for _ in range(5):
        resp = await client.post("/login", data={"password": ""})
        assert resp.status_code == 401
    resp = await client.post("/login", data={"password": "correct-horse"})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_cookie_signed_with_real_secret_is_accepted(client):
    """Control for the forgery tests: the helper produces a valid cookie."""
    client.cookies.set("session", _forge_session_cookie(TEST_SESSION_SECRET))
    resp = await client.get("/")
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("known_key", [PUBLIC_DEV_SECRET, ""])
async def test_cookie_forged_with_public_key_is_rejected(client, known_key):
    client.cookies.set("session", _forge_session_cookie(known_key))
    resp = await client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # secret left unset: used to fall back to the public dev default
        {"session_secret": PUBLIC_DEV_SECRET},
        {"session_secret": ""},  # `MESHWIKI_SESSION_SECRET=` as the examples shipped
        {"session_secret": "too-short"},
        {"session_secret": TEST_SESSION_SECRET, "auth_password": ""},
    ],
    ids=["unset", "dev-default", "blank", "short", "no-password"],
)
def test_auth_enabled_app_cannot_be_configured_insecurely(tmp_path, overrides):
    """The configurations that allowed forged cookies no longer produce an app."""
    kwargs = {"auth_password": "correct-horse", **overrides}
    with pytest.raises(ValidationError):
        cfg.Settings(
            _env_file=None,
            data_dir=tmp_path,
            auth_enabled=True,
            graph_watch=False,
            **kwargs,
        )


def test_auth_enabled_app_signs_with_configured_secret(auth_settings):
    assert _session_secret_in_use(meshwiki.main.app) == TEST_SESSION_SECRET


@pytest.fixture
def no_auth_settings(tmp_path):
    """Auth disabled and no secret configured: the developer default."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        _env_file=None, data_dir=tmp_path, auth_enabled=False, graph_watch=False
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


def test_auth_disabled_app_never_signs_with_a_public_key(no_auth_settings):
    secret = _session_secret_in_use(meshwiki.main.app)
    assert secret not in ("", PUBLIC_DEV_SECRET)
    assert len(secret) >= 32
    assert secret == no_auth_settings.session_secret


@pytest.mark.asyncio
async def test_auth_disabled_app_starts_and_serves_without_secret(no_auth_settings):
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(no_auth_settings.data_dir, watch=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=meshwiki.main.app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            resp = await c.get("/")
            assert resp.status_code == 200
            # /login can never succeed while auth is off, whatever is submitted.
            resp = await c.post("/login", data={"password": ""})
            assert resp.status_code == 401
    finally:
        shutdown_engine()


# ── WebSocket terminal auth tests ─────────────────────────────────────────────


@pytest.fixture
def sync_auth_client(auth_settings):
    """Synchronous TestClient for WebSocket tests.

    TestClient manages its own event loop, so we must not call
    manager.start_polling() before entering the ``with`` block.
    The TestClient's lifespan handling starts the app (and polling)
    internally when ``raise_server_exceptions=False`` and ``app``
    has no lifespan registered for this fixture path.  We skip
    explicit polling here to avoid the "no running event loop" error.
    """
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(auth_settings.data_dir, watch=False)
    with TestClient(meshwiki.main.app, raise_server_exceptions=False) as c:
        yield c
    shutdown_engine()


def test_ws_terminal_unauthenticated_rejected(sync_auth_client):
    """Unauthenticated WebSocket connection to /ws/terminal/ is closed with 1008."""
    with pytest.raises(Exception):
        # TestClient raises when the server closes the connection immediately
        with sync_auth_client.websocket_connect("/ws/terminal/SomeTask"):
            pass  # should not reach here


def test_ws_terminal_authenticated_accepted(sync_auth_client):
    """An authenticated WebSocket reaches the handler (no policy-violation close)."""
    from meshwiki.core.terminal_sessions import create_session

    # Create a closed session so the handler sends the "no output" message and exits
    create_session("AuthedTask")
    from meshwiki.core.terminal_sessions import _sessions

    _sessions["AuthedTask"].closed = True

    # Log in first to establish a session cookie
    resp = sync_auth_client.post("/login", data={"password": "correct-horse"})
    assert resp.status_code in (200, 302)

    # The WebSocket connection should be accepted and then cleanly closed by the server
    with sync_auth_client.websocket_connect("/ws/terminal/AuthedTask") as ws:
        # Drain any buffered messages; the server closes after replaying a closed
        # session
        try:
            while True:
                ws.receive_text()
        except Exception:
            pass  # server-side close or disconnect is expected


# ── /ws/factory requires a session when auth is enabled ───────────────────────


@pytest.fixture
def factory_auth_settings(tmp_path):
    """Auth enabled AND factory enabled, so /ws/factory reaches its auth check."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        auth_enabled=True,
        auth_password="correct-horse",
        session_secret="test-secret-key-32-chars-minimum!",
        graph_watch=False,
        factory_enabled=True,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
def sync_factory_auth_client(factory_auth_settings, monkeypatch):
    from meshwiki.core.graph import init_engine, shutdown_engine
    from meshwiki.core.webhooks import dispatcher

    # factory_enabled makes the lifespan start the module-level webhook
    # dispatcher, whose queue binds to the first event loop that uses it; each
    # TestClient runs its own loop. These tests don't exercise webhooks.
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(dispatcher, "start", _noop)
    monkeypatch.setattr(dispatcher, "stop", _noop)

    init_engine(factory_auth_settings.data_dir, watch=False)
    with TestClient(meshwiki.main.app, raise_server_exceptions=False) as c:
        yield c
    shutdown_engine()


def test_ws_factory_unauthenticated_rejected(sync_factory_auth_client):
    """Without a session, /ws/factory closes with 1008 before accepting."""
    with pytest.raises(Exception):
        with sync_factory_auth_client.websocket_connect("/ws/factory"):
            pass  # should not reach here


def test_ws_factory_authenticated_accepted(sync_factory_auth_client, monkeypatch):
    """A logged-in client is accepted and receives factory events."""
    from starlette.websockets import WebSocketDisconnect

    from meshwiki.core import factory_ws_manager as fwm

    class _OneShotQueue:
        """Deliver one event, then end the handler's loop cleanly."""

        def __init__(self):
            self._sent = False

        async def get(self):
            if self._sent:
                raise WebSocketDisconnect()
            self._sent = True
            return {"type": "ping"}

    monkeypatch.setattr(fwm.factory_ws_manager, "connect", lambda: (0, _OneShotQueue()))
    monkeypatch.setattr(fwm.factory_ws_manager, "disconnect", lambda _cid: None)

    resp = sync_factory_auth_client.post("/login", data={"password": "correct-horse"})
    assert resp.status_code in (200, 302)

    with sync_factory_auth_client.websocket_connect("/ws/factory") as ws:
        assert ws.receive_json() == {"type": "ping"}
