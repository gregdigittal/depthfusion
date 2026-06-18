# Multi-Provider Context Bridge — Design Spec

**Date:** 2026-06-09
**Status:** Approved
**Approach:** C — OpenRouter for real-time delegation, native parsers for ingestion

---

## Goal

Add multi-provider memory continuity to DepthFusion so that:
1. Claude can delegate a prompt to GPT-4o / Gemini / DeepSeek / local models in real time, with shared memory flowing both ways
2. Past conversations from other AI tools can be bulk-imported into DepthFusion so Claude can recall them in future sessions

---

## Architecture Overview

A thin **Provider Bridge layer** is added alongside the existing backend stack. The memory core, MCP server, event store, and all 26 existing tools are unchanged.

```
Claude Desktop / Claude Code
        │  MCP (SSE, port 7301)
        ▼
┌─────────────────────────────────────────┐
│        DepthFusion MCP Server           │
│  (existing 26 tools + 3 new bridge tools)│
│                                         │
│  depthfusion_bridge()       ──────────► OpenRouterBackend ──► OpenRouter API
│  depthfusion_ingest_conversation() ◄──  ProviderParsers        (GPT-4o, Gemini,
│  depthfusion_list_providers()           [chatgpt|gemini|ds]     DeepSeek, Ollama)
└────────────┬────────────────────────────┘
             │ read/write memories
             ▼
     Existing memory core
     (GraphScope, HNSW+BM25, SQLite)
     sub_scope tag: "provider:openai/gpt-4o"
```

**Key constraints:**
- `OpenRouterBackend` extends `GemmaBackend` — ~40 lines, reuses all existing HTTP/retry/timeout logic
- Provider memories live in the same graph, namespaced by `sub_scope` tag — no schema migration
- All three new tools register in `mcp/tools/` alongside existing tools — no new server, no new port
- `OPENROUTER_API_KEY` missing → warning at startup, tools return "not configured" error (non-fatal)

---

## New Files

| Path | Purpose |
|---|---|
| `backends/openrouter.py` | `OpenRouterBackend(GemmaBackend)` — sets base URL and header |
| `mcp/tools/bridge.py` | Registers `depthfusion_bridge`, `depthfusion_ingest_conversation`, `depthfusion_list_providers` |
| `ingest/parsers/chatgpt.py` | Normalises ChatGPT `conversations.json` export |
| `ingest/parsers/gemini.py` | Normalises Google Takeout Gemini export |
| `ingest/parsers/deepseek.py` | Normalises DeepSeek export format |
| `ingest/parsers/generic.py` | Plain-text / unknown fallback parser |
| `tests/fixtures/` | Anonymised sample export files for parser unit tests |

---

## Tool Specifications

### `depthfusion_bridge(model, prompt, context_tags?)`

Real-time delegation with shared memory.

**Parameters:**
- `model` — OpenRouter model string: `"openai/gpt-4o"`, `"google/gemini-1.5-pro"`, `"deepseek/deepseek-chat"`, `"ollama/mistral"`, etc.
- `prompt` — the prompt to send to the provider
- `context_tags` — optional list of sub_scope tags to filter recalled memories

**Flow:**
1. Recall relevant memories (existing `recall_relevant` logic, filtered by `context_tags` if provided)
2. Inject recalled memories as system context in the outgoing messages array
3. Call `OpenRouterBackend.complete(model, messages)`
4. Store provider response as memory fragments tagged `sub_scope: "provider:{model}"`
5. Return the provider response to Claude

**Error behaviour:** if OpenRouter is unreachable or returns non-200, returns `{error, provider, model}` without writing to memory. Claude decides whether to retry.

---

### `depthfusion_ingest_conversation(provider, data)`

Bulk import of past conversations from other AI tools.

**Parameters:**
- `provider` — `"chatgpt"` | `"gemini"` | `"deepseek"` | `"generic"`
- `data` — raw conversation export (JSON string or plain text)

