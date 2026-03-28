# DepthFusion — Power User Research Report
# Date: 2026-03-28 | Source: /goal autonomous optimization run

---

## 1. Claude Code Power User Memory Patterns

### The Standard Multi-Layer Stack
Most power users assemble a 3–4 layer system:

1. **CLAUDE.md** — project-local rules, conventions, architecture decisions (in-context, always loaded)
2. **MEMORY.md index** + memory/*.md files — user-written cross-session facts (small, selective)
3. **SessionStart hooks** — enforce context recovery on every session start
4. **PreCompact / PostCompact hooks** — snapshot active plan before compaction, restore on resume

Source: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks), [DEV Community Ultimate Guide](https://dev.to/holasoymalva/the-ultimate-claude-code-guide-every-hidden-trick-hack-and-power-feature-you-need-to-know-2l45)

### SessionStart Hook Pattern
- Only `type: "command"` hooks supported for SessionStart
- Typical payloads: git status, recent TODOs, sprint priorities, recovery state after compaction
- Keep hooks fast — they run before every session start
- PreCompact snapshots plan path + current task; SessionStart(compact|resume) reloads from checkpoint

Source: [Claude Code power user customization](https://claude.com/blog/how-to-configure-hooks)

### Key Insight: Active vs Passive Context
Power users distinguish two strategies:
- **Passive:** Load relevant context automatically at session start (hook-injected)
- **Active:** User explicitly invokes recall when needed (`/recall`, MCP tool call)

The most effective pattern combines both: passive hook for always-relevant context (preferences,
project boundaries), active tool for session-specific or topic-specific retrieval.

### What They DON'T Do
- Most do NOT use MCP servers for memory — the extra infrastructure is seen as overhead for the gain
- Most prefer structured CLAUDE.md sections and explicit `/learn` captures over automated extraction
- The dominant pattern is **better writing discipline** (structured discovery files, mandatory `/learn`)
  rather than **better retrieval** (MCP servers, vector DBs)

---

## 2. Competing Memory MCP Servers

| Server | Approach | Storage | Status |
|--------|----------|---------|--------|
| [mem0ai/mem0-mcp](https://github.com/mem0ai/mem0-mcp) | Semantic search + filtering | Cloud/local | Production, widely adopted |
| [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | Session capture + AI compression | ChromaDB | Maintained |
| Memory Anchor (awesome-claude-code) | 5-layer cognitive model + hybrid search | Local | Community |
| [yuvalsuede/memory-mcp](https://github.com/yuvalsuede/memory-mcp) | Persistent memory + git snapshots | REST/local | Active |
| [doobidoo/mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) | Knowledge graph + consolidation | REST | Active |

**Key competitive insight:** Most production alternatives use vector databases (ChromaDB, Qdrant)
for semantic retrieval. DepthFusion uses BM25 (keyword-based), which:
- ✓ Requires no external services, no API keys, runs offline
- ✓ Appropriate for small corpora (<100 files) where BM25 ≈ vector search in precision
- ✗ Cannot match on semantic similarity (synonyms, paraphrases)
- ✗ Requires exact term overlap — "embedding retrieval" won't match "vector search"

**Positioning:** DepthFusion is competitive with mem0/claude-mem for keyword-heavy technical
content (code, errors, decisions). For semantic recall ("remember that thing about neural nets"),
vector-based alternatives win. For <50 files and technical queries, BM25 is sufficient and
significantly lighter weight.

---

## 3. BM25 vs TF-IDF vs Keyword Overlap for Small Corpora

**Winner: BM25, clear margin**

| Method | Precision delta (vs keyword overlap) | Notes |
|--------|--------------------------------------|-------|
| Keyword overlap | baseline | Linear, no length norm, no saturation |
| TF-IDF | +3–5% | Length norm but no term saturation |
| BM25 | +5–15% | Both saturation and length norm; dominates |

**For small corpora specifically (<100 docs):**
- BM25 advantages are *more pronounced* — term distribution is uneven; length normalization matters more
- The dominant problem with keyword overlap on small corpora: one large file (e.g., review-gate-patterns.md
  at 19KB) scores well on nearly every query due to vocabulary breadth → BM25 corrects this
- BM25 defaults (k1=1.5, b=0.75) are well-validated and work well without tuning

Sources: [TF-IDF and BM25 for RAG](https://www.ai-bites.net/tf-idf-and-bm25-for-rag-a-complete-guide/),
[BM25 vs TF-IDF](https://olafuraron.is/blog/bm25vstfidf/)

---

## 4. Block Chunking (Header-Based) for Technical Docs

**Header-aware chunking (splitting on ## H2) outperforms naive whole-file by 5–10%**

Best practices (from Weaviate RAG guide, NVIDIA blog):
- Split on H2 (##) headers — each represents a complete semantic unit
- Include header hierarchy as metadata (acts as "semantic enrichment")
- For technical docs: headers encode structure that matters (methods, definitions, error patterns)
- Fixed-size splitting that cuts mid-section degrades precision measurably

**Implementation already complete in depthfusion v0.2.0.** The `_split_into_blocks()` function
splits on `\n## ` headers, preserving each section as an independently-scored chunk.

Sources: [Chunking Strategies for RAG — Weaviate](https://weaviate.io/blog/chunking-strategies-for-rag),
[Optimizing RAG Context](https://dev.to/oleh-halytskyi/optimizing-rag-context-chunking-and-summarization-for-technical-docs-3pel)

---

## 5. Implications for DepthFusion

### What the research validates
- ✓ BM25 over keyword overlap (implemented)
- ✓ Header-aware chunking (implemented)
- ✓ SessionStart hook for passive context injection (improved with git log + BACKLOG.md)
- ✓ On-demand via MCP tool for active recall (already the design)

### What the research reveals that we should consider next
1. **The write-back gap is the #1 limitation** — most power users succeed via better writing discipline,
   not better retrieval. A PostToolUse hook that prompts `/learn` extraction after each `/goal` run
   would address Category D more than any scoring improvement.
2. **Hybrid BM25 + embeddings** — for semantic queries, adding TF-IDF cosine as a secondary
   signal (scikit-learn TfidfVectorizer, already a dev dependency candidate) would catch
   synonym-based queries BM25 misses.
3. **Mem0 compatibility** — if the user has Mem0 installed, DepthFusion could delegate to it
   for semantic queries and handle its own keyword/structural queries. Not needed now.
