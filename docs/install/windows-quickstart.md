# Windows Quickstart — DepthFusion

This guide installs DepthFusion on Windows 10/11 and registers it with Claude Desktop.

---

## Prerequisites

- **Python 3.11+** — download from [python.org](https://python.org) (check "Add Python to PATH" during install)
- **Git** — download from [git-scm.com](https://git-scm.com)
- **Claude Desktop** — installed and signed in

---

## Installation

### 1. Clone the repository

Open PowerShell (or Git Bash):

```powershell
git clone https://github.com/gregdigittal/depthfusion
cd depthfusion
```

### 2. Run the installer

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer will:
- Create a Python virtual environment at `%USERPROFILE%\.depthfusion-venv`
- Install DepthFusion and its dependencies
- Prompt you for your DepthFusion API key
- Register the MCP server with Claude Desktop

### 3. Get your API key

Go to **claude.ai/settings → API Keys** and create a new key.

> **Important:** This is your *DepthFusion* API key — it is different from any Claude Code subscription key you may have.

### 4. Restart Claude Desktop

Close and reopen Claude Desktop to load the DepthFusion MCP server.

### 5. Verify the installation

Open a new Claude Desktop conversation and run:

```
depthfusion_status
```

You should see a status response confirming DepthFusion is active.

---

## Alternative: Python installer

For advanced users or CI environments:

```powershell
# Interactive
py install.py

# Non-interactive (CI)
$Env:DEPTHFUSION_API_KEY = "your-key-here"
py src\depthfusion\install\install.py --non-interactive
```

---

## Troubleshooting

### "Python not found" or "py not found"

Python is not on your PATH. Re-run the Python installer and check **"Add Python to PATH"**, or add it manually:

```powershell
$Env:PATH += ";$Env:LOCALAPPDATA\Programs\Python\Python312"
```

### "Execution policy" error

Allow local scripts to run for your user account:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then re-run the installer.

### MCP server not appearing in Claude Desktop

Check that the config file was written correctly:

```powershell
notepad "$Env:APPDATA\Claude\claude_desktop_config.json"
```

You should see a `depthfusion` entry under `mcpServers`. If the file is missing or malformed, re-run the installer.

### API key was rejected during install

If you see "That looks like a Claude Code API key" — you've entered the wrong key. Use the key from **claude.ai/settings → API Keys**, not a Claude Code subscription credential.

---

## Uninstall

To remove DepthFusion:

1. Delete the virtual environment: `Remove-Item -Recurse -Force "$HOME\.depthfusion-venv"`
2. Remove the `depthfusion` entry from `%APPDATA%\Claude\claude_desktop_config.json`
3. Delete the env file: `Remove-Item "$Env:APPDATA\Claude\depthfusion.env"`
4. Restart Claude Desktop
