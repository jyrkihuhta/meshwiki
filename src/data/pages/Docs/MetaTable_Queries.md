---
title: MetaTable Queries
tags:
  - documentation
  - metadata
  - features
status: complete
author: admin
---

# MetaTable Queries

MetaTable is a macro that queries page metadata and renders results as a table. It's inspired by [Graphingwiki](http://graphingwiki.python-hosting.com/).

## Syntax

```markdown
<<MetaTable(filters, ||column1||column2||column3||)>>
```

## Filters

| Operator | Syntax | Meaning |
|----------|--------|---------|
| Equals | `key=value` | Exact match |
| Contains | `key~=value` | Substring match |
| Regex | `key/=pattern` | Regular expression |

Multiple filters are comma-separated and AND'd together.

## Examples

### All pages with their status

```markdown
<<MetaTable(||name||status||tags||)>>
```

<<MetaTable(||name||status||tags||)>>

### Active pages only

```markdown
<<MetaTable(status=active, ||name||author||priority||)>>
```

<<MetaTable(status=active, ||name||author||priority||)>>

### Pages by a specific author

```markdown
<<MetaTable(author=alice, ||name||status||)>>
```

<<MetaTable(author=alice, ||name||status||)>>

### Documentation pages

```markdown
<<MetaTable(tags~=documentation, ||name||status||author||)>>
```

<<MetaTable(tags~=documentation, ||name||status||author||)>>

## How It Works

1. The Markdown preprocessor detects `<<MetaTable(...)>>` macros
2. Filters and columns are parsed from the arguments
3. The [[Docs/Architecture Overview|Rust graph engine]] queries the in-memory graph
4. Results are rendered as an HTML table
5. The table is stashed as raw HTML to prevent Markdown interference

## Requirements

MetaTable requires the Rust graph engine (`graph_core`). Without it, a "graph engine not available" message is shown instead.

## Tips

- Always include `name` as a column -- it links to each page
- Use `tags~=value` for substring matching on tag lists
- Combine multiple filters: `status=active, author=alice`
- MetaTable reads from YAML frontmatter at the top of each page

## Related

- [[Docs/Markdown Guide]] - General formatting syntax
- [[Docs/Architecture Overview]] - Graph engine details
