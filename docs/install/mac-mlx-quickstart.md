# mac-mlx Quickstart — Apple Silicon DepthFusion + MLX Inference

> **Target:** Apple Silicon Mac (M1/M2/M3/M4/M5 family) with macOS 13+
> **Time:** ~20 minutes hands-on (first MLX model download adds ~15–25 GB; budget extra time)
> **Produces:** DepthFusion in `mac-mlx` mode, MLX inference on-device, launchd auto-restart, Claude Desktop + Claude Code CLI integration
> **Hardware minimum:** 24 GB unified memory recommended for Gemma 4 26B 4-bit; 16 GB works with a smaller model

At the end you'll have:

- DepthFusion REST API on `127.0.0.1:7300` (27 MCP tools)
- MLX-LM inference server on `127.0.0.1:8080` (Gemma 4 26B 4-bit by default)
- Both services auto-start and auto-restart on crash via launchd
- `depthfusion` registered as a **global** (user-level) MCP in Claude Code CLI
- `depthfusion` registered in Claude Desktop

---

## 0. Prerequisites

```bash
# Python 3.11 or 3.12 recommended; 3.10 minimum.
python3 --version

# Homebrew (for git, if not already present)
# Install from https://brew.sh if absent.
brew --version

# Confirm Apple Silicon (arm64) — MLX does not run on Intel Macs
uname -m    # must print: arm64
```

**Python note:** macOS ships an outdated Python. Install a current version via Homebrew:

```bash
brew install python@3.12
# then use python3.12 in the venv command below
```

---

## 1. Clone and install

```bash
cd ~/projects
git clone https://github.com/gregdigittal/depthfusion.git
cd depthfusion

# Create venv — use python3.12 or whichever brew-installed version you have
python3.12 -m venv .venv
source .venv/bin/activate

# Install mac-mlx extras (includes mlx-lm, sentence-transformers, hnswlib)
pip install -e ".[mac-mlx,hnsw]"
```

The `mac-mlx` extra installs `mlx-lm` and its dependencies. The first `pip install` may take 3–5 minutes on a fast connection.

---

## 2. Download the MLX model

```bash
# This downloads ~15 GB; run in a terminal you can leave alone.
# The 4-bit Gemma 4 26B model provides good quality with manageable RAM.
python3 -c "
from mlx_lm import load
load('mlx-community/gemma-4-26b-a4b-it-4bit')
print('Model ready.')
"
```

The model is cached in `~/.cache/huggingface/hub/`. Subsequent starts use the cache.

---

## 3. Write the MLX inference server script

DepthFusion uses a thin wrapper script to serve MLX models via an OpenAI-compatible API at port 8080:

```bash
# Verify the script exists (it ships with DepthFusion >= v1.2.0)
ls scripts/mlx-serve-direct.py
```

If the script is absent (older checkout):

```bash
cat > scripts/mlx-serve-direct.py << 'EOF'
#!/usr/bin/env python3
"""Minimal OpenAI-compatible MLX inference server."""
import argparse, sys
from mlx_lm.server import run_server

p = argparse.ArgumentParser()
p.add_argument("--model", required=True)
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=8080)
args = p.parse_args()
run_server(model=args.model, host=args.host, port=args.port)
EOF
chmod +x scripts/mlx-serve-direct.py
```

---

## 4. Create the launchd plist files

macOS uses **launchd** (via `~/Library/LaunchAgents/`) instead of systemd. These plists auto-start both services at login and restart them on crash.

### MLX inference server plist

```bash
cat > ~/Library/LaunchAgents/com.depthfusion.mlx-server.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.mlx-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/projects/depthfusion/.venv/bin/python3</string>
        <string>/Users/YOUR_USERNAME/projects/depthfusion/scripts/mlx-serve-direct.py</string>
        <string>--model</string>
        <string>mlx-community/gemma-4-26b-a4b-it-4bit</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/mlx-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/mlx-server.log</string>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/projects/depthfusion</string>
</dict>
</plist>
EOF
```

### DepthFusion REST API plist

