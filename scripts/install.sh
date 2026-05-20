#!/usr/bin/env bash
# DepthFusion installer — Mac/Linux
# Usage: bash scripts/install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${DEPTHFUSION_VENV_PATH:-$HOME/.depthfusion-venv}"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ENV_FILE="$CONFIG_DIR/depthfusion.env"
DESKTOP_CONFIG="$CONFIG_DIR/claude_desktop_config.json"

echo "DepthFusion Installer"
echo "====================="

# 1. Python version check
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.10+ and try again." >&2
    exit 1
fi
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "Error: Python 3.10+ required (found $PY_VER)" >&2
    exit 1
fi
echo "✓ Python $PY_VER"

# 2. Create venv
echo "Creating virtual environment at $VENV_PATH ..."
python3 -m venv "$VENV_PATH"
echo "✓ Virtual environment created"

# 3. Install DepthFusion
echo "Installing DepthFusion (this may take a minute) ..."
"$VENV_PATH/bin/pip" install --quiet -e "$REPO_ROOT[local]"
echo "✓ DepthFusion installed"

# 4. Get API key
echo ""
echo "Get your DepthFusion API key from: claude.ai/settings → API Keys"
echo "(This is NOT the same as your Claude Code subscription key)"
echo ""
# shellcheck disable=SC2162
read -s -p "DEPTHFUSION_API_KEY: " API_KEY
echo ""

# Guard: refuse Claude Code's own billing key
if echo "$API_KEY" | grep -qE '^sk-ant-api03-'; then
    echo "" >&2
    echo "Error: That looks like a Claude Code API key (used for subscription billing)." >&2
    echo "Your DepthFusion API key is different — get it from claude.ai/settings → API Keys." >&2
    exit 1
fi

if [ -z "$API_KEY" ]; then
    echo "Error: API key cannot be empty." >&2
    exit 1
fi

# 5. Write env file (permissions: current user only)
mkdir -p "$CONFIG_DIR"
printf 'DEPTHFUSION_API_KEY=%s\n' "$API_KEY" > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "✓ API key saved to $ENV_FILE"

# 6. Register MCP server in claude_desktop_config.json
PYTHON_BIN="$VENV_PATH/bin/python"
mkdir -p "$CONFIG_DIR"

# Backup existing config before any mutation
if [ -f "$DESKTOP_CONFIG" ]; then
    BACKUP_STAMP=$(date +%Y%m%d-%H%M%S)
    cp "$DESKTOP_CONFIG" "${DESKTOP_CONFIG}.bak-${BACKUP_STAMP}"
    echo "  (backed up existing config to ${DESKTOP_CONFIG}.bak-${BACKUP_STAMP})"
fi

if [ ! -f "$DESKTOP_CONFIG" ]; then
    python3 - "$DESKTOP_CONFIG" "$PYTHON_BIN" "$ENV_FILE" <<'PYEOF'
import json, os, sys, tempfile
config_path, python_bin, env_file = sys.argv[1], sys.argv[2], sys.argv[3]
config = {"mcpServers": {"depthfusion": {"command": python_bin, "args": ["-m", "depthfusion.mcp"], "env": {"DEPTHFUSION_ENV_FILE": env_file}}}}
d = os.path.dirname(config_path)
fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
try:
    with os.fdopen(fd, "w") as tf:
        json.dump(config, tf, indent=2)
    os.replace(tmp, config_path)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
PYEOF
else
    python3 - "$DESKTOP_CONFIG" "$PYTHON_BIN" "$ENV_FILE" <<'PYEOF'
import json, os, sys, tempfile
config_path, python_bin, env_file = sys.argv[1], sys.argv[2], sys.argv[3]
with open(config_path) as f:
    try:
        config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f"Error: {config_path} is not valid JSON ({exc}). Fix or remove it, then re-run the installer.")
config.setdefault("mcpServers", {})
if not isinstance(config["mcpServers"], dict):
    sys.exit(f"Error: 'mcpServers' in {config_path} is not an object. Cannot safely merge. Fix the file and re-run.")
config["mcpServers"]["depthfusion"] = {"command": python_bin, "args": ["-m", "depthfusion.mcp"], "env": {"DEPTHFUSION_ENV_FILE": env_file}}
d = os.path.dirname(config_path)
fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
try:
    with os.fdopen(fd, "w") as tf:
        json.dump(config, tf, indent=2)
    os.replace(tmp, config_path)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
PYEOF
fi
echo "✓ MCP server registered in $DESKTOP_CONFIG"

echo ""
echo "Installation complete. Restart Claude Desktop to activate DepthFusion."
