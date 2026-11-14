# Domain: Business Logic

**Owner:** TBD
**Status:** MVP + graph integration + editor + search complete (M1-M8)
**Language:** Python

## Scope

Core wiki functionality in the Python/FastAPI layer:
- Page CRUD operations
- Markdown rendering
- Wiki link processing
- Template rendering
- HTMX interactions

**Not in scope:** Graph queries (graph-engine), auth (authentication), deployment (infrastructure)

## Current State

MVP + editor + navigation complete:
- [x] View, create, edit, delete pages
- [x] Markdown rendering with extensions
- [x] Wiki links (`[[Page]]` and `[[Page|Text]]`)
- [x] Missing page detection and styling
- [x] Task lists, tables, code blocks, strikethrough
- [x] HTMX-powered interactions
- [x] Split-pane editor with live preview (`POST /api/preview`)
- [x] Optional preview toggle (button + Ctrl+P, persisted in localStorage)
- [x] Toolbar (bold, italic, strikethrough, heading, code, link, wikilink)
- [x] Keyboard shortcuts (Ctrl+B/I/K/S/P)
- [x] Auto-growing textarea, unsaved changes warning
- [x] Wiki link autocomplete (`GET /api/autocomplete?q=`)
- [x] Frontmatter preserved in editor (raw content with YAML frontmatter shown)
- [x] PageMetadata allows extra fields (`extra="allow"` for custom frontmatter)
- [x] Header search box with instant HTMX results
- [x] Full search page (`/search?q=&tag=`)
- [x] Tag index page (`/tags`) with counts
- [x] TOC sidebar on page view
- [x] Breadcrumbs (`Home / PageTitle`)
- [x] Clickable tags linking to `/search?tag=...`
- [x] Recently modified section on home page
- [x] Delete button with confirmation dialog on page view
- [x] MetaTable skips fenced code blocks (renders as literal text inside `` ``` ``)

## Architecture

```
src/meshwiki/
├── main.py           # FastAPI routes + WebSocket endpoint
├── config.py         # Settings (pydantic-settings)
├── core/
│   ├── storage.py    # Abstract Storage + FileStorage (incl. search, tag filter)
│   ├── parser.py     # Markdown + WikiLink + MetaTable + TOC extensions
│   ├── models.py     # Page model
│   ├── graph.py      # Graph engine wrapper (optional import)
│   └── ws_manager.py # WebSocket connection manager + event fanout
├── templates/        # Jinja2 templates
│   ├── partials/     # HTMX fragments (preview, search results)
│   ├── search.html   # Full search results page
│   └── tags.html     # Tag index page
├── static/css/       # CSS
├── static/js/        # graph.js, editor.js
└── tests/            # Tests (204 tests)
```

## Design Patterns

### Storage Abstraction
```python
class Storage(ABC):
    async def get_page(name: str) -> Page | None
    async def save_page(name: str, content: str) -> Page
    async def delete_page(name: str) -> bool
    async def list_pages() -> list[str]
    async def page_exists(name: str) -> bool
    async def search_pages(query: str) -> list[dict]          # M8
    async def list_pages_with_metadata() -> list[Page]         # M8
    async def get_raw_content(name: str) -> str | None          # Raw file with frontmatter
    async def search_by_tag(tag: str) -> list[Page]            # M8
```

Current implementation: `FileStorage` (files on disk)
Future: May delegate existence checks to graph engine

#### Frontmatter tolerance

Page files are also written by hand, by git and by bots, so reading never
fails because of what a file contains:

- **Wrong YAML type for a declared field** (`title`, `uuid`, `tags`, `children`,
  `created`, `modified`) is coerced by `coerce_frontmatter()` in
  `core/models.py`: a scalar for a text field becomes text (the text the author
  wrote: `title: 1.10` stays `"1.10"`), a scalar for a list field becomes a
  one-item list, list items become text, nested lists are flattened.
- **A value with no sensible reading** (`created: yesterday`, `title: [a, b]`,
  `tags: {a: 1}`) leaves the field at its default and is kept under
  `<field>_invalid` in the extras, so nothing is lost on the next write. The
  write that first stores such a key (`update_frontmatter_field()`,
  `patch_frontmatter()`) logs `frontmatter_values_set_aside` with the page name;
  reading and scanning do not log it.
- **Frontmatter YAML cannot load** (a syntax error, or a tagged scalar PyYAML
  cannot build such as `draft: !!bool maybe`, for which it raises `KeyError`,
  `IndexError` or `AttributeError` rather than `YAMLError`) is not frontmatter:
  the whole text is the page body and the metadata is empty.
- **Reading is lenient, writing is strict.** `save_page()` normalises coercible
  values (`tags: python` is written back as a list) but raises
  `InvalidFrontmatterError` (HTTP 422) for a value it would have to set aside.
  `update_frontmatter_field()` / `patch_frontmatter()` convert the new value to
  the field's type, so they cannot write a file the reader rejects.
- **Unreadable files** (not UTF-8, permissions) are logged (`page_file_skipped`)
  and skipped by every scan (listing, search, tags, sidebar); the scan guard
  catches any exception, so an unforeseen failure in one file costs that page
  only. Addressed by name they raise `PageFileError`, which the app answers
  with an explanatory 500; the page can be deleted from the UI or overwritten
  through the API. A directory named `*.md` is not a page.
- **Extras are unvalidated.** Code that reads an extra field across many pages
  (sidebar tree, epic children) must not assume its type, nor that it is
  hashable (`status: [planned]` is a list: compare with `==`, do not look it up
  in a set or dict); see `_frontmatter_text()` in `main.py`. Sort timestamps
  with `datetime_sort_key()`: files may mix naive and timezone-aware datetimes.
- **Metadata leaves the app as JSON through `json_safe_metadata()`**
  (`core/models.py`), not `model_dump()`. YAML can produce values no JSON
  encoder accepts (`!!binary` data that is not UTF-8, a value that contains
  itself, `.nan`, `.inf`); one such page would fail the whole listing. They are
  replaced with text in the response only; the file keeps the value.

### Parser Pipeline
```
Raw Markdown → [Extensions] → HTML
                    ↓
         - extra (tables, code)
         - sane_lists
         - smarty (typography)
         - toc
         - pymdownx.tasklist
         - WikiLinkExtension (custom)
         - StrikethroughExtension (custom)
```

### HTMX Integration
- Check `HX-Request` header for partial responses
- Return fragments for dynamic updates
- Full page for direct navigation

## Completed Enhancements

### Backlinks Panel ✅
Displays pages linking to current page in `view.html`:
```python
# In view_page route
backlinks = sorted(engine.get_backlinks(name))
```

### MetaTable Macro ✅
Query pages by metadata, rendered as inline HTML table:
```markdown
<<MetaTable(status=draft, ||name||status||author||)>>
```
Filter operators: `key=value` (equals), `key~=sub` (contains), `key/=regex` (matches).
Implemented as Markdown `Preprocessor` in `parser.py`. Uses `htmlStash.store()` to prevent
Markdown parser from wrapping table HTML in `<p>` tags. Output wrapped in `<div class="metatable-wrapper">`
with dedicated CSS styling (alternating rows, hover, capitalized headers). Skips macros inside
fenced code blocks (`` ``` `` and `~~~`) — they render as literal text.

