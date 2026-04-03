"""Markdown parser with wiki link support."""

import json
import re
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Callable
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor, SimpleTagInlineProcessor
from markdown.preprocessors import Preprocessor

# Pattern for wiki links: [[PageName]] or [[PageName|Display Text]]
WIKI_LINK_PATTERN = r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"

# Pattern for strikethrough: ~~text~~
# Group 2 must contain the text (SimpleTagInlineProcessor expectation)
STRIKETHROUGH_PATTERN = r"(~~)(.*?)~~"


class StrikethroughExtension(Extension):
    """Markdown extension for ~~strikethrough~~ text."""

    def extendMarkdown(self, md: Markdown) -> None:
        """Add strikethrough pattern to markdown parser."""
        md.inlinePatterns.register(
            SimpleTagInlineProcessor(STRIKETHROUGH_PATTERN, "del"),
            "strikethrough",
            50,
        )


class WikiLinkInlineProcessor(InlineProcessor):
    """Inline processor for wiki links."""

    def __init__(self, pattern: str, md: Markdown, page_exists: Callable[[str], bool]):
        super().__init__(pattern, md)
        self.page_exists = page_exists

    def handleMatch(self, m: re.Match, data: str) -> tuple[Element | None, int, int]:
        """Convert wiki link match to HTML anchor element."""
        page_name = m.group(1).strip()
        display_text = m.group(2)
        if display_text:
            display_text = display_text.strip()
        else:
            display_text = page_name

        # Create anchor element
        el = Element("a")
        el.text = display_text
        el.set("href", f"/page/{page_name.replace(' ', '_')}")

        # Add class based on whether page exists
        if self.page_exists(page_name):
            el.set("class", "wiki-link")
        else:
            el.set("class", "wiki-link wiki-link-missing")

        return el, m.start(0), m.end(0)


class WikiLinkExtension(Extension):
    """Markdown extension for wiki links."""

    def __init__(self, page_exists: Callable[[str], bool] | None = None, **kwargs):
        self.page_exists = page_exists or (lambda x: True)
        super().__init__(**kwargs)

    def extendMarkdown(self, md: Markdown) -> None:
        """Add wiki link pattern to markdown parser."""
        wiki_link_processor = WikiLinkInlineProcessor(
            WIKI_LINK_PATTERN,
            md,
            self.page_exists,
        )
        md.inlinePatterns.register(wiki_link_processor, "wiki_link", 75)


# Pattern for MetaTable macro: <<MetaTable(...)>>
METATABLE_PATTERN = re.compile(r"<<MetaTable\((.+?)\)>>", re.DOTALL)


def _parse_metatable_args(args_str: str) -> tuple[list, list[str]]:
    """Parse MetaTable arguments into filters and columns.

    Args:
        args_str: e.g. "status=draft, ||name||status||author||"

    Returns:
        Tuple of (filter list, column names list).
    """
    from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE

    if not GRAPH_ENGINE_AVAILABLE:
        return [], []

    from graph_core import Filter

    filters = []
    columns: list[str] = []

    # Extract column spec: ||col1||col2||col3||
    col_match = re.search(r"\|\|(.+?)$", args_str)
    if col_match:
        col_str = col_match.group(0)
        args_str = args_str[: col_match.start()].strip().rstrip(",")
        columns = [c.strip() for c in col_str.split("||") if c.strip()]

    # Parse remaining filters
    if args_str.strip():
        for part in args_str.split(","):
            part = part.strip()
            if not part:
                continue
            if "~=" in part:
                key, value = part.split("~=", 1)
                filters.append(Filter.contains(key.strip(), value.strip()))
            elif "/=" in part:
                key, value = part.split("/=", 1)
                filters.append(Filter.matches(key.strip(), value.strip()))
            elif "=" in part:
                key, value = part.split("=", 1)
                filters.append(Filter.equals(key.strip(), value.strip()))

    return filters, columns


