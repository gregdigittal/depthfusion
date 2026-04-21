# Claude Design Prompt — DepthFusion Interactive Install UX

> **Source:** Design brief for an interactive, animated install-onboarding
> experience that visually demonstrates what DepthFusion adds to an LLM.
>
> **Audience:** A designer (human or AI) using Claude's design mode to
> produce a production-ready UX.

---

## The prompt (paste this into Claude's design mode)

> You are designing an **interactive onboarding experience** for DepthFusion —
> an open-source cross-session memory system for Claude Code. The goal is to
> replace a CLI-first installer (`python -m depthfusion.install.install`)
> with a visual landing page that both **explains what DepthFusion does**
> and **guides the user through choosing the right install mode for their
> hardware**.
>
> Treat this as a serious product landing page / onboarding wizard, not a
> marketing site. Users arrive through one of three channels:
>
> 1. A GitHub repo README linking to the experience
> 2. A shell hint printed by the CLI installer: "for a visual walkthrough,
>    visit https://depthfusion.dev/install"
> 3. A direct link from Claude Code documentation
>
> Design it to feel fast, developer-native, and technically credible — not
> sales-y.
>
> ---
>
> ### Part 1 — Core metaphor: "LLM memory before and after DepthFusion"
>
> The central visual hook is a **side-by-side animated comparison** of how
> an LLM handles context/memory without and with DepthFusion. This runs
> on scroll OR as a looped auto-animation above the fold. Show both panes
> in parallel so the delta is obvious.
>
> **Without DepthFusion (left pane):**
>
> - An LLM turn represented as a glowing orb. Each new turn, the orb
>   "forgets" the previous one — animate context dropping out of the
>   window as new tokens flow in.
> - When context runs out, show a hard wall labeled "Context Limit
>   (200k tokens)" with a compaction event erasing most of the history.
>   The conversation essentially restarts with a thin summary.
> - Overlay a counter showing "facts retained: 3 / 47" or similar,
>   demonstrating information loss.
>
> **With DepthFusion (right pane):**
>
> - Same LLM turn orb, but flanked by three small icons representing the
>   three DepthFusion layers:
>   - 📚 **BM25 retrieval** — keyword search over memory/sessions
>   - 🧠 **Embedding backend** — semantic similarity (local or GPU)
>   - 🕸️ **Knowledge graph** — entity + edge store with temporal signals
> - When a new turn arrives, animate arrows from these three layers back
>   into the context window, carrying re-discovered context blocks (show
>   them as smaller glowing fragments being "handed up" to the LLM).
> - At the compaction event, DepthFusion writes the session to persistent
>   storage (animate a "save to disk" visual with a file icon flying out
>   of the context window into a memory vault).
> - Counter shows "facts retained: 47 / 47" with a subtle animation when
>   a new fact is stored.
>
> The two panes should share the same time axis so the comparison is
> direct. Time DepthFusion's advantage to a specific moment — the
> compaction event is the most dramatic, because the without-DepthFusion
> pane visibly loses information while the with-DepthFusion pane preserves it.
>
> ---
>
> ### Part 2 — Interactive install-mode selector
>
> Below the animation, present three cards side-by-side representing the
> three install modes. Detect the user's hardware via a backend probe
> (the page calls `/api/probe-host` which returns `{hasGpu, gpuName, vramGb}`).
>
> Highlight the **recommended** card with a subtle glow + "Recommended
> for your host" badge. Let the user override by clicking another card.
>
> **The three cards:**
>
> 1. **Local** — minimalist pane
>    - Icon: a laptop / tower workstation
>    - Tagline: "BM25 keyword retrieval, zero external dependencies"
>    - Specs list:
>      - ✓ No API key required
>      - ✓ No GPU required
>      - ✓ ~50 MB install
>      - ✗ No semantic embeddings
>      - ✗ No on-box LLM inference
>    - Animated demo: tiny orb exchanging keyword-matched chunks with
>      the LLM. Fast but coarse.
>    - CLI hint: `python -m depthfusion.install.install --mode=local`
>
> 2. **VPS + CPU** (vps-cpu) — mid-tier card
>    - Icon: a cloud server (no GPU)
>    - Tagline: "Haiku reranker + optional ChromaDB vector search"
>    - Specs list:
>      - ✓ Semantic reranking via Anthropic Haiku
>      - ✓ Tier 2 vector search at 500+ sessions
>      - ✓ Requires DEPTHFUSION_API_KEY
>      - ✗ No on-box LLM
>    - Animated demo: the orb exchanges both keyword AND semantically
>      ranked chunks. Two passes of context retrieval visible.
>    - CLI hint: `python -m depthfusion.install.install --mode=vps-cpu`
>
> 3. **VPS + GPU** (vps-gpu) — premium card
>    - Icon: a server with a visible GPU chip (animated pulse effect)
>    - Tagline: "Gemma on-box + local embeddings — lowest latency,
>      cloud independence"
>    - Specs list:
>      - ✓ All LLM capabilities (reranker, extractor, linker, summariser)
>        run on-box via Gemma
>      - ✓ Local sentence-transformers embeddings
>      - ✓ Byte-identical output to cloud mode (T-121 verified)
>      - ⚠ Requires NVIDIA GPU (CUDA 12+, ≥ 12 GB VRAM recommended)
>    - Animated demo: the richest visual — three retrieval layers firing
>      in parallel (BM25 + local embeddings + graph traversal), all
>      completing before the next LLM turn.
>    - CLI hint: `python -m depthfusion.install.install --mode=vps-gpu`
>
> If the hardware probe returns `hasGpu: false`, the vps-gpu card should
> show a greyed-out ⚠ warning: "No NVIDIA GPU detected. You can still
> install this mode, but the local embedding backend will fall back to
> NullBackend. Run on a GPU host for full effect."
>
> Clicking a card should reveal an **install command block** below it
> with a copy-to-clipboard button. For vps-gpu specifically, also show
> the smoke-test command that validates the GPU path:
> `python -c "from depthfusion.install.smoke import run_vps_gpu_smoke; print(run_vps_gpu_smoke())"`.
>
> ---
>
> ### Part 3 — The "what happens next" walkthrough
>
> After mode selection, show an **animated deployment diagram** specific
> to the chosen mode. This is a 4-step horizontal flow:
>
> 1. **Install** — show the pip extras being fetched:
>    - local: nothing extra
>    - vps-cpu: `anthropic`, `chromadb`
>    - vps-gpu: `sentence-transformers`, `chromadb`
>
> 2. **Configure** — show `~/.claude/depthfusion.env` being written with
>    the mode's env vars. Animate the file appearing with its contents
>    visible (scrolling past).
>
> 3. **Hook integration** — show Claude Code's `settings.json` getting a
>    `PreCompact` + `PostCompact` hook entry. Optionally animate a
>    git post-commit hook install for the user's project repo.
>
> 4. **First recall** — show a simulated recall query succeeding. The
>    metrics stream populating (`YYYY-MM-DD-recall.jsonl` filling with
>    its first entry). Perhaps a live "operators can now query
>    `backend_summary()`" moment.
>
> ---
>
> ### Part 4 — Tier-specific value callouts
>
> A "why this tier" section with short technical callouts specific to
> the selected mode. These should be interactive — hover reveals deeper
> detail.
>
> **For local:**
>
> - "Works offline" — reveal: "No API calls ever. BM25 scoring happens
>   entirely in your Python process. Discoveries written to
>   `~/.claude/shared/discoveries/` never leave your machine."
> - "Zero-config upgrade path" — reveal: "Set `DEPTHFUSION_API_KEY` and
>   re-run the installer; your existing discoveries migrate automatically
>   to vps-cpu. Upgrade to vps-gpu by adding a GPU and re-running."
>
> **For vps-cpu:**
>
> - "Billing-safe by design" — reveal: "DepthFusion reads
>   `DEPTHFUSION_API_KEY` explicitly; it NEVER reads `ANTHROPIC_API_KEY`.
>   Your Claude Code Pro/Max subscription stays untouched even when
>   you enable Haiku features."
> - "Tier-2 auto-promotion" — reveal: "At 500 sessions, DepthFusion
>   automatically promotes your corpus to ChromaDB vector storage.
>   The transition is idempotent and reversible."
>
> **For vps-gpu:**
>
> - "True offline LLM" — reveal: "Gemma runs via vLLM on your GPU.
>   Reranking, extraction, summarisation, linking — all happen without
>   an external API call. Your conversation data never leaves your box."
> - "Mamba B/C/Δ selective fusion gates" — reveal: "Query-similarity
>   gate → topical-coherence gate → α-blended-threshold filter.
>   Outperforms flat BM25+reranker on Category A recall benchmarks."
>   *(Link to S-51 build plan for the curious.)*
>
> ---
>
> ### Part 5 — Observability peek
>
> A small section near the bottom demonstrating the **metrics stream
> DepthFusion produces**. Show a terminal-style widget with a live-
> animated `-recall.jsonl` feed — each new entry flying in from the
> right, with syntax-highlighted fields:
>
> ```json
> {
>   "timestamp": "2026-04-21T17:42:03+00:00",
>   "event": "recall_query",
>   "event_subtype": "ok",
>   "query_hash": "abc123def456",
>   "mode": "vps-gpu",
>   "backend_used": {
>     "reranker": "gemma",
>     "embedding": "local_embedding"
>   },
>   "latency_ms_per_capability": {
>     "vector_search": 45.2,
>     "fusion_gates": 12.8,
>     "reranker": 127.3
>   },
>   "total_latency_ms": 201.7,
>   "result_count": 5
> }
> ```
>
> Caption: "DepthFusion records structured metrics for every query,
> ready for aggregation via `backend_summary()`."
>
> ---
>
> ### Technical constraints & design principles
>
> 1. **Accessibility first.** All animations must respect
>    `prefers-reduced-motion: reduce` — fall back to static illustrations
>    with clear before/after states.
>
> 2. **Dark + light themes.** Developer tooling; both must look
>    first-class. Default to dark.
>
> 3. **No heavy frameworks.** Keep it fast — target < 100 KB JS initial
>    load. Animations should use CSS transforms + requestAnimationFrame,
>    not a library like Framer Motion.
>
> 4. **Typography.** Monospace for all CLI / code / technical labels
>    (suggest `JetBrains Mono` or `IBM Plex Mono`). Sans-serif for body
>    copy (suggest `Inter` or `IBM Plex Sans`).
>
> 5. **Color palette.** Use a terminal-adjacent palette:
>    - Primary: a signature blue-green (suggest `#00D9B2` or `#34D399`)
>      for the "DepthFusion active" state
>    - Warning: amber for fallback paths (vps-gpu without GPU)
>    - Error: muted red, never bright — developers are suspicious of
>      red-as-marketing
>    - Background: near-black `#0B0F17` in dark, `#F8FAFC` in light
>
> 6. **Animation pacing.** The before/after comparison animation should
>    complete one full loop in ~20 seconds. Not so fast it's dizzying,
>    not so slow it's boring. The compaction event — the dramatic
>    moment — should land at ~12 seconds into the loop.
>
> 7. **Real content, real commands.** Every code block, CLI snippet,
>    and JSON example must be ACCURATE — things a user can actually
>    run. No lorem ipsum. The JSON example in Part 5 should match the
>    real output of `MetricsCollector.record_recall_query()`.
>
> 8. **One primary CTA.** After the user selects a mode, the primary
>    action is copy-to-clipboard on the install command. Do not compete
>    with that CTA. Avoid "schedule a demo" or similar sales moves.
>
> 9. **Progressive disclosure.** The landing page works without any
>    clicks — hero animation plays, three cards visible, core concept
>    understood. Deeper technical detail only appears on hover / click.
>
> 10. **Escape hatch.** A small link at the top-right says
>     "I prefer the CLI" → scrolls directly to the install command block
>     without animations. Respect developers who don't want hand-holding.
>
> ---
>
> ### Deliverables requested
>
> Produce:
>
> 1. **A single interactive HTML prototype** (one file, no build step)
>    that renders the full experience with all animations and mode-card
>    interactions working.
> 2. **Three wireframe illustrations** showing the hero animation at
>    its three key frames: (a) normal conversation, (b) compaction event,
>    (c) post-compaction recovery — split-screen across both panes.
> 3. **A short Figma-style component library** annotation describing
>    the reusable pieces (mode card, code block with copy button,
>    animated orb, metrics-stream terminal widget).
> 4. **Mobile layout notes** — how the side-by-side comparison collapses
>    to vertical on < 768px viewports, and how the three mode cards
>    stack.
>
> Tone: the finished experience should make a senior Python developer
> say "huh, this is actually interesting" within 10 seconds of landing.
> Not "this is cool marketing." *Interesting.*

---

## Usage notes for the designer

- The backend probe endpoint `/api/probe-host` doesn't exist yet — it
  would be a thin wrapper around `depthfusion.install.gpu_probe.detect_gpu()`
  returning the JSON shape `{hasGpu: bool, gpuName: str, vramGb: float}`.
  Design the page so it works WITHOUT the probe (user picks manually);
  the probe is an enhancement.
- All CLI commands cited in the design (install, smoke test, env vars)
  exist in `v0.5.1+`. The env var list is authoritative in
  `CHANGELOG.md` §[v0.5.1] §"New environment variables".
- The metrics schema shown in Part 5 is the real shape emitted by
  `MetricsCollector.record_recall_query()` as of v0.5.2 (post-S-62).
  Don't invent fields; if unsure what's real, reference
  `src/depthfusion/metrics/collector.py`.
- The build plan for S-51 (Mamba B/C/Δ gates) lives at
  `docs/plans/v0.5/02-build-plan.md §TG-11` — link target for the
  "selective fusion gates" callout in Part 4.

The CLI installer already shipped a text-mode version of this
experience (S-62, v0.5.2). The web UX complements it; both exist.
Users who want a CLI install get an interactive banner; users who
want to understand WHY they'd install DepthFusion at all get this
web experience.
