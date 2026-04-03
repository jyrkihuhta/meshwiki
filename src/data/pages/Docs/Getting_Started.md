---
title: Getting Started
tags:
  - tutorial
  - documentation
status: complete
author: admin
---

# Getting Started

Welcome to MeshWiki! This guide covers the basics of creating and navigating wiki pages.

## Creating Pages

1. Click **New Page** or visit `/page/YourPageName/edit`
2. Write your content using [[Docs/Markdown Guide|Markdown]]
3. Add frontmatter at the top for metadata (title, tags, status)
4. Click **Save** or press `Ctrl+S`

## Frontmatter

Every page can have YAML frontmatter at the top:

```yaml
---
title: My Page Title
tags:
  - topic1
  - topic2
status: draft
author: yourname
---
```

This metadata is:
- Displayed in the page header
- Queryable via [[Docs/MetaTable Queries|MetaTable]]
- Visible in the [[Docs/Architecture Overview|graph engine]]

## Linking Pages

Use double brackets to link pages:

| Syntax | Result |
|--------|--------|
| `[[PageName]]` | Link showing page name |
| `[[PageName\|Custom Text]]` | Link with display text |

Missing pages show in red — click them to create the page.

## Navigation

- **Search** - Use the search box in the header (`Ctrl+K` to focus)
- **Tags** - Click any tag to filter pages, or visit `/tags`
- **Graph** - Visit `/graph` for an interactive visualization
- **Backlinks** - Scroll down on any page to see what links to it

## Editor Features

The editor includes:

- **Toolbar** - Bold, italic, headings, links, wiki links
- **Keyboard shortcuts** - `Ctrl+B` bold, `Ctrl+I` italic, `Ctrl+K` link, `Ctrl+S` save
- **Live preview** - Toggle with the Preview button or `Ctrl+P`
- **Autocomplete** - Type `[[` to get page suggestions
- **Unsaved changes warning** - Won't lose work accidentally

## Next Steps

- Read the [[Docs/Markdown Guide]] for formatting details
- Learn about [[Docs/MetaTable Queries]] for metadata queries
- Check the [[Project Roadmap]] for planned features
