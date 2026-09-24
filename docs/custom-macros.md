# Custom Macros Developer Guide

MeshWiki's parser supports `<<MacroName(args)>>` macros that expand to HTML during Markdown rendering. This guide explains how the system works and how to add your own macros.

## Architecture Overview

MeshWiki uses [Python-Markdown](https://python-markdown.github.io/) with a pipeline of extension points:

```
Raw Markdown lines
  ↓  Preprocessors  (macros expand here — before markdown parsing)
  ↓  Block Processors
  ↓  Inline Processors  (wiki links, strikethrough — during parsing)
  ↓  Tree Processors
  ↓  Postprocessors
HTML output
```

Macros use **Preprocessors** because they need to replace `<<Macro(...)>>` with raw HTML *before* the Markdown parser runs. Inline patterns like `[[WikiLinks]]` and `~~strikethrough~~` use **Inline Processors** instead.

## How MetaTable Works (Reference Implementation)

`<<MetaTable(...)>>` is the primary macro. Here's how it's built:

### 1. Pattern

```python
# parser.py
METATABLE_PATTERN = re.compile(r"<<MetaTable\((.+?)\)>>", re.DOTALL)
```

The pattern captures everything between the parentheses as `group(1)`.

### 2. Argument Parser

```python
def _parse_metatable_args(args_str: str) -> tuple[list, list[str]]:
    """Parse 'status=draft, ||name||status||' into filters and columns."""
    # Extract column spec: ||col1||col2||col3||
    col_match = re.search(r"\|\|(.+?)$", args_str)
    if col_match:
        columns = [c.strip() for c in col_match.group(0).split("||") if c.strip()]
        args_str = args_str[:col_match.start()].strip().rstrip(",")

    # Parse filters: key=value, key~=substring, key/=regex
    filters = []
    for part in args_str.split(","):
        part = part.strip()
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
```

### 3. Render Function

Returns an HTML string. Handles errors gracefully:

```python
def _render_metatable(filters, columns) -> str:
    from meshwiki.core.graph import get_engine

    engine = get_engine()
    if engine is None:
        return '<p class="metatable-unavailable"><em>MetaTable: graph engine not available</em></p>'

    try:
        result = engine.metatable(filters, columns)
    except Exception as e:
        return f'<p class="metatable-error"><em>MetaTable error: {e}</em></p>'

    # Build HTML table from result...
    return html_string
```

### 4. Preprocessor

Joins lines, does regex substitution, splits back:

```python
class MetaTablePreprocessor(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)
        if "<<MetaTable(" not in text:
            return lines  # Fast exit

        def replace_match(m):
            filters, columns = _parse_metatable_args(m.group(1))
            return _render_metatable(filters, columns)

        text = METATABLE_PATTERN.sub(replace_match, text)
        return text.split("\n")
```

### 5. Extension (registers the preprocessor)

```python
class MetaTableExtension(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(
            MetaTablePreprocessor(md),
            "metatable",    # unique name
            30,             # priority (higher = runs earlier)
        )
```

### 6. Registration in `create_parser()`

```python
def create_parser(page_exists=None):
    return Markdown(extensions=[
        "extra", "sane_lists", "smarty", "toc",
        "pymdownx.tasklist",
        StrikethroughExtension(),
        WikiLinkExtension(page_exists=page_exists),
        MetaTableExtension(),  # <-- registered here
    ])
```

## Data Access in Preprocessors

> **Critical constraint:** Preprocessors run synchronously inside FastAPI's already-running event loop. **Never call `asyncio.run()` inside a preprocessor** — it raises `RuntimeError: This event loop is already running`.

### Two patterns, depending on your data source

#### Pattern A — Synchronous data (graph engine)

The graph engine (`get_engine()`) is synchronous. Call it directly from the render function:

```python
def _render_my_macro() -> str:
    from meshwiki.core.graph import get_engine
    engine = get_engine()
    if engine is None:
        return '<em>graph engine not available</em>'
    return engine.list_pages()  # synchronous, safe in preprocessor
```

#### Pattern B — Async data (storage, database)

If your macro needs data from `storage` (or any async source), **do not fetch inside the preprocessor**. Instead:

1. Fetch data in the async route handler (it already runs in the event loop)
2. Pass it as a constructor parameter to your Extension
3. Store it on the Preprocessor instance

```python
# 1. Render function takes pre-fetched data as a parameter
def _render_my_macro(my_data: list) -> str:
    ...

# 2. Preprocessor stores data on self
class MyMacroPreprocessor(Preprocessor):
    def __init__(self, md: Markdown, my_data: list | None = None):
        super().__init__(md)
        self.my_data = my_data or []

    def run(self, lines: list[str]) -> list[str]:
        ...
        html = _render_my_macro(self.my_data)
        ...

# 3. Extension accepts and threads data through
class MyMacroExtension(Extension):
    def __init__(self, my_data: list | None = None, **kwargs):
        self.my_data = my_data or []
        super().__init__(**kwargs)

    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            MyMacroPreprocessor(md, self.my_data), "my_macro", 28
        )

# 4. create_parser() accepts and passes the data
def create_parser(..., my_data: list | None = None) -> Markdown:
    return Markdown(extensions=[
        ...
        MyMacroExtension(my_data=my_data),
    ])

# 5. parse_wiki_content() threads it through
def parse_wiki_content(..., my_data: list | None = None) -> str:
    parser = create_parser(..., my_data=my_data)
    return parser.convert(content)
```

```python
# 6. Route handler: fetch once, pass in
@app.get("/page/{name}")
async def view_page(name: str):
    my_data = await storage.list_things()  # ✅ async here is fine
    html = parse_wiki_content(content, my_data=my_data)
```

**Reference implementations:** `<<RecentChanges>>` and `<<PageList>>` in `parser.py` both follow Pattern B.

**Anti-pattern (do not do this):**
```python
# ❌ WRONG — raises RuntimeError: This event loop is already running
def _render_my_macro() -> str:
    storage = asyncio.run(get_storage())  # crashes in FastAPI
    pages = asyncio.run(storage.list_pages())
    ...
```

---

## Adding a New Macro: Step-by-Step

Let's build a `<<PageCount>>` macro that displays the total number of wiki pages.

### Step 1: Define the pattern

```python
# In parser.py
PAGECOUNT_PATTERN = re.compile(r"<<PageCount>>")
```

### Step 2: Write the render function

```python
def _render_pagecount() -> str:
    from meshwiki.core.graph import get_engine

    engine = get_engine()
    if engine is None:
        return '<span class="macro-unavailable">?</span>'

    try:
        pages = engine.list_pages()
        return f'<span class="page-count">{len(pages)}</span>'
    except Exception:
        return '<span class="macro-error">?</span>'
```

### Step 3: Create the preprocessor

```python
class PageCountPreprocessor(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)
        if "<<PageCount>>" not in text:
            return lines

        text = PAGECOUNT_PATTERN.sub(lambda m: _render_pagecount(), text)
        return text.split("\n")
```

### Step 4: Create the extension

```python
class PageCountExtension(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(
            PageCountPreprocessor(md),
            "pagecount",
            30,
        )
```

### Step 5: Register in `create_parser()`

```python
def create_parser(page_exists=None):
    return Markdown(extensions=[
        # ... existing extensions ...
        PageCountExtension(),
    ])
```

### Step 6: Use it in a wiki page

```markdown
This wiki has <<PageCount>> pages.
```

Renders as: "This wiki has **42** pages."

## Adding a Macro with Arguments

For `<<PageList(tag=python)>>`, follow the same pattern but add argument parsing:

```python
PAGELIST_PATTERN = re.compile(r"<<PageList\((.+?)\)>>", re.DOTALL)

def _render_pagelist(args_str: str) -> str:
    from meshwiki.core.graph import get_engine

    engine = get_engine()
    if engine is None:
        return '<p class="macro-unavailable"><em>PageList: graph engine not available</em></p>'

    # Parse key=value args
    filters = []
    for part in args_str.split(","):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            from graph_core import Filter
            filters.append(Filter.equals(key.strip(), value.strip()))

    try:
        pages = engine.query(filters)
        if not pages:
            return '<p class="macro-empty"><em>No matching pages</em></p>'

        items = []
        for p in sorted(pages, key=lambda p: p.name):
            url = f"/page/{p.name.replace(' ', '_')}"
            items.append(f'<li><a href="{url}" class="wiki-link">{p.name}</a></li>')
        return f'<ul class="page-list-macro">{"".join(items)}</ul>'
    except Exception as e:
        return f'<p class="macro-error"><em>PageList error: {e}</em></p>'
```

Usage in a wiki page:

```markdown
## Python Pages
<<PageList(tags=python)>>
```

## Extension Points Reference

| Type | Base Class | Input | Output | Use Case |
|------|-----------|-------|--------|----------|
| **Preprocessor** | `Preprocessor` | `list[str]` (lines) | `list[str]` | Block macros (`<<Macro>>`) |
| **InlineProcessor** | `InlineProcessor` | regex match | `Element`, start, end | Inline syntax (`[[links]]`, `~~strike~~`) |
| **BlockProcessor** | `BlockProcessor` | text block | XML elements | Custom block structures |
| **TreeProcessor** | `Treeprocessor` | `ElementTree` | `ElementTree` | Post-parse DOM manipulation |
| **Postprocessor** | `Postprocessor` | `str` (HTML) | `str` | Final HTML string tweaks |

### When to use which

- **Macro that outputs a block of HTML** (table, list, div) → Preprocessor
- **Inline syntax** (`<<something>>` inline, custom brackets) → InlineProcessor
- **Modify existing elements** (add classes, wrap elements) → TreeProcessor
- **String replacement in final HTML** → Postprocessor

## Accessing the Graph Engine

The graph engine is optional. Always use lazy imports and handle the missing case:

```python
def _render_my_macro() -> str:
    from meshwiki.core.graph import get_engine

    engine = get_engine()
    if engine is None:
        return '<em>Graph engine not available</em>'

    # engine.list_pages() -> list of page names
    # engine.get_backlinks(name) -> list of page names
    # engine.get_outlinks(name) -> list of page names
    # engine.get_metadata(name) -> dict[str, list[str]] or None
    # engine.query(filters) -> list of PageInfo objects
    # engine.metatable(filters, columns) -> MetaTableResult
```

Available `Filter` constructors:

```python
from graph_core import Filter

Filter.equals("key", "value")
Filter.has_key("key")
Filter.contains("key", "substring")
Filter.matches("key", r"regex")
Filter.links_to("PageName")
Filter.linked_from("PageName")
```

## Testing Macros

Follow the existing test patterns in `tests/test_parser.py`:

```python
from meshwiki.core.parser import parse_wiki_content

class TestPageCountMacro:
    def test_renders_count(self):
        # With engine mocked/initialized
        html = parse_wiki_content("Total: <<PageCount>>")
        assert "page-count" in html

    def test_without_engine(self):
        html = parse_wiki_content("Total: <<PageCount>>")
        assert "macro-unavailable" in html or "?" in html
```

For macros that use the graph engine, see `tests/test_graph_integration.py` for the pattern of initializing the engine with test data.

## Existing Extensions

| Extension | Syntax | Type | File |
|-----------|--------|------|------|
| Wiki Links | `[[Page]]`, `[[Page\|Text]]` | InlineProcessor | `parser.py` |
| Strikethrough | `~~text~~` | InlineProcessor (SimpleTag) | `parser.py` |
| MetaTable | `<<MetaTable(filters, \|\|cols\|\|)>>` | Preprocessor | `parser.py` |
| Task Lists | `- [ ] item`, `- [x] item` | pymdownx extension | (third-party) |
| Tables | `\| A \| B \|` | extra extension | (built-in) |
| Fenced Code | `` ```lang `` | extra extension | (built-in) |
| Smart Quotes | `"text"` → "text" | smarty extension | (built-in) |
| TOC | headings → `[TOC]` | toc extension | (built-in) |