def _render_metatable(filters: list, columns: list[str]) -> str:
    """Render a MetaTable query as an HTML table.

    Args:
        filters: List of Filter objects.
        columns: Column names to display.

    Returns:
        HTML table string wrapped in a div.
    """
    from meshwiki.core.graph import get_engine

    engine = get_engine()
    if engine is None:
        return (
            '<div class="metatable-wrapper">'
            '<p class="metatable-unavailable">'
            "<em>MetaTable: graph engine not available</em></p></div>"
        )

    if not columns:
        columns = ["name"]

    try:
        result = engine.metatable(filters, columns)
    except Exception as e:
        return (
            f'<div class="metatable-wrapper">'
            f'<p class="metatable-error"><em>MetaTable error: {e}</em></p></div>'
        )

    if not result.rows:
        return (
            '<div class="metatable-wrapper">'
            '<p class="metatable-empty"><em>No matching pages found</em></p></div>'
        )

    lines = ['<div class="metatable-wrapper">', '<table class="metatable">']
    lines.append("<thead><tr>")
    for col in result.columns:
        lines.append(f"<th>{col}</th>")
    lines.append("</tr></thead>")
    lines.append("<tbody>")

    for row in result:
        # Skip completely empty rows
        if all(not row.get(col) for col in result.columns):
            continue
        escaped_page = html_escape(row.page_name, quote=True)
        lines.append("<tr>")
        for col in result.columns:
            escaped_col = html_escape(col, quote=True)
            values = row.get(col)
            if col == "name" and values:
                page_name = values[0]
                url_name = page_name.replace(" ", "_")
                cell = f'<a href="/page/{url_name}" class="wiki-link">{page_name}</a>'
                lines.append(
                    f'<td data-page="{escaped_page}" data-field="{escaped_col}">{cell}</td>'
                )
            elif values:
                cell = ", ".join(values)
                lines.append(
                    f'<td data-page="{escaped_page}" data-field="{escaped_col}" data-editable="true">{cell}</td>'
                )
            else:
                lines.append(
                    f'<td data-page="{escaped_page}" data-field="{escaped_col}" data-editable="true"></td>'
                )
        lines.append("</tr>")

    lines.append("</tbody></table>")
    lines.append("</div>")
    return "\n".join(lines)


class MetaTablePreprocessor(Preprocessor):
    """Preprocessor that replaces <<MetaTable(...)>> macros with HTML tables."""

    def run(self, lines: list[str]) -> list[str]:
        """Process lines, replacing MetaTable macros."""
        from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE

        if not GRAPH_ENGINE_AVAILABLE:
            return lines

        text = "\n".join(lines)
        if "<<MetaTable(" not in text:
            return lines

        # Strip out fenced code blocks so we don't replace macros inside them
        code_block_re = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
        code_blocks: list[str] = []

        def stash_code(m: re.Match) -> str:
            placeholder = f"\x00CODEBLOCK{len(code_blocks)}\x00"
            code_blocks.append(m.group(0))
            return placeholder

        text = code_block_re.sub(stash_code, text)

        def replace_match(m: re.Match) -> str:
            args_str = m.group(1)
            filters, columns = _parse_metatable_args(args_str)
            html = _render_metatable(filters, columns)
            # Stash raw HTML so Markdown parser doesn't wrap it in <p> tags
            return self.md.htmlStash.store(html)

        text = METATABLE_PATTERN.sub(replace_match, text)

        # Restore code blocks
        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CODEBLOCK{i}\x00", block)

        return text.split("\n")


class MetaTableExtension(Extension):
    """Markdown extension for <<MetaTable(...)>> macros."""

    def extendMarkdown(self, md: Markdown) -> None:
        """Add MetaTable preprocessor."""
        md.preprocessors.register(
            MetaTablePreprocessor(md),
            "metatable",
            30,
        )


# ─────────────────────────────────────────────────────────────────────────────
# RecentChanges macro
# ─────────────────────────────────────────────────────────────────────────────

RECENTCHANGES_PATTERN = re.compile(r"<<RecentChanges(?:\((\d+)\))?>>")


