#!/usr/bin/env python3
"""Install the DepthFusion MCP config for ChatGPT Desktop on macOS."""
import getpass, json, os

TOKEN = getpass.getpass("Paste your DEPTHFUSION_MCP_TOKEN: ").strip()
if not TOKEN:
    raise SystemExit("No token provided.")

config = {
    "mcpServers": {
        "depthfusion": {
            "type": "sse",
            "url": "https://mcp.tonracein.com/sse",
            "headers": {
                "Authorization": f"Bearer {TOKEN}"
            }
        }
    }
}

dest_dir = os.path.expanduser("~/Library/Application Support/com.openai.chat")
dest_file = os.path.join(dest_dir, "mcp.json")

os.makedirs(dest_dir, mode=0o700, exist_ok=True)
os.chmod(dest_dir, 0o700)

fd = os.open(dest_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print(f"Written: {dest_file} (mode 600)")
print("Restart ChatGPT Desktop, then ask it to call depthfusion_status.")
