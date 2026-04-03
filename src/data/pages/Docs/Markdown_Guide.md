---
title: Markdown Guide
tags:
  - documentation
  - tutorial
  - markdown
status: complete
author: admin
---

# Markdown Guide

MeshWiki uses standard Markdown with several extensions. This page covers all supported syntax.

## Basic Formatting

| Syntax | Output |
|--------|--------|
| `**bold**` | **bold** |
| `*italic*` | *italic* |
| `~~strikethrough~~` | ~~strikethrough~~ |
| `` `inline code` `` | `inline code` |

## Headings

```markdown
# Heading 1
## Heading 2
### Heading 3
```

Headings automatically generate a table of contents sidebar on the page view.

## Links

### Standard Links

```markdown
[Link Text](https://example.com)
```

### Wiki Links

```markdown
[[PageName]]
[[PageName|Display Text]]
```

Wiki links are the primary way to connect pages. See [[Docs/Getting Started]] for more.

## Lists

### Unordered

- Item one
- Item two
  - Nested item
  - Another nested

### Ordered

1. First step
2. Second step
3. Third step

### Task Lists

- [x] Set up the wiki
- [x] Create example pages
- [ ] Add more content
- [ ] Invite collaborators

## Code Blocks

Fenced code blocks with language hints:

```python
def hello(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"
```

```rust
fn main() {
    println!("Hello from Rust!");
}
```

## Tables

```markdown
| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Cell 1   | Cell 2   | Cell 3   |
```

| Language | Use Case | Performance |
|----------|----------|-------------|
| Python | Web app, API | Good |
| Rust | Graph engine | Excellent |
| JavaScript | Frontend | Good |

## Blockquotes

> Knowledge is structured information.
> A wiki is a tool for structuring knowledge.

## MetaTable Macro

Query metadata across pages:

```markdown
<<MetaTable(status=active, ||name||status||author||)>>
```

See [[Docs/MetaTable Queries]] for the full reference.

## Related Pages

- [[Docs/Getting Started]] - Basic wiki usage
- [[Docs/MetaTable Queries]] - Advanced metadata queries
- [[Docs/Architecture Overview]] - How the parser works
