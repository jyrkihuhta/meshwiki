"""Entry point for the factory orchestrator service."""

import logging

import uvicorn

from .config import get_settings
from .webhook_server import app  # noqa: F401 — re-export for uvicorn string reference

if __name__ == "__main__":
    s = get_settings()
    # Configure root logger so factory.* loggers are visible.
    # Uvicorn only configures its own loggers; without this, application
    # INFO messages are silently dropped by the root logger's WARNING default.
    logging.basicConfig(
        level=s.log_level.upper(), format="%(levelname)s:     %(name)s - %(message)s"
    )
    uvicorn.run(app, host=s.host, port=s.port, log_level=s.log_level)
