---
title: Wiki Best Practices
tags:
  - documentation
  - guidelines
status: active
author: admin
priority: medium
---

# Wiki Best Practices

Guidelines for maintaining a well-organized wiki.

## Page Naming

- Use descriptive names: `Python Development` not `Dev`
- Use title case: `Getting Started` not `getting started`
- Avoid special characters in page names
- Underscores in URLs map to spaces in names

## Frontmatter

Always include frontmatter with at least:

```yaml
---
title: Human-Readable Title
tags:
  - relevant-tag
status: draft | active | complete | archived
author: yourname
---
```

### Status Values

| Status | Meaning |
|--------|---------|
| `draft` | Work in progress, may be incomplete |
| `active` | Current and maintained |
| `complete` | Finished, no more updates expected |
| `archived` | Historical, kept for reference |

## Linking

- Link generously -- connections make the wiki valuable
- Use display text for clarity: `[[Architecture Overview|architecture docs]]`
- Create pages before linking when possible (avoids red links)
- Check backlinks to see if a page is well-connected

## Structure

- Start with a clear `# Heading`
- Use headings to create a scannable outline (generates TOC)
- Keep pages focused on one topic
- Use tables for structured data
- Use task lists for action items

## MetaTable Dashboards

Create dashboard pages using [[Docs/MetaTable Queries]]:

```
<<MetaTable(status=active, ||name||author||priority||)>>
<<MetaTable(status=draft, ||name||author||)>>
```

## Tags

- Use lowercase, hyphenated tags: `getting-started` not `Getting Started`
- Be consistent -- check `/tags` before creating new ones
- Common tags: `documentation`, `tutorial`, `project`, `meeting`

## Related

- [[Docs/Getting Started]] - New user guide
- [[Docs/Markdown Guide]] - Formatting reference
