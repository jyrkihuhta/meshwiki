"""Factory WebSocket broadcast manager with activity ring buffer.

Holds a 500-entry deque of recent task-transition events so that late-joining
dashboard clients can replay history on connect.  Broadcasts each new event to
all currently-connected /ws/factory clients.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from meshwiki.core.logging import get_logger

log = get_logger(__name__)


class FactoryWSManager:
    """Broadcast factory transition events to connected dashboard clients."""

    def __init__(self, max_activity: int = 500) -> None:
        self._clients: dict[int, asyncio.Queue[dict[str, Any]]] = {}
        self._next_id: int = 0
        self._activity: deque[dict[str, Any]] = deque(maxlen=max_activity)

    def connect(self) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
        """Register a new client and return (client_id, queue)."""
        client_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._clients[client_id] = queue
        log.info("factory_ws_connected", client_id=client_id, total=len(self._clients))
        return client_id, queue

    def disconnect(self, client_id: int) -> None:
        """Unregister a client."""
        self._clients.pop(client_id, None)
        log.info(
            "factory_ws_disconnected", client_id=client_id, total=len(self._clients)
        )

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Append to ring buffer and push to all connected clients."""
        self._activity.append(msg)
        for client_id, queue in list(self._clients.items()):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("factory_ws_queue_full", client_id=client_id)

    def get_activity(self) -> list[dict[str, Any]]:
        """Return ring buffer snapshot in oldest-first order."""
        return list(self._activity)


# Module-level singleton
factory_ws_manager = FactoryWSManager()