def _timeago(dt: datetime | None) -> str:
    """Convert datetime to relative time string."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = int(seconds // 60)
        return f"{m}m ago"
    elif seconds < 86400:
        h = int(seconds // 3600)
        return f"{h}h ago"
    elif seconds < 604800:
        d = int(seconds // 86400)
        return f"{d}d ago"
    else:
        return dt.strftime("%Y-%m-%d")


def _render_recent_changes(n: int = 10) -> str:
    """Render the <<RecentChanges(n)>> macro as an HTML list of recent pages."""
    try:
        from meshwiki.core.dependencies import get_storage

        storage = get_storage()
    except RuntimeError:
        return (
            '<div class="recent-changes-wrapper">'
            '<p class="recent-changes-unavailable">'
            "<em>RecentChanges: storage not available</em></p></div>"
        )

    try:
        import asyncio

        pages = asyncio.run(storage.list_pages_with_metadata())
    except Exception as e:
        return (
            f'<div class="recent-changes-wrapper">'
            f'<p class="recent-changes-error"><em>RecentChanges error: {e}</em></p></div>'
        )

    filtered = [p for p in pages if not p.name.startswith("Factory/")]
    sorted_pages = sorted(
        filtered,
        key=lambda p: p.metadata.modified or datetime.min,
        reverse=True,
    )
    top_pages = sorted_pages[:n]

    if not top_pages:
        return (
            '<div class="recent-changes-wrapper">'
            '<p class="recent-changes-empty"><em>No pages found</em></p></div>'
        )

    lines = ['<div class="recent-changes-wrapper">', '<ul class="recent-changes-list">']
    for page in top_pages:
        url_name = page.name.replace(" ", "_")
        time_str = _timeago(page.metadata.modified)
        lines.append(
            f'<li class="recent-changes-item">'
            f'<a href="/page/{url_name}" class="wiki-link">{html_escape(page.name)}</a>'
            f'<span class="recent-changes-time">{html_escape(time_str)}</span>'
            f"</li>"
        )
    lines.append("</ul></div>")
    return "\n".join(lines)


class RecentChangesPreprocessor(Preprocessor):
    """Preprocessor that replaces <<RecentChanges(n)>> macros with an HTML list."""

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)
        if "<<RecentChanges" not in text:
            return lines

        code_block_re = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
        code_blocks: list[str] = []

        def stash_code(m: re.Match) -> str:
            placeholder = f"\x00RCBLOCK{len(code_blocks)}\x00"
            code_blocks.append(m.group(0))
            return placeholder

        text = code_block_re.sub(stash_code, text)

        def replace_match(m: re.Match) -> str:
            n_str = m.group(1)
            n = int(n_str) if n_str else 10
            html = _render_recent_changes(n)
            return self.md.htmlStash.store(html)

        text = RECENTCHANGES_PATTERN.sub(replace_match, text)

        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00RCBLOCK{i}\x00", block)

        return text.split("\n")


class RecentChangesExtension(Extension):
    """Markdown extension for <<RecentChanges(n)>> macros."""

    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            RecentChangesPreprocessor(md),
            "recentchanges",
            29,
        )


# ─────────────────────────────────────────────────────────────────────────────
# TaskStatus macro
# ─────────────────────────────────────────────────────────────────────────────

TASKSTATUS_PATTERN = re.compile(r"<<TaskStatus>>")

# Ordered happy-path states for the progress diagram.
_HAPPY_PATH = [
    "draft",
    "planned",
    "decomposed",
    "approved",
    "in_progress",
    "review",
    "merged",
    "done",
]

# Off-path terminal states and the happy-path node they branch from.
_OFF_PATH_BRANCH: dict[str, str] = {
    "failed": "in_progress",
    "rejected": "review",
}

# CSS badge class suffix → state names that share it.
_BADGE_CLASS: dict[str, str] = {
    "draft": "gray",
    "planned": "gray",
    "decomposed": "gray",
    "approved": "blue",
    "in_progress": "amber",
    "review": "purple",
    "merged": "green",
    "done": "green",
    "failed": "red",
    "rejected": "red",
    "blocked": "orange",
}


def _mermaid_diagram(status: str) -> str:
    """Return a Mermaid ``flowchart LR`` string for *status*."""
    # Build the happy-path chain definition once.
    chain = " --> ".join(f"{s}({s.replace('_', ' ')})" for s in _HAPPY_PATH)
    lines = ["flowchart LR", f"    {chain}"]

    # Add side-branch node for off-path terminal states.
    is_off_path = status in _OFF_PATH_BRANCH
    if is_off_path:
        branch_from = _OFF_PATH_BRANCH[status]
        lines.append(f"    {branch_from} --> {status}({status.replace('_', ' ')})")

    lines.append("")

    # Pick the colour for the current/active node.
    if status in ("done", "merged"):
        current_fill = "fill:#22c55e,color:#fff,stroke:#16a34a"
    elif status in ("failed", "rejected"):
        current_fill = "fill:#ef4444,color:#fff,stroke:#dc2626"
    elif status == "blocked":
        current_fill = "fill:#f97316,color:#fff,stroke:#ea580c"
    elif status == "review":
        current_fill = "fill:#a855f7,color:#fff,stroke:#9333ea"
    elif status == "in_progress":
        current_fill = "fill:#f59e0b,color:#fff,stroke:#d97706"
    else:
        current_fill = "fill:#3b82f6,color:#fff,stroke:#2563eb"

    lines.append("    classDef done fill:#22c55e,color:#fff,stroke:#16a34a")
    lines.append(f"    classDef current {current_fill}")
    lines.append("    classDef pending fill:#e2e8f0,color:#64748b,stroke:#cbd5e1")
    lines.append("")

    # Compute which happy-path nodes fall into each class.
    if status in _HAPPY_PATH:
        idx = _HAPPY_PATH.index(status)
        done_nodes = _HAPPY_PATH[:idx]
        current_nodes = [status]
        pending_nodes = _HAPPY_PATH[idx + 1 :]
        # merged is terminal for factory tasks — absorb "done" into done nodes
        # so it renders green rather than as a pending white node.
        if status == "merged" and "done" in pending_nodes:
            pending_nodes = [n for n in pending_nodes if n != "done"]
            done_nodes = done_nodes + ["done"]
    elif is_off_path:
        branch_from = _OFF_PATH_BRANCH[status]
        b_idx = _HAPPY_PATH.index(branch_from)
        done_nodes = _HAPPY_PATH[: b_idx + 1]
        current_nodes = [status]
        pending_nodes = _HAPPY_PATH[b_idx + 1 :]
    else:
        # blocked – position in the flow is unknown
        done_nodes = []
        current_nodes = []
        pending_nodes = _HAPPY_PATH

    if done_nodes:
        lines.append(f"    class {','.join(done_nodes)} done")
    if current_nodes:
        lines.append(f"    class {','.join(current_nodes)} current")
    if pending_nodes:
        lines.append(f"    class {','.join(pending_nodes)} pending")

    return "\n".join(lines)


def _get_meta_str(page_metadata: dict, key: str, default: str = "") -> str:
    """Get a metadata value as a string, handling list values from the graph engine."""
    val = page_metadata.get(key, default)
    if isinstance(val, list):
        return val[0] if val else default
    return val or default


def _render_task_status(page_name: str, page_metadata: dict) -> str:
    """Render the <<TaskStatus>> macro as an HTML string."""
    if _get_meta_str(page_metadata, "type") != "task":
        return (
            '<p class="task-status-error">'
            "<code>&lt;&lt;TaskStatus&gt;&gt;</code> is only available on task pages."
            "</p>"
        )

    status: str = _get_meta_str(page_metadata, "status", "draft")
    badge_cls = _BADGE_CLASS.get(status, "gray")

    # ── Section A: status badge ───────────────────────────────────────────────
    badge_html = (
        f'<span class="task-status-badge task-status-badge--{badge_cls}">'
        f"{html_escape(status.replace('_', ' '))}"
        f"</span>"
    )

    # ── Section B: Mermaid flowchart ──────────────────────────────────────────
    mermaid_src = _mermaid_diagram(status)
    diagram_html = (
        '<div class="task-status-diagram">'
        f'<div class="mermaid">{html_escape(mermaid_src)}</div>'
        "</div>"
    )

    # ── Section C: metadata row ───────────────────────────────────────────────
    meta_items: list[str] = []
    if assignee := _get_meta_str(page_metadata, "assignee"):
        meta_items.append(
            f'<span class="task-meta-item">'
            f'<span class="task-meta-key">Assignee</span> {html_escape(assignee)}'
            f"</span>"
        )
    if branch := _get_meta_str(page_metadata, "branch"):
        meta_items.append(
            f'<span class="task-meta-item">'
            f'<span class="task-meta-key">Branch</span> '
            f"<code>{html_escape(branch)}</code>"
            f"</span>"
        )
    if pr_url := _get_meta_str(page_metadata, "pr_url"):
        pr_num = _get_meta_str(page_metadata, "pr_number", "PR")
        meta_items.append(
            f'<span class="task-meta-item">'
            f'<span class="task-meta-key">PR</span> '
            f'<a href="{html_escape(pr_url)}" target="_blank" rel="noopener">'
            f"#{html_escape(pr_num)}</a>"
            f"</span>"
        )
    if parent := _get_meta_str(page_metadata, "parent_task"):
        url = parent.replace(" ", "_")
        meta_items.append(
            f'<span class="task-meta-item">'
            f'<span class="task-meta-key">Parent</span> '
            f'<a href="/page/{html_escape(url)}" class="wiki-link">{html_escape(parent)}</a>'
            f"</span>"
        )
    meta_html = (
        f'<div class="task-status-meta">{"".join(meta_items)}</div>'
        if meta_items
        else ""
    )

    # ── Section D: live terminal (in_progress only) ───────────────────────────
    terminal_html = ""
    if status == "in_progress":
        safe_id = re.sub(r"[^a-zA-Z0-9-]", "-", page_name)
        # Escape < > so the JSON string is safe inside a <script> tag.
        page_name_js = (
            json.dumps(page_name).replace("<", "\\u003c").replace(">", "\\u003e")
        )
        terminal_html = (
            '<div class="task-status-terminal">'
            '<div class="task-terminal-header">'
            '<span class="task-terminal-dot task-terminal-dot--red"></span>'
            '<span class="task-terminal-dot task-terminal-dot--yellow"></span>'
            '<span class="task-terminal-dot task-terminal-dot--green"></span>'
            f'<span class="task-terminal-title">kilo &mdash; {html_escape(page_name)}</span>'
            "</div>"
            f'<div id="task-terminal-{safe_id}" class="task-terminal-body"></div>'
            "</div>"
            "<script>"
            "(function(){"
            f"var PAGE={page_name_js};"
            f"var EL=document.getElementById('task-terminal-{safe_id}');"
            "function boot(){"
            "var t=new Terminal({"
            "cols:220,rows:50,disableStdin:true,convertEol:true,scrollback:5000,"
            "fontFamily:'Menlo,Monaco,\"Courier New\",monospace',fontSize:13,"
            "theme:{background:'#1e1e1e',foreground:'#d4d4d4'}"
            "});"
            "t.open(EL);"
            "var pr=location.protocol==='https:'?'wss:':'ws:';"
            "var ws=new WebSocket(pr+'//'+location.host+'/ws/terminal/'+PAGE);"
            "ws.onmessage=function(e){t.write(e.data);};"
            "ws.onclose=function(){t.write('\\r\\n\\x1b[2m\\u2501\\u2501\\u2501 session ended \\u2501\\u2501\\u2501\\x1b[0m\\r\\n');};"
            "ws.onerror=function(){t.write('\\r\\n\\x1b[31m[connection error]\\x1b[0m\\r\\n');};"
            "}"
            "if(window.Terminal){boot();}"
            "else{"
            "var l=document.createElement('link');l.rel='stylesheet';"
            "l.href='https://cdn.jsdelivr.net/npm/xterm@5/css/xterm.css';"
            "document.head.appendChild(l);"
            "var s=document.createElement('script');"
            "s.src='https://cdn.jsdelivr.net/npm/xterm@5/lib/xterm.js';"
            "s.onload=boot;document.head.appendChild(s);"
            "}"
            "})();"
            "</script>"
        )

    return (
        '<div class="task-status-wrapper">'
        f"{badge_html}"
        f"{diagram_html}"
        f"{meta_html}"
        f"{terminal_html}"
        "</div>"
    )


class TaskStatusPreprocessor(Preprocessor):
    """Preprocessor that replaces <<TaskStatus>> with a status card."""

    def __init__(self, md: Markdown, page_name: str | None, page_metadata: dict | None):
        super().__init__(md)
        self.page_name = page_name or ""
        self.page_metadata = page_metadata or {}

    def run(self, lines: list[str]) -> list[str]:
        """Process lines, replacing TaskStatus macro."""
        text = "\n".join(lines)
        if "<<TaskStatus>>" not in text:
            return lines

        # Protect fenced code blocks (same technique as MetaTablePreprocessor).
        code_block_re = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
        code_blocks: list[str] = []

        def stash_code(m: re.Match) -> str:
            placeholder = f"\x00TSCODE{len(code_blocks)}\x00"
            code_blocks.append(m.group(0))
            return placeholder

        text = code_block_re.sub(stash_code, text)

        def replace_match(_m: re.Match) -> str:
            html = _render_task_status(self.page_name, self.page_metadata)
            return self.md.htmlStash.store(html)

        text = TASKSTATUS_PATTERN.sub(replace_match, text)

        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00TSCODE{i}\x00", block)

        return text.split("\n")


class TaskStatusExtension(Extension):
    """Markdown extension for <<TaskStatus>> macro."""

    def __init__(
        self, page_name: str | None = None, page_metadata: dict | None = None, **kwargs
    ):
        self.page_name = page_name
        self.page_metadata = page_metadata
        super().__init__(**kwargs)

    def extendMarkdown(self, md: Markdown) -> None:
        """Add TaskStatus preprocessor."""
        md.preprocessors.register(
            TaskStatusPreprocessor(md, self.page_name, self.page_metadata),
            "taskstatus",
            29,
        )


# ─────────────────────────────────────────────────────────────────────────────
# EpicStatus macro
# ─────────────────────────────────────────────────────────────────────────────

EPICSTATUS_PATTERN = re.compile(r"<<EpicStatus>>")

# Terminal states count as "complete" for progress calculation.
_COMPLETE_STATES = {"merged", "done"}
_EPIC_BADGE_CLASS = {
    "planned": "gray",
    "in_progress": "amber",
    "review": "purple",
    "merged": "green",
    "done": "green",
    "failed": "red",
    "rejected": "red",
    "blocked": "orange",
}


def _render_epic_status(page_name: str, page_metadata: dict) -> str:
    """Render the <<EpicStatus>> macro as an HTML string."""
    if _get_meta_str(page_metadata, "type") != "epic":
        return (
            '<p class="task-status-error">'
            "<code>&lt;&lt;EpicStatus&gt;&gt;</code> is only available on epic pages."
            "</p>"
        )

    child_tasks: list[dict] = page_metadata.get("_child_tasks", [])
    title = _get_meta_str(page_metadata, "title", page_name)

    # ── Progress counter ──────────────────────────────────────────────────────
    total = len(child_tasks)
    complete = sum(1 for t in child_tasks if t.get("status") in _COMPLETE_STATES)
    pct = int(complete / total * 100) if total else 0

    progress_html = (
        f'<div class="epic-progress">'
        f'<span class="epic-progress-label">{complete} / {total} tasks complete</span>'
        f'<div class="epic-progress-bar">'
        f'<div class="epic-progress-fill" style="width:{pct}%"></div>'
        f"</div>"
        f"</div>"
    )

    # ── Mermaid flowchart ─────────────────────────────────────────────────────
    if child_tasks:
        lines = ["flowchart TD"]
        # Epic root node
        safe_title = title.replace('"', "'")
        lines.append(f'    epic["{html_escape(safe_title)}"]:::epic_node')

        for i, task in enumerate(child_tasks):
            node_id = f"t{i}"
            task_title = task.get("title") or task.get("name", f"Task {i+1}")
            status = task.get("status", "planned")
            # Short label: strip 'Factory/' prefix for readability
            label = task_title.replace('"', "'")
            icon = (
                "✓"
                if status in _COMPLETE_STATES
                else ("⚡" if status == "in_progress" else "○")
            )
            lines.append(f'    {node_id}["{icon} {html_escape(label)}"]:::{status}')
            lines.append(f"    epic --> {node_id}")

        # classDefs
        lines.append(
            "    classDef epic_node fill:#1e40af,color:#fff,stroke:#1d4ed8,font-weight:bold"
        )
        lines.append("    classDef planned fill:#94a3b8,color:#fff,stroke:#64748b")
        lines.append("    classDef decomposed fill:#94a3b8,color:#fff,stroke:#64748b")
        lines.append("    classDef approved fill:#3b82f6,color:#fff,stroke:#2563eb")
        lines.append("    classDef in_progress fill:#f59e0b,color:#fff,stroke:#d97706")
        lines.append("    classDef review fill:#a855f7,color:#fff,stroke:#9333ea")
        lines.append("    classDef merged fill:#22c55e,color:#fff,stroke:#16a34a")
        lines.append("    classDef done fill:#22c55e,color:#fff,stroke:#16a34a")
        lines.append("    classDef failed fill:#ef4444,color:#fff,stroke:#dc2626")
        lines.append("    classDef rejected fill:#ef4444,color:#fff,stroke:#dc2626")
        lines.append("    classDef blocked fill:#f97316,color:#fff,stroke:#ea580c")

        mermaid_src = "\n".join(lines)
        diagram_html = (
            '<div class="task-status-diagram">'
            f'<div class="mermaid">{html_escape(mermaid_src)}</div>'
            "</div>"
        )
    else:
        diagram_html = (
            '<p class="epic-no-tasks">No tasks linked to this epic yet. '
            f"Create task pages under <code>{html_escape(page_name)}/</code> "
            "or add <code>parent_epic: "
            f"{html_escape(page_name)}</code> to existing task pages.</p>"
        )

    return (
        f'<div class="task-status-wrapper epic-status-wrapper">'
        f"{progress_html}"
        f"{diagram_html}"
        f"</div>"
    )


class EpicStatusPreprocessor(Preprocessor):
    """Replace <<EpicStatus>> with rendered HTML, skipping code blocks."""

    def __init__(
        self, md: Markdown, page_name: str | None, page_metadata: dict | None
    ) -> None:
        super().__init__(md)
        self.page_name = page_name or ""
        self.page_metadata = page_metadata or {}

    def run(self, lines: list[str]) -> list[str]:
        # Stash fenced code blocks to avoid replacing macros inside them.
        in_fence = False
        fence_char = ""
        processed: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not in_fence and (
                stripped.startswith("```") or stripped.startswith("~~~")
            ):
                in_fence = True
                fence_char = stripped[:3]
                processed.append(line)
                continue
            if in_fence:
                processed.append(line)
                if stripped.startswith(fence_char):
                    in_fence = False
                continue
            if EPICSTATUS_PATTERN.search(line):
                html = _render_epic_status(self.page_name, self.page_metadata)
                placeholder = self.md.htmlStash.store(html)
                processed.append(placeholder)
            else:
                processed.append(line)
        return processed


class EpicStatusExtension(Extension):
    """Markdown extension for <<EpicStatus>> macro."""

    def __init__(
        self, page_name: str | None = None, page_metadata: dict | None = None, **kwargs
    ):
        self.page_name = page_name
        self.page_metadata = page_metadata
        super().__init__(**kwargs)

    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            EpicStatusPreprocessor(md, self.page_name, self.page_metadata),
            "epicstatus",
            28,
        )


def create_parser(
    page_exists: Callable[[str], bool] | None = None,
    page_name: str | None = None,
    page_metadata: dict | None = None,
) -> Markdown:
    """Create a Markdown parser with wiki link support.

    Args:
        page_exists: Callback to check if a page exists.
                    Used to style missing page links differently.
        page_name: Name of the page being rendered (for TaskStatus macro).
        page_metadata: Frontmatter dict of the page (for TaskStatus macro).

    Returns:
        Configured Markdown parser instance.
    """
    return Markdown(
        extensions=[
            # Core formatting
            "extra",  # Includes: abbreviations, attr_list, def_list, fenced_code, footnotes, md_in_html, tables
            "sane_lists",  # Better list handling
            "smarty",  # Smart quotes and dashes
            "toc",  # Table of contents
            # PyMdown extensions
            "pymdownx.tasklist",  # Task lists with checkboxes
            # Custom extensions
            StrikethroughExtension(),  # ~~strikethrough~~
            WikiLinkExtension(page_exists=page_exists),  # [[WikiLinks]]
            MetaTableExtension(),  # <<MetaTable(...)>>
            TaskStatusExtension(
                page_name=page_name, page_metadata=page_metadata
            ),  # <<TaskStatus>>
            EpicStatusExtension(
                page_name=page_name, page_metadata=page_metadata
            ),  # <<EpicStatus>>
            RecentChangesExtension(),  # <<RecentChanges(n)>>
        ]
    )


def parse_wiki_content(
    content: str,
    page_exists: Callable[[str], bool] | None = None,
    page_name: str | None = None,
    page_metadata: dict | None = None,
) -> str:
    """Parse wiki content (Markdown + wiki links) to HTML.

    Args:
        content: Markdown content with wiki links.
        page_exists: Callback to check if a page exists.
        page_name: Name of the page being rendered (for TaskStatus macro).
        page_metadata: Frontmatter dict of the page (for TaskStatus macro).

    Returns:
        HTML string.
    """
    parser = create_parser(
        page_exists, page_name=page_name, page_metadata=page_metadata
    )
    return parser.convert(content)


def parse_wiki_content_with_toc(
    content: str,
    page_exists: Callable[[str], bool] | None = None,
    page_name: str | None = None,
    page_metadata: dict | None = None,
) -> tuple[str, str]:
    """Parse wiki content and return HTML with table of contents.

    Args:
        content: Markdown content with wiki links.
        page_exists: Callback to check if a page exists.
        page_name: Name of the page being rendered (for TaskStatus macro).
        page_metadata: Frontmatter dict of the page (for TaskStatus macro).

    Returns:
        Tuple of (html_content, toc_html).
    """
    parser = create_parser(
        page_exists, page_name=page_name, page_metadata=page_metadata
    )
    html = parser.convert(content)
    toc_html = getattr(parser, "toc", "")
    return html, toc_html


def extract_wiki_links(content: str) -> list[str]:
    """Extract all wiki links from content.

    Args:
        content: Markdown content with wiki links.

    Returns:
        List of page names referenced in wiki links.
    """
    matches = re.findall(WIKI_LINK_PATTERN, content)
    return [m[0].strip() for m in matches]


FRONTMATTER_PATTERN = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def word_count(content: str) -> int:
    """Count words in Markdown content, stripping frontmatter.

    Args:
        content: Raw Markdown content (may include frontmatter).

    Returns:
        Integer word count of the Markdown body (excluding frontmatter).
    """
    stripped = FRONTMATTER_PATTERN.sub("", content)
    words = stripped.split()
    return len(words)
