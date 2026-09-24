"""Graph engine integration layer.

Provides optional integration with the Rust-based graph_core module.
If graph_core is not installed, the application falls back to
filesystem-based operations gracefully.
"""

from pathlib import Path

from meshwiki.core.logging import get_logger

log = get_logger(__name__)

try:
    from graph_core import (
        Filter,
        GraphEngine,
        MetaTableResult,
        MetaTableRow,
    )

    GRAPH_ENGINE_AVAILABLE = True
except ImportError:
    GRAPH_ENGINE_AVAILABLE = False
    Filter = None  # type: ignore[assignment, misc]
    GraphEngine = None  # type: ignore[assignment, misc]
    MetaTableResult = None  # type: ignore[assignment, misc]
    MetaTableRow = None  # type: ignore[assignment, misc]

_engine: "GraphEngine | None" = None


def get_engine() -> "GraphEngine | None":
    """Get the global graph engine instance."""
    return _engine


def init_engine(data_dir: Path, watch: bool = True) -> "GraphEngine | None":
    """Initialize the graph engine.

    Args:
        data_dir: Path to the wiki pages directory.
        watch: Whether to start file watching.

    Returns:
        The initialized GraphEngine, or None if graph_core is not available.
    """
    global _engine
    if not GRAPH_ENGINE_AVAILABLE:
        log.info("graph_engine_unavailable")
        return None

    try:
        _engine = GraphEngine(str(data_dir))
        _engine.rebuild()
        log.info(
            "graph_engine_initialized",
            pages=_engine.page_count(),
            links=_engine.link_count(),
        )
        if watch:
            _engine.start_watching()
            log.info("graph_watch_started")
    except Exception:
        log.exception("graph_engine_init_failed")
        _engine = None

    return _engine


def shutdown_engine() -> None:
    """Shut down the graph engine and stop file watching."""
    global _engine
    if _engine is not None:
        try:
            if _engine.is_watching():
                _engine.stop_watching()
                log.info("graph_watch_stopped")
        except Exception:
            log.exception("graph_engine_shutdown_failed")
        _engine = None
