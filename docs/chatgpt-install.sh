#!/usr/bin/env python3
"""Install the DepthFusion MCP config for ChatGPT Desktop on macOS."""
import json, os

TOKEN = "3cea56481975dc53587e8d99cfa989c3ab8b1c3e5e44792443832f4cf8c1f317"

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

os.makedirs(dest_dir, exist_ok=True)
with open(dest_file, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print(f"Written: {dest_file}")
print("Restart ChatGPT Desktop, then ask it to call depthfusion_status.")
