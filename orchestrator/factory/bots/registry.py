"""Bot registry: register and lifecycle-manage all periodic bots."""

from __future__ import annotations

import logging

from .base import BaseBot

logger = logging.getLogger(__name__)


class BotRegistry:
    """Holds registered bots and provides start_all / stop_all coroutines.

    Usage::

        registry = BotRegistry()
        registry.register(BookkeeperBot())
        await registry.start_all()   # call from lifespan startup
        ...
        await registry.stop_all()    # call from lifespan shutdown
    """

    def __init__(self) -> None:
        self._bots: list[BaseBot] = []

    def register(self, bot: BaseBot) -> None:
        """Add a bot to the registry.

        Args:
            bot: Any :class:`BaseBot` subclass instance.
        """
        self._bots.append(bot)
        logger.debug("registry: registered bot %r", bot.name)

    async def start_all(self) -> None:
        """Start all registered bots."""
        for bot in self._bots:
            await bot.start()
        logger.info("registry: %d bot(s) started", len(self._bots))

    async def stop_all(self) -> None:
        """Stop all registered bots in registration order."""
        for bot in self._bots:
            await bot.stop()
        logger.info("registry: all bots stopped")

    def get_status(self) -> list[dict]:
        """Return status snapshots for all registered bots."""
        return [bot.get_status() for bot in self._bots]
