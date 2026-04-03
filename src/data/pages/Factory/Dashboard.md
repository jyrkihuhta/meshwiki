---
title: Factory Dashboard
tags:
  - factory
---

# Factory Dashboard

## Needs Attention
<<MetaTable(type=task, status=blocked, ||name||priority||assignee||)>>

## Awaiting Decomposition Approval
<<MetaTable(type=task, status=decomposed, ||name||parent_task||estimation||)>>

## In Progress
<<MetaTable(type=task, status=in_progress, ||name||assignee||branch||)>>

## In Review
<<MetaTable(type=task, status=review, ||name||assignee||pr_url||)>>

## Agents
<<MetaTable(type=agent, ||name||agent_role||status||max_concurrent||)>>
