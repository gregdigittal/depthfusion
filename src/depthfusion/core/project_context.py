from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass
class BacklogSummary:
    project_slug: str
    epics: list
    stories: list
    tasks: list
    last_updated: str


def parse_backlog(backlog_path: Path) -> BacklogSummary:
    """Parse BACKLOG.md into structured summary."""
    text = backlog_path.read_text(encoding='utf-8')
    epics, stories, tasks = [], [], []
    current_epic_id = ''

    for line in text.splitlines():
        # Epic: ## E-nn: Title [status]
        m = re.match(r'^## (E-\d+):\s+(.+?)\s+\[(\w+)\]', line)
        if m:
            current_epic_id = m.group(1)
            epics.append({'id': m.group(1), 'title': m.group(2), 'status': m.group(3)})
            continue
        # Story: ### S-nn: Title
        m = re.match(r'^### (S-\d+):\s+(.+?)(?:\s+`(P\d)`)?(?:\s+`(\w+)`)?$', line)
        if m:
            stories.append({
                'id': m.group(1), 'title': m.group(2).strip(),
                'priority': m.group(3) or '', 'effort': m.group(4) or '',
                'epic_id': current_epic_id,
            })
            continue
        # Task: - [ ] T-nn: title  or  - [x] T-nn: title
        m = re.match(r'^- \[([ x])\] (T-\d+):\s+(.+)', line)
        if m:
            tasks.append({
                'id': m.group(2), 'title': m.group(3).strip(),
                'done': m.group(1) == 'x',
                'story_id': stories[-1]['id'] if stories else '',
            })

    return BacklogSummary(
        project_slug='',
        epics=epics, stories=stories, tasks=tasks,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )


def _format_backlog_context(slug: str, summary: BacklogSummary) -> str:
    lines = [
        f'# Backlog Summary: {slug}',
        f'Last synced: {summary.last_updated}',
        f'Epics: {len(summary.epics)}, Stories: {len(summary.stories)}, '
        f'Tasks: {len(summary.tasks)}',
        '',
    ]
    for epic in summary.epics:
        lines.append(f"## {epic['id']}: {epic['title']} [{epic['status']}]")
        epic_stories = [s for s in summary.stories if s.get('epic_id') == epic['id']]
        for s in epic_stories:
            lines.append(f"  - {s['id']}: {s['title']} {s.get('priority','')} {s.get('effort','')}")
    return '\n'.join(lines)


def sync_project(slug: str, local_path: str, publish_fn: Callable) -> dict:
    """Sync project context (BACKLOG, CLAUDE.md, git log) to DepthFusion KB."""
    project_dir = Path(local_path)
    results: dict = {}

    # Parse BACKLOG.md
    backlog_path = project_dir / 'BACKLOG.md'
    if backlog_path.exists():
        summary = parse_backlog(backlog_path)
        summary.project_slug = slug
        context_text = _format_backlog_context(slug, summary)
        publish_fn(
            slug=slug, content=context_text,
            tags=[slug, 'backlog', 'project-context'],
        )
        results['backlog'] = {
            'epics': len(summary.epics),
            'stories': len(summary.stories),
            'tasks': len(summary.tasks),
        }

    # CLAUDE.md (cap at 8KB)
    claude_md = project_dir / 'CLAUDE.md'
    if claude_md.exists():
        content = claude_md.read_text(encoding='utf-8', errors='replace')[:8000]
        publish_fn(
            slug=slug,
            content=f'# CLAUDE.md: {slug}\n\n{content}',
            tags=[slug, 'claude-md', 'project-context'],
        )
        results['claude_md'] = True

    # Recent git log (last 20 commits)
    try:
        log_out = subprocess.run(
            ['git', 'log', '--oneline', '-20'],
            capture_output=True, text=True, cwd=str(project_dir), timeout=10,
        ).stdout.strip()
        if log_out:
            publish_fn(
                slug=slug,
                content=f'# Recent commits: {slug}\n\n{log_out}',
                tags=[slug, 'git-log', 'project-context'],
            )
            results['git_log'] = True
    except Exception:
        pass

    return results
