"""In-memory terminal session registry for live task streaming.

Each session buffers the full PTY output so late-joining WebSocket clients
(e.g. after navigating away and back) get a complete replay.  Multiple
concurrent WebSocket connections are supported via per-connection subscriber
queues.

Lifecycle:
  create_session(name)     called when task → in_progress
  put_chunk(name, data)    called by POST /api/v1/tasks/{name}/terminal
  close_session(name)      called when task → any other state
  subscribe(name)          called by each new WebSocket connection
  unsubscribe(name, q)     called when a WebSocket disconnects
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class TerminalSession:
    """Holds the full output buffer and a set of live subscriber queues."""

    buffer: list[str] = field(default_factory=list)
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    closed: bool = False


_sessions: dict[str, TerminalSession] = {}


def create_session(task_name: str) -> None:
    """Open a new terminal session, clearing any previous buffer."""
    _sessions[task_name] = TerminalSession()


def get_session(task_name: str) -> TerminalSession | None:
    """Return the session for *task_name*, or ``None`` if it never existed."""
    return _sessions.get(task_name)


def resolve_session_name(url_name: str) -> str:
    """Resolve a URL path segment to the canonical session key.

    Wiki URLs encode spaces as underscores, but page names may also contain
    real underscores (e.g. ``get_engine()``).  A naive ``replace("_", " ")``
    would corrupt those.  Instead, search existing sessions for one whose key,
    when spaces are replaced by underscores, matches the incoming URL segment.

    Falls back to ``url_name.replace("_", " ")`` if no session matches
    (preserves the old behaviour for pages with no real underscores).
    """
    if url_name in _sessions:
        return url_name
    for key in _sessions:
        if key.replace(" ", "_") == url_name:
            return key
    return url_name.replace("_", " ")


def subscribe(task_name: str) -> asyncio.Queue[str | None] | None:
    """Register a new WebSocket subscriber.

    Must be called *before* snapshotting the buffer so that no chunk
    arriving between snapshot and queue-drain is missed.  Returns ``None``
    if the session is already closed (caller should just replay the buffer).
    """
    session = _sessions.get(task_name)
    if session is None or session.closed:
        return None
    q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=10_000)
    session.subscribers.append(q)
    return q


def unsubscribe(task_name: str, q: asyncio.Queue[str | None]) -> None:
    """Remove a subscriber queue (called on WebSocket disconnect)."""
    session = _sessions.get(task_name)
    if session and q in session.subscribers:
        session.subscribers.remove(q)


async def put_chunk(task_name: str, data: str) -> None:
    """Append a PTY chunk to the buffer and fan it out to all subscribers."""
    session = _sessions.get(task_name)
    if session is None or session.closed:
        return
    session.buffer.append(data)
    for q in session.subscribers:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass  # slow consumer — drop rather than block


async def close_session(task_name: str) -> None:
    """Mark the session closed and send a sentinel to all live subscribers.

    The buffer is kept so future WebSocket connections can replay the full
    output even after the grinder has finished.
    """
    session = _sessions.get(task_name)
    if session is None:
        return
    session.closed = True
    for q in session.subscribers:
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass
    session.subscribers.clear()