### Graph Visualization ✅
Interactive D3.js force graph at `/graph` with WebSocket live updates.

## Integration Points

| Component | Status | Implementation |
|-----------|--------|----------------|
| Page existence | ✅ Done | `graph_engine.page_exists()` with filesystem fallback |
| Backlinks | ✅ Done | `graph_engine.get_backlinks()` in view route |
| MetaTable | ✅ Done | `graph_engine.metatable()` via MetaTablePreprocessor |
| Visualization | ✅ Done | `/api/graph` + `/ws/graph` + D3.js |
| Search | ✅ Done | `FileStorage.search_pages()` (filesystem-based) |
| Editor preview | ✅ Done | `POST /api/preview` (server-side Markdown render) |
| Autocomplete | ✅ Done | `GET /api/autocomplete?q=` (page name matching) |
| Tags | ✅ Done | `FileStorage.search_by_tag()` + `/tags` route |
| TOC | ✅ Done | `parse_wiki_content_with_toc()` + sidebar in view |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHWIKI_DATA_DIR` | `data/pages` | Page storage directory |
| `MESHWIKI_DEBUG` | `false` | Debug mode |
| `MESHWIKI_APP_TITLE` | `MeshWiki` | Application title |
| `MESHWIKI_GRAPH_WATCH` | `true` | Enable file watching for graph engine |

## Code Conventions

- Async/await for all I/O operations
- Type hints on all function signatures
- Google-style docstrings for public functions
- HTTPException with appropriate status codes
- Jinja2 auto-escaping (careful with `| safe`)

## Testing Strategy

- Unit tests for parser, storage operations
- Integration tests for API routes (httpx TestClient)
- Test wiki link rendering (existing vs missing pages)
- Test HTMX partial responses