**Flow:**
1. Route to the correct parser based on `provider`
2. Parser normalises to `[{role, content, timestamp}]`
3. Extract meaningful fragments (assistant answers, key user turns — skip system boilerplate)
4. Store each fragment with `sub_scope: "provider:{provider}:ingested"`
5. Return `{fragments_stored, skipped, provider}`

**Error behaviour:** parse failures are soft — stores what it can, returns `{fragments_stored, skipped, errors[]}`. A completely unparseable file returns an error but does not crash the server.

**Provider export formats:**
- `chatgpt` — `conversations.json` from ChatGPT data export (list of conversation objects)
- `gemini` — Google Takeout Gemini format
- `deepseek` — DeepSeek conversation export
- `generic` — plain-text or unknown JSON; best-effort extraction

---

### `depthfusion_list_providers()`

Read-only status listing.

**Returns:**
- Which providers have API keys configured
- OpenRouter reachability status
- Memory count per provider namespace (`sub_scope` prefix)

---

## Backend: `OpenRouterBackend`

```python
# backends/openrouter.py
class OpenRouterBackend(GemmaBackend):
    base_url = "https://openrouter.ai/api/v1"

    def _extra_headers(self):
        return {"X-Title": "DepthFusion"}
```

Inherits all HTTP, retry, timeout, and streaming logic from `GemmaBackend`. The only differences are the base URL and the `X-Title` header OpenRouter recommends for routing attribution.

---

## Memory Namespacing

No schema migration required. Provider memories use the existing `sub_scope` tag mechanism:

| Source | sub_scope tag |
|---|---|
| Real-time bridge call | `provider:openai/gpt-4o` |
| Bridge call (Gemini) | `provider:google/gemini-1.5-pro` |
| Ingested from ChatGPT | `provider:chatgpt:ingested` |
| Ingested from Gemini | `provider:gemini:ingested` |

Recall queries can filter by sub_scope to retrieve cross-provider or provider-specific memories.

---

## Configuration

**New environment variable:**
- `OPENROUTER_API_KEY` — required for bridge tools
- `OPENROUTER_BASE_URL` — optional override, default `https://openrouter.ai/api/v1`

Startup behaviour: `OPENROUTER_API_KEY` missing → logged as warning (same pattern as `PEXELS_API_KEY`). Bridge tools register normally but return a "not configured" error when called. All other DepthFusion functionality is unaffected.

---

## Error Handling Summary

| Scenario | Behaviour |
|---|---|
| OpenRouter unreachable | `depthfusion_bridge` returns `{error, provider, model}`; no memory write |
| Invalid model string | OpenRouter returns 4xx; surfaced as error to caller |
| `OPENROUTER_API_KEY` missing | Warning at startup; tools return "not configured" |
| Unparseable conversation export | Soft failure: partial store + error list returned |
| Completely invalid ingestion data | Error returned; server continues running |

---

## Testing Plan

| Test | Type | Notes |
|---|---|---|
| ChatGPT parser | Unit | Uses `tests/fixtures/chatgpt-export-sample.json` |
| Gemini parser | Unit | Uses `tests/fixtures/gemini-export-sample.json` |
| DeepSeek parser | Unit | Uses `tests/fixtures/deepseek-export-sample.json` |
| Generic parser | Unit | Plain text and unknown JSON |
| `OpenRouterBackend` | Unit | Mocked HTTP — verifies header and base URL |
| `depthfusion_bridge` E2E | Integration | Mock OpenRouter server; verifies memory write after call |
| `depthfusion_list_providers` | Unit | Verifies response shape with/without API key configured |

No new E2E Playwright tests — existing MCP health check covers server startup; bridge tools are opt-in.

---

## Out of Scope (v1)

- Automatic polling of provider conversation APIs (scheduled sync)
- Native per-provider adapters without OpenRouter routing
- UI for browsing cross-provider memories
- Streaming responses from bridge tool
