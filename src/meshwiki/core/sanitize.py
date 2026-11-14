"""HTML sanitizer for rendered wiki content.

Wiki pages are not only written by trusted editors: the agent factory builds
pages from external, untrusted text (disclosed-report titles, LLM output,
terminal logs). Python-Markdown passes raw HTML through untouched, and the
templates insert the result with ``| safe``, so without sanitization a page can
carry ``<script>``, ``onerror=`` handlers, ``javascript:`` links or HTMX
attributes that act as the viewer.

Everything ``parse_wiki_content`` returns therefore goes through
:func:`sanitize_html`, an allowlist built on ``nh3`` (Rust ``ammonia``).

The allowlist is deliberately narrow and is the single place to change when a
macro starts emitting a new tag or attribute: add it here, with a test in
``tests/test_sanitize.py``. Anything not listed is stripped (unknown tags keep
their text; ``<script>`` and ``<style>`` lose their content too).

Macro output goes through the same filter, so macros must not emit inline
scripts or ``on*=`` handlers. Behaviour lives in ``static/js`` and is driven by
the ``data-*`` attributes listed below.
"""

from __future__ import annotations

import re

import nh3

# ── Tags ──────────────────────────────────────────────────────────────────────

# Standard Markdown / extension output plus the structural tags the macros use.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        # Text structure
        "p", "br", "hr", "div", "span", "blockquote", "pre", "code",
        "h1", "h2", "h3", "h4", "h5", "h6",
        # Inline formatting
        "a", "em", "strong", "b", "i", "u", "s", "del", "ins", "mark", "small",
        "sub", "sup", "kbd", "samp", "var", "abbr", "cite", "q",
        # Lists (including definition lists and task lists)
        "ul", "ol", "li", "dl", "dt", "dd", "label",
        # Tables
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
        # Media and disclosure
        "img", "details", "summary", "figure", "figcaption",
        # Navigation (table of contents)
        "nav",
        # Controls emitted by macros / task lists; inert without a <form>
        "input", "button",
    }
)  # fmt: skip

# Elements whose *content* is dropped as well (everything else keeps its text).
_CLEAN_CONTENT_TAGS: frozenset[str] = frozenset({"script", "style", "iframe", "object"})

# ── Attributes ────────────────────────────────────────────────────────────────

# Allowed on every tag.
_GENERIC_ATTRIBUTES: frozenset[str] = frozenset({"class", "id", "title", "lang", "dir"})

# ``data-*`` attributes consumed by the static JS, scoped to the tags that use
# them so content can't attach behaviour hooks to arbitrary elements.
_DATA_ATTRIBUTES: dict[str, frozenset[str]] = {
    "div": frozenset(
        {"data-included-page", "data-terminal-page", "data-terminal-done"}
    ),
    "span": frozenset({"data-clock", "data-timezone"}),
    "th": frozenset({"data-col"}),
    "td": frozenset({"data-col", "data-field", "data-page", "data-editable"}),
    "button": frozenset({"data-newpage-template", "data-newpage-parent"}),
}

_ATTRIBUTES: dict[str, set[str]] = {
    "*": set(_GENERIC_ATTRIBUTES),
    "a": {"href", "rel", "hx-get", "hx-trigger", "hx-target", "hx-swap"},
    "img": {"src", "alt", "width", "height"},
    "input": {"checked", "disabled", "placeholder"},
    "ol": {"start"},
    "td": {"align", "colspan", "rowspan", "style"},
    "th": {"align", "colspan", "rowspan", "scope", "style"},
    "div": {"style"},
    "details": {"open"},
}
for _tag, _names in _DATA_ATTRIBUTES.items():
    _ATTRIBUTES.setdefault(_tag, set()).update(_names)

# Attributes limited to a fixed set of values. ``nh3`` requires these to be left
# out of ``_ATTRIBUTES``.
_TAG_ATTRIBUTE_VALUES: dict[str, dict[str, set[str]]] = {
    "a": {"target": {"_blank"}},
    "input": {"type": {"checkbox", "text"}},
    "button": {"type": {"button"}},
}

# ── Value filters ─────────────────────────────────────────────────────────────

# HTMX attributes would let content fire requests as the viewer
# (``hx-post`` + ``hx-trigger="load"``), so they are only kept on links, and only
# in the exact shape the wiki-link macro generates for hover-card previews.
_HX_GET_RE = re.compile(r"^/api/pages/[^?#\s]+/preview$")
_HX_FIXED: dict[str, str] = {
    "hx-trigger": "mouseenter delay:250ms",
    "hx-target": "#wiki-hover-card",
    "hx-swap": "outerHTML",
}

# ``style`` is only kept for the two shapes the app emits: the epic progress bar
# and Markdown table column alignment.
_PROGRESS_STYLE_RE = re.compile(r"^width:\s*(\d{1,3})(?:\.\d+)?%;?$")
_ALIGN_STYLE_RE = re.compile(r"^text-align:\s*(?:left|right|center);?$")

_REL_TOKENS = frozenset({"noopener", "noreferrer", "nofollow", "ugc"})
_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp|avif);base64,[A-Za-z0-9+/=]+$"
)

# Schemes ``nh3`` lets through for URL attributes. ``data`` is admitted here only
# so images can embed data URIs; _filter_attribute drops it everywhere else.
_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel", "data"})


def _filter_attribute(tag: str, attr: str, value: str) -> str | None:
    """Final per-value check; return the value to keep or ``None`` to drop."""
    if attr == "href" and value.lower().startswith("data:"):
        return None
    if attr == "src":
        if value.lower().startswith("data:"):
            return value if _DATA_IMAGE_RE.match(value) else None
        return value
    if tag == "a":
        if attr == "hx-get":
            if ".." in value:
                return None
            return value if _HX_GET_RE.match(value) else None
        if attr in _HX_FIXED:
            return value if value == _HX_FIXED[attr] else None
        if attr == "rel":
            tokens = value.lower().split()
            return value if tokens and set(tokens) <= _REL_TOKENS else None
    if attr == "style":
        style = value.strip()
        if tag == "div":
            match = _PROGRESS_STYLE_RE.match(style)
            return style if match and int(match.group(1)) <= 100 else None
        if tag in ("td", "th"):
            return style if _ALIGN_STYLE_RE.match(style) else None
        return None
    return value


def sanitize_html(html: str) -> str:
    """Return *html* with everything outside the allowlist removed.

    Args:
        html: Rendered HTML (Markdown output plus macro HTML).

    Returns:
        Sanitized HTML safe to insert into a page with ``| safe``.
    """
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        clean_content_tags=set(_CLEAN_CONTENT_TAGS),
        attributes={tag: set(names) for tag, names in _ATTRIBUTES.items()},
        tag_attribute_values={
            tag: {attr: set(values) for attr, values in attrs.items()}
            for tag, attrs in _TAG_ATTRIBUTE_VALUES.items()
        },
        generic_attribute_prefixes={"aria-"},
        attribute_filter=_filter_attribute,
        url_schemes=set(_URL_SCHEMES),
        # Keep ``rel`` exactly as the macros set it instead of adding
        # ``noopener noreferrer`` to every internal link.
        link_rel=None,
        strip_comments=True,
    )