```bash
cat > ~/Library/LaunchAgents/com.depthfusion.rest.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.depthfusion.rest</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/projects/depthfusion/.venv/bin/python3</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>depthfusion.api.rest:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>7300</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DEPTHFUSION_MODE</key>
        <string>mac-mlx</string>
        <key>DEPTHFUSION_GEMMA_URL</key>
        <string>http://127.0.0.1:8080/v1</string>
        <key>DEPTHFUSION_GEMMA_MODEL</key>
        <string>mlx-community/gemma-4-26b-a4b-it-4bit</string>
        <key>DEPTHFUSION_HNSW_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_GRAPH_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_VECTOR_SEARCH_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_TIER_AUTOPROMOTE</key>
        <string>true</string>
        <key>DEPTHFUSION_RERANKER_ENABLED</key>
        <string>true</string>
        <key>DEPTHFUSION_EMBEDDING_BACKEND</key>
        <string>local</string>
        <key>DEPTHFUSION_TIER_THRESHOLD</key>
        <string>500</string>
    </dict>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/depthfusion-rest.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/depthfusion-rest.log</string>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/projects/depthfusion</string>
</dict>
</plist>
EOF
```

**Replace `YOUR_USERNAME` with your actual macOS username in both files.** Do this before loading:

```bash
USERNAME=$(whoami)
sed -i '' "s/YOUR_USERNAME/$USERNAME/g" \
    ~/Library/LaunchAgents/com.depthfusion.mlx-server.plist \
    ~/Library/LaunchAgents/com.depthfusion.rest.plist
```

---

## 5. Load the services

```bash
# Load MLX inference server first — REST API connects to it at startup
launchctl load ~/Library/LaunchAgents/com.depthfusion.mlx-server.plist
launchctl load ~/Library/LaunchAgents/com.depthfusion.rest.plist
```

> **Critical:** `launchctl load` starts the service immediately. Without this step, `RunAtLoad=true` only applies to the *next* login — the service will not be running now.

Wait ~30 seconds for the MLX server to load the model weights into RAM, then verify:

```bash
# Both should appear with a PID (not a dash)
launchctl list | grep depthfusion

# REST API health check
curl -s http://127.0.0.1:7300/health
# → {"status":"ok"}

# Confirm 27 tools are active
curl -s http://127.0.0.1:7300/status | python3 -m json.tool | grep tool_count
```

---

## 6. Register with Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "depthfusion": {
      "command": "/Users/YOUR_USERNAME/projects/depthfusion/.venv/bin/python3",
      "args": ["-m", "depthfusion.mcp.server"],
      "env": {
        "DEPTHFUSION_MODE": "mac-mlx",
        "DEPTHFUSION_GEMMA_URL": "http://127.0.0.1:8080/v1",
        "DEPTHFUSION_GEMMA_MODEL": "mlx-community/gemma-4-26b-a4b-it-4bit",
        "DEPTHFUSION_HNSW_ENABLED": "true",
        "DEPTHFUSION_GRAPH_ENABLED": "true",
        "DEPTHFUSION_VECTOR_SEARCH_ENABLED": "true",
        "DEPTHFUSION_TIER_AUTOPROMOTE": "true",
        "DEPTHFUSION_RERANKER_ENABLED": "true",
        "DEPTHFUSION_EMBEDDING_BACKEND": "local",
        "DEPTHFUSION_TIER_THRESHOLD": "500"
      }
    }
  }
}
```

Replace `YOUR_USERNAME`. Restart Claude Desktop after saving.

---

## 7. Register with Claude Code CLI (global / user-level)

The following registers depthfusion as a **user-level** MCP (available in every project, not just one):

```bash
claude mcp add depthfusion \
  --scope user \
  /Users/$(whoami)/projects/depthfusion/.venv/bin/python3 \
  -m depthfusion.mcp.server
```

Verify it appears under **User MCPs** (not project-scoped):

```bash
claude mcp list
# depthfusion should show as "Local" under user-level MCPs
```

Then set the env vars for the CLI session. The easiest approach is to add these to your shell profile (`~/.zshrc` or `~/.zprofile`):

```bash
export DEPTHFUSION_MODE=mac-mlx
export DEPTHFUSION_GEMMA_URL=http://127.0.0.1:8080/v1
export DEPTHFUSION_GEMMA_MODEL=mlx-community/gemma-4-26b-a4b-it-4bit
export DEPTHFUSION_HNSW_ENABLED=true
export DEPTHFUSION_GRAPH_ENABLED=true
export DEPTHFUSION_VECTOR_SEARCH_ENABLED=true
export DEPTHFUSION_TIER_AUTOPROMOTE=true
export DEPTHFUSION_RERANKER_ENABLED=true
export DEPTHFUSION_EMBEDDING_BACKEND=local
export DEPTHFUSION_TIER_THRESHOLD=500
```

---

## 8. Verify end-to-end

```bash
# Smoke-test recall (returns 0 results on a fresh store — that's correct)
curl -s -X POST http://127.0.0.1:7300/recall \
  -H "Content-Type: application/json" \
  -d '{"query":"test","top_k":3}' | python3 -m json.tool

