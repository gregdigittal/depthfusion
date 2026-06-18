# Show HN: DepthFusion — Shared memory for AI agent teams (MIT, self-hosted)

**Title (HN):**
Show HN: DepthFusion – Shared memory for AI agent teams (MIT, self-hosted)

---

## Post body

I built this for my own Claude Code workflow and now have a handful of external users, so I'm sharing it here.

**The problem:** Every time I start a Claude Code session, my agent starts from zero. It doesn't know what I figured out last week. It doesn't know what my teammate's agent discovered yesterday. I spend the first 10 minutes rebuilding context that should already be there — explaining the auth bug that's already been found, re-deriving the architecture decisions that were already made, re-discovering the edge case that already bit us.

When I started running multiple agents across different sessions and machines, it got worse. Agent A spent 2 hours mapping the auth flow. Agent B started fresh the next day and mapped the same thing. Zero shared learning between them.

**What I built:** DepthFusion is the shared memory layer that sits underneath your agent team. Every memory publication, recall, and discovery becomes a first-class node in a knowledge graph. New sessions inherit the team's working knowledge instantly via `fabric_seed` — a cold-start that seeds the context with ranked discoveries from previous sessions, weighted by relevance, recency, and how many other agents have already encountered that knowledge.

The core idea: agents publish what they learn → knowledge graph grows → new sessions inherit it. The more your team uses it, the smarter every new session starts.

**The technical piece I'm most proud of:** The Event Graph Fabric (shipped in v1.2.0). Every publish and receive becomes a graph node with edges like `AGENT_PUBLISHED`, `AGENT_RECEIVED`, and `DERIVED_FROM`. This means you can ask provenance queries: "who knew about this memory, when, and which agent first discovered it?" — which turns out to be surprisingly useful for debugging multi-agent coordination.

The fabric_seed ranking function is: `score = recall_relevance × recency_decay × log(1 + observer_count)` — so hot knowledge (many agents touched it recently) surfaces first, and cold knowledge (found once, weeks ago, by one agent) deprioritizes itself.

**What it is technically:**
- Python MCP server — works with Claude Code, any MCP client
- Knowledge graph backed by SQLite (self-hosted, no external dependencies by default)
- Redis Streams for live SSE pub/sub across agents (optional — degrades gracefully without it)
- REST API for non-MCP clients, Tailscale-aware for multi-machine teams
- HNSW vector index for semantic recall (optional, via hnswlib)
- MIT license, runs on a $6/mo VPS

**I made an animated demo** that walks through the 3-act story — the pain (two agents re-discovering the same thing), the store (a discovery propagating through the graph), and the team (three agents sharing a growing knowledge base). It's a single self-contained HTML file in the repo: `docs/depthfusion-animated-demo.html`

Happy to answer questions about the architecture, the graph model, or the agent coordination patterns that emerged from using this on real projects.

**Links:**
- GitHub: https://github.com/gregdigittal/depthfusion
- Animated demo: https://gregdigittal.github.io/depthfusion/depthfusion-animated-demo.html
- Install in 60 seconds: `pip install depthfusion && depthfusion init`

---

## Metadata (for Claude Design context)

**Tone:** Direct, first-person engineering story. No hype. Specific claims, not vague promises.

**Audience:** HN engineering audience — skeptical, technically literate, values OSS and self-hosting. Responds to specific numbers, honest tradeoffs, and "I built this for myself" origin stories.

**Key messages to preserve:**
1. The pain is re-deriving context that already exists
2. The wow moment is instant inheritance of team discoveries
3. MIT + self-hosted = no vendor lock-in
4. The provenance graph ("who knew what, when") is the differentiating technical insight

**Avoid:**
- "AI-powered" or any AI buzzword framing
- Claiming it replaces docs or existing tools
- Overstating the user base ("a handful of external users" is honest and HN-appropriate)

**Word count target:** 400–500 words for the body (above is ~520 — trim the technical section if needed)
