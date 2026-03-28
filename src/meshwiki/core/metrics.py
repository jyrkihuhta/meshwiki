"""Prometheus metrics definitions for MeshWiki.

All metrics are defined at module level so they are registered once
in the default CollectorRegistry.  Import this module early (before
routes are registered) to ensure metrics exist before any requests arrive.
"""

import re

from prometheus_client import Counter, Gauge, Histogram

# ── HTTP metrics ──────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "meshwiki_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "meshwiki_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ── Wiki page metrics ─────────────────────────────────────────────────────────

page_views_total = Counter(
    "meshwiki_page_views_total",
    "Total page views",
    ["page"],
)

page_writes_total = Counter(
    "meshwiki_page_writes_total",
    "Total page write operations",
    ["operation"],  # save | delete
)

# ── Graph metrics ─────────────────────────────────────────────────────────────

graph_pages_total = Gauge(
    "meshwiki_graph_pages_total",
    "Current number of pages tracked by the graph engine",
)

graph_links_total = Gauge(
    "meshwiki_graph_links_total",
    "Current number of links tracked by the graph engine",
)

# ── Path normalisation ────────────────────────────────────────────────────────

# Patterns whose first capture group is replaced with a placeholder.
# Order matters: more specific patterns should appear first.
_NORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # /page/<name>/edit  →  /page/{name}/edit  (name may contain slashes)
    (re.compile(r"^/page/.+/edit$"), "/page/{name}/edit"),
    # /page/<name>/raw   →  /page/{name}/raw
    (re.compile(r"^/page/.+/raw$"), "/page/{name}/raw"),
    # /page/<name>/delete → /page/{name}/delete
    (re.compile(r"^/page/.+/delete$"), "/page/{name}/delete"),
    # /api/page/<name>/metadata → /api/page/{name}/metadata
    (re.compile(r"^/api/page/.+/metadata$"), "/api/page/{name}/metadata"),
    # /page/<name>  →  /page/{name}
    (re.compile(r"^/page/.+$"), "/page/{name}"),
]


def normalize_path(path: str) -> str:
    """Normalise a URL path to avoid high-cardinality label values.

    Replaces dynamic segments (page names, etc.) with ``{name}``
    so that the Prometheus label set stays bounded.

    Args:
        path: Raw URL path from the request.

    Returns:
        Normalised path string suitable for use as a metric label.
    """
    for pattern, replacement in _NORM_PATTERNS:
        if pattern.match(path):
            return replacement
    return path