# Expected response shape:
# {
#   "results": [],
#   "strategy": "bm25-only",      ← switches to "bm25+hnsw-fused" after first publish
#   "hnsw_available": true,
#   "sources_scanned": 0
# }
```

**Note on `strategy`:** On a fresh store with no published contexts, `strategy` will be `"bm25-only"` even with HNSW enabled — there are no vectors to index yet. After your first `depthfusion_publish_context` call, the strategy switches to `"bm25+hnsw-fused"`.

---

## Managing the services

```bash
# Stop a service
launchctl unload ~/Library/LaunchAgents/com.depthfusion.rest.plist

# Start it again
launchctl load ~/Library/LaunchAgents/com.depthfusion.rest.plist

# View logs (both services log to /tmp/)
tail -f /tmp/depthfusion-rest.log
tail -f /tmp/mlx-server.log

# List running services
launchctl list | grep depthfusion

# Force restart (unload then load)
launchctl unload ~/Library/LaunchAgents/com.depthfusion.rest.plist && \
launchctl load ~/Library/LaunchAgents/com.depthfusion.rest.plist
```

`KeepAlive=true` in both plists means launchd automatically restarts either service if it crashes. You should not normally need to restart manually.

---

## Troubleshooting

### REST API not starting

Check `launchctl list | grep depthfusion`. If the PID column shows `-`, the service has not been loaded or failed to start:

```bash
# Was it ever loaded?
launchctl list | grep com.depthfusion.rest
# If absent: run launchctl load ...

# If present but PID is -: check the log
tail -50 /tmp/depthfusion-rest.log
```

Common causes:
- **`launchctl load` was never run** — the plist is installed but the service hasn't been started for this session. Run `launchctl load` explicitly; `RunAtLoad=true` only applies from the *next* login onwards.
- **Python path wrong** — check that `.venv/bin/python3` exists and is executable.
- **Port 7300 already in use** — `lsof -i :7300`.

### MLX server slow to start

The model loads from `~/.cache/huggingface/hub/` on every start. On an M5 Max with 48 GB RAM this takes ~15 seconds. Check `tail -f /tmp/mlx-server.log` — you'll see `Application startup complete` when ready.

### `strategy: "bm25-only"` after first publish

If you published a context but HNSW still shows `bm25-only`, the embedding backend may not be configured. Confirm `DEPTHFUSION_EMBEDDING_BACKEND=local` is set in the plist `EnvironmentVariables` section, then restart the REST service.

### `zsh: parse error` when pasting Python scripts

Multi-line Python inside `python3 -c "..."` often causes parse errors in zsh. Write the script to a `.py` file first:

```bash
# Write script to a temp file, then run it — avoids zsh quoting issues
cat > /tmp/my-script.py << 'EOF'
# your Python here
EOF
python3 /tmp/my-script.py
```

### Duplicate plist labels

If you previously installed `com.depthfusion.mlx-serve.plist` (note: `-serve` not `-server`), both plists may exist. Check:

```bash
ls ~/Library/LaunchAgents/ | grep depthfusion
launchctl list | grep depthfusion
```

The label inside the plist (`<key>Label</key>`) is what launchd uses — not the filename. Two plists with different filenames but the same `Label` will conflict. Unload and remove any stale plist before loading the new one.

---

## What's different vs the VPS guides

| | VPS (Linux) | Mac MLX |
|---|---|---|
| Inference | vLLM (GPU) or Haiku API (CPU) | MLX-LM, on-device Apple Silicon GPU |
| Service management | `systemctl --user` | `launchctl` + `~/Library/LaunchAgents/` |
| Auto-restart | systemd `Restart=always` | launchd `KeepAlive=true` |
| Mode env var | `vps-gpu` / `vps-cpu` | `mac-mlx` |
| Port 8080 | vLLM REST | MLX-LM REST |
| Tool count | 32 tools | 27 tools (fabric tools not active without Redis) |
| Redis / fabric | Optional but supported | Not required; HNSW + graph work without it |
