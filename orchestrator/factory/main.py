"""Entry point for the factory orchestrator service."""

import uvicorn

from .config import get_settings
from .webhook_server import app  # noqa: F401 — re-export for uvicorn string reference

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(app, host=s.host, port=s.port, log_level=s.log_level)
