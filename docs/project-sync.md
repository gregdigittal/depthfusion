# Project Sync — DepthFusion

## Overview

DepthFusion can automatically sync project context (BACKLOG.md, CLAUDE.md, git log)
to its knowledge base when a Claude Code session ends.

## Setup

### 1. Register your project

Call the `depthfusion_register_project` MCP tool with:
- `slug`: short identifier (e.g. `depthfusion`)
- `name`: human-readable name
- `local_path`: absolute path on this VPS

### 2. Add the Claude Code Stop hook (manual step)

Edit `~/.claude/settings.json` and add to the `hooks.Stop` array:

```json
{
  "matcher": "",
  "hooks": [
    {
      "type": "command",
      "command": "bash /home/gregmorris/projects/depthfusion/scripts/push-project-context.sh"
    }
  ]
}
```

The hook auto-detects the active project from the current working directory.

### 3. Trigger a manual sync

Call `depthfusion_sync_project` with your project `slug`.

## What Gets Synced

- **BACKLOG.md** — epic/story/task summary with status
- **CLAUDE.md** — project instructions (capped at 8KB)
- **git log** — last 20 commits

## Output Location

Context files are written to:
`~/.claude/shared/project-context/<slug>/`

These are also published to the DepthFusion knowledge base for recall.
