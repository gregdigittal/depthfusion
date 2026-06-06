#!/usr/bin/env bash
# push-project-context.sh
# Syncs current project context to DepthFusion KB on Claude Code session end.
# Called by Claude Code Stop hook. Usage: bash push-project-context.sh [slug]
set -euo pipefail

# Resolve script directory reliably in bash. Inside the python heredoc below,
# __file__ is the literal '<stdin>' (the script is piped via `python3 -`), so it
# cannot be used to locate the depthfusion src dir. Resolve it here and
# interpolate into the heredoc instead.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DF_SRC="$SCRIPT_DIR/../src"

PROJECT_SLUG="${1:-}"
CWD="$(pwd)"
PROJECTS_JSON="$HOME/.depthfusion/projects.json"

# Auto-detect project slug from projects.json if not provided
if [ -z "$PROJECT_SLUG" ] && [ -f "$PROJECTS_JSON" ]; then
    PROJECT_SLUG="$(python3 - <<'PYEOF'
import json, sys, os
try:
    data = json.load(open(os.path.expanduser('~/.depthfusion/projects.json')))
    cwd = os.getcwd()
    for slug, p in data.items():
        lp = p.get('local_path', '').rstrip('/')
        if lp and (cwd == lp or cwd.startswith(lp + '/')):
            print(slug)
            sys.exit(0)
except Exception:
    pass
PYEOF
)" 2>/dev/null || true
fi

if [ -z "$PROJECT_SLUG" ]; then
    echo "[push-project-context] No registered project found for $CWD — skipping" >&2
    exit 0
fi

echo "[push-project-context] Syncing project: $PROJECT_SLUG" >&2

# Pass slug and src path via env vars; heredoc is quoted ('PYEOF') so no shell
# interpolation occurs inside Python — eliminates code-injection risk entirely.
DF_SRC="$DF_SRC" PROJECT_SLUG="$PROJECT_SLUG" python3 - <<'PYEOF' 2>&1 || true
import sys, os, json
from pathlib import Path

slug = os.environ['PROJECT_SLUG']
sys.path.insert(0, os.environ['DF_SRC'])

try:
    from depthfusion.core.project_context import sync_project
    from depthfusion.core.project_registry import ProjectRegistry
except ImportError as e:
    print(f"[push-project-context] Import error: {e}", file=sys.stderr)
    sys.exit(0)

registry = ProjectRegistry()
entry = registry.get(slug)
if not entry:
    print(f"[push-project-context] Project not in registry: {slug}", file=sys.stderr)
    sys.exit(0)

out_dir = Path.home() / '.claude' / 'shared' / 'project-context' / slug
out_dir.mkdir(parents=True, exist_ok=True)

def publish_fn(slug, content, tags):
    fname = tags[1] if len(tags) > 1 else 'context'
    out_path = out_dir / f'{fname}.md'
    tmp = out_path.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    os.replace(tmp, out_path)
    print(f"[push-project-context] Written: {out_path}")

results = sync_project(slug=slug, local_path=entry.local_path, publish_fn=publish_fn)
registry.update_last_synced(slug)
print(f"[push-project-context] Done: {json.dumps(results)}")
PYEOF
