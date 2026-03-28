"""Playwright E2E test fixtures for MeshWiki."""

import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

# Live mode: set E2E_BASE_URL to run against a live server instead of local.
# Also set E2E_AUTH_PASSWORD if the server has auth enabled.
_LIVE_MODE = bool(os.environ.get("E2E_BASE_URL"))

# All test pages live under this subpage in live mode, isolating them from real content.
_E2E_PREFIX = "E2e/" if _LIVE_MODE else ""

# Pages that browser-based tests save via submit/ctrl+s — need explicit cleanup on live server.
_BROWSER_CREATED_PAGES = [f"{_E2E_PREFIX}HelloWorld", f"{_E2E_PREFIX}SaveShortcut"]

# Per-test list of pages created via the create_page fixture (live mode only).
# Tests run serially, so a module-level list is safe.
_live_cleanup_pages: list[str] = []


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 15.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} did not start within {timeout}s")


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    """Start a local MeshWiki server, or yield live server details if E2E_BASE_URL is set."""
    if _LIVE_MODE:
        base_url = os.environ["E2E_BASE_URL"].rstrip("/")
        yield {"url": base_url, "data_dir": None, "port": None}
        return

    data_dir = tmp_path_factory.mktemp("wiki_data")
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "MESHWIKI_DATA_DIR": str(data_dir),
        "MESHWIKI_DEBUG": "true",
        "MESHWIKI_GRAPH_WATCH": "false",
    }

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "meshwiki.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_server(base_url)
        yield {"url": base_url, "data_dir": data_dir, "port": port}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(scope="session")
def live_http_client(e2e_server):
    """Authenticated httpx client for managing pages on the live server."""
    if not _LIVE_MODE:
        yield None
        return

    import httpx

    password = os.environ.get("E2E_AUTH_PASSWORD", "")
    base_url = e2e_server["url"]

    with httpx.Client(base_url=base_url, follow_redirects=True) as client:
        if password:
            resp = client.post("/login", data={"password": password})
            assert (
                resp.status_code == 200
            ), f"Login failed: {resp.status_code} at {resp.url}"
        yield client


@pytest.fixture(scope="session")
def _live_auth_state(e2e_server, live_http_client):
    """Convert httpx session cookies into Playwright storage state."""
    if not _LIVE_MODE or live_http_client is None:
        return None

    parsed = urlparse(e2e_server["url"])
    hostname = parsed.hostname
    is_secure = parsed.scheme == "https"

    cookies = [
        {
            "name": name,
            "value": value,
            "domain": hostname,
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": is_secure,
            "sameSite": "Lax",
        }
        for name, value in live_http_client.cookies.items()
    ]
    return {"cookies": cookies, "origins": []}


@pytest.fixture
def browser_context_args(browser_context_args, _live_auth_state):
    """Inject auth cookies into every Playwright browser context in live mode."""
    if _live_auth_state:
        return {**browser_context_args, "storage_state": _live_auth_state}
    return browser_context_args


@pytest.fixture(scope="session")
def base_url(e2e_server) -> str:
    return e2e_server["url"]


@pytest.fixture(scope="session")
def data_dir(e2e_server) -> Path | None:
    return e2e_server["data_dir"]


@pytest.fixture(scope="session")
def live_prefix() -> str:
    """Page name prefix used in live mode to isolate test pages from real content."""
    return _E2E_PREFIX


def _delete_pages(client, names: set[str]) -> None:
    """Delete pages on the live server, ignoring 404s."""
    for name in names:
        try:
            client.post(f"/page/{name}/delete")
        except Exception:
            pass


@pytest.fixture(autouse=True)
def clean_wiki(e2e_server, live_http_client):
    """Remove all test pages before and after each test for isolation."""
    if _LIVE_MODE:
        # Before: sweep known browser-created pages and any leftovers from prior test
        all_pages = set(_live_cleanup_pages) | set(_BROWSER_CREATED_PAGES)
        _delete_pages(live_http_client, all_pages)
        _live_cleanup_pages.clear()

        yield

        # After: clean up pages created during this test
        all_pages = set(_live_cleanup_pages) | set(_BROWSER_CREATED_PAGES)
        _delete_pages(live_http_client, all_pages)
    else:
        dd = e2e_server["data_dir"]
        for f in dd.glob("*.md"):
            f.unlink()
        yield
        for f in dd.glob("*.md"):
            f.unlink()


@pytest.fixture()
def create_page(e2e_server, live_http_client):
    """Factory fixture to create wiki pages. Returns the actual page name used."""
    if _LIVE_MODE:

        def _create(name: str, content: str) -> str:
            actual_name = f"{_E2E_PREFIX}{name}"
            if not content.startswith("---"):
                now = datetime.now(timezone.utc).isoformat()
                full = f"---\ncreated: {now}\nmodified: {now}\n---\n\n{content}"
            else:
                full = content
            resp = live_http_client.post(f"/page/{actual_name}", data={"content": full})
            assert (
                resp.status_code == 200
            ), f"Failed to create page {actual_name!r}: {resp.status_code}"
            _live_cleanup_pages.append(actual_name)
            return actual_name

        return _create

    else:
        dd = e2e_server["data_dir"]

        def _create(name: str, content: str) -> str:
            filename = name.replace(" ", "_") + ".md"
            if not content.startswith("---"):
                now = datetime.now(timezone.utc).isoformat()
                full = f"---\ncreated: {now}\nmodified: {now}\n---\n\n{content}"
            else:
                full = content
            (dd / filename).write_text(full, encoding="utf-8")
            return name

        return _create
