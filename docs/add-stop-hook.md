# Add Stop Hook (Local Mac)

Run these two commands in your Mac terminal, one at a time.

## Step 1 — write the script

```
cat > /tmp/add_hook.py << 'EOF'
import json, pathlib

p = pathlib.Path.home() / '.claude/settings.json'
cfg = json.loads(p.read_text())
hook = {
    'matcher': '',
    'hooks': [{
        'type': 'command',
        'command': 'bash $HOME/projects/depthfusion/scripts/push-project-context.sh',
        'timeout': 30000
    }]
}
cfg.setdefault('hooks', {}).setdefault('Stop', []).append(hook)
p.write_text(json.dumps(cfg, indent=2))
print('Done:', p)
EOF
```

## Step 2 — run it

```
python3 /tmp/add_hook.py
```

Expected output: `Done: /Users/gregmorris/.claude/settings.json`
