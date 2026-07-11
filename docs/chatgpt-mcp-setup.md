# ChatGPT MCP Setup Guide — DepthFusion

Connect ChatGPT Desktop to DepthFusion's MCP server for persistent memory and knowledge graph access.

---

## Prerequisites

- **ChatGPT Desktop for macOS** — MCP support requires the version released in early 2025 (build 1.2025.x or later). Update via the app's menu: ChatGPT → Check for Updates.
- A **DepthFusion MCP token** (see §3 below).

---

## 1. Create the Config File

Create or edit:

```
~/Library/Application Support/com.openai.chat/mcp.json
```

Paste this content (replace `<YOUR_TOKEN>`):

```json
{
  "mcpServers": {
    "depthfusion": {
      "type": "sse",
      "url": "https://mcp.tonracein.com/sse",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

Restart ChatGPT Desktop after saving.

---

## 2. Find Your Token

The token lives in your local environment file:

```bash
cat ~/.claude/depthfusion.env
```

Look for the line:

```
DEPTHFUSION_MCP_TOKEN=df_...
```

Copy the value after `=` and paste it as `<YOUR_TOKEN>` in the config above.

---

## 3. Verify the Connection

In a new ChatGPT conversation, type:

> Call the `depthfusion_status` tool and show me the raw result.

A successful response looks like:

```json
{ "status": "ok", "version": "...", "projects": [...] }
```

If you see a `401 Unauthorized` error, re-check the token in `mcp.json`.

---

## 4. Tool Reference

All 30 tools available via this server. Tools marked **[Claude Code only]** are designed for the Claude Code agentic workflow and have limited utility in ChatGPT.

| Tool | Description | Note |
|---|---|---|
| `depthfusion_status` | Server health and connected project list | |
| `depthfusion_list_projects` | List all registered projects | |
| `depthfusion_list_providers` | List available LLM providers | |
| `depthfusion_set_scope` | Set the active project scope for a session | |
| `depthfusion_publish_context` | Publish a context snapshot to the knowledge store | |
| `depthfusion_retrieve_context` | Retrieve stored context by query | |
| `depthfusion_recall_relevant` | Semantic search over stored memories | |
| `depthfusion_research_topic` | Research a topic against stored knowledge | |
| `depthfusion_record_decision` | Record an architectural or design decision | |
| `depthfusion_record_incident` | Record an incident or outage event | |
| `depthfusion_report_outcome` | Report the outcome of a task or experiment | |
| `depthfusion_query_telemetry` | Query stored telemetry data | |
| `depthfusion_graph_status` | Get knowledge graph statistics | |
| `depthfusion_bridge` | Bridge context between projects | |
| `depthfusion_confirm_discovery` | Confirm a discovered pattern or insight | |
| `depthfusion_mark_superseded` | Mark a stored item as superseded | |
| `depthfusion_recommend_model` | Get a model recommendation for a task | |
| `depthfusion_ingest_conversation` | Ingest a conversation into the knowledge store | |
| `depthfusion_ingest_project` | Ingest a project into the knowledge store | |
| `depthfusion_compress_session` | Compress session data for storage | **[Claude Code only]** |
| `depthfusion_graph_traverse` | Traverse the knowledge graph | **[Claude Code only]** |
| `depthfusion_session_seed` | Seed context at session start | **[Claude Code only]** |
| `depthfusion_tag_session` | Tag the current session | **[Claude Code only]** |
| `depthfusion_auto_learn` | Trigger automatic learning from session | **[Claude Code only]** |
| `depthfusion_set_memory_score` | Manually adjust a memory importance score | **[Claude Code only]** |
| `depthfusion_pin_discovery` | Pin a discovery for high-priority recall | **[Claude Code only]** |
| `depthfusion_recall_feedback` | Submit feedback on a recall result | **[Claude Code only]** |
| `depthfusion_record_telemetry` | Record telemetry from an agent session | **[Claude Code only]** |
| `depthfusion_register_project` | Register a new project with DepthFusion | **[Claude Code only]** |
| `depthfusion_sync_project` | Sync project metadata | **[Claude Code only]** |

**19 tools** are fully usable in ChatGPT. **11 tools** are designed for Claude Code's agentic loop — they will work but have limited practical value in a chat context.

---

## 5. Troubleshooting

### 401 Unauthorized

- Token is missing or wrong in `mcp.json`. Re-check with `cat ~/.claude/depthfusion.env`.
- Ensure there is no trailing whitespace or newline in the token value.
- Confirm `mcp.json` is valid JSON: `python3 -m json.tool ~/Library/Application\ Support/com.openai.chat/mcp.json`

### SSE Connection Drops / Tool List Empty

- The SSE connection closes after idle periods. ChatGPT reconnects automatically on the next tool call; this is normal.
- If tools never appear: restart ChatGPT Desktop. If still missing, verify the `url` field points to `https://mcp.tonracein.com/sse` (not `/mcp` or `/`).
- Check server status: `curl -I https://mcp.tonracein.com/sse` — expect `200 OK` with `Content-Type: text/event-stream`.

### Config File Not Picked Up

- The file must be at exactly `~/Library/Application Support/com.openai.chat/mcp.json`.
- ChatGPT reads this file at startup only — a full restart is required after any edit.
- Confirm the file exists: `ls -la ~/Library/Application\ Support/com.openai.chat/mcp.json`
