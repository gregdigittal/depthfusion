# Handoff: DepthFusion Landing — Darkroom Amber

## Overview

A landing page for **DepthFusion** — an open-source retrieval layer for Claude Code that keeps context across session-compact boundaries. The page walks a developer from "what this does" → "which install tier fits my host" → "what the installer does step-by-step" → "why this tier is the right one" → "the receipts" (observable metrics).

The design direction is **Darkroom Amber**: sodium-safelight palette (warm near-black paper, amber glow, ember accents, warm ivory type). The visual metaphor is a photographic darkroom — memory "develops" into view rather than snapping into place. Motion is intentionally slow and patient (2–3s eases, not 200ms). No blue, no indigo, no generic-dev-tool dark mode.

## About the Design Files

The files in this bundle are **design references created in HTML** — a working prototype showing intended look and behavior, not production code to copy directly. The task is to **recreate these HTML designs in the target codebase's existing environment** (React, Vue, SvelteKit, Astro, etc.) using its established patterns and libraries. If no frontend environment exists yet, choose the most appropriate framework for the project (recommend Astro or Next.js for a marketing site) and implement there.

`darkroom_landing.html` is self-contained — it inlines the full stylesheet and all JS (comparison animation, probe simulator, callouts, metrics stream). Treat every CSS custom property, keyframe, SVG diagram, and copy line as authoritative.

## Fidelity

**High-fidelity (hifi).** Colors, typography, spacing, motion timings, SVG diagrams, and copy are all final. The developer should recreate the UI pixel-perfectly. Copy and tier names are aligned to the actual DepthFusion codebase (local · vps-cpu · vps-gpu · `DEPTHFUSION_*` env vars · `recall_query` metric events) — do not rename.

## Screens / Views

There's one long scrolling page, composed of six sections. Widths are fluid with a `max-width: 1220px` shell and `44px` side padding. All sections share a top-and-bottom film-sprocket-hole motif via `.section::before/::after` pseudo-elements.

### 1. Top Navigation (`nav.nav`)
- **Layout**: 4-column grid — brand · nav links · GitHub button · "i prefer the cli" escape link. Full width within shell. Height ~62px. Bottom border `1px solid var(--warm-rule)`.
- **Brand**: 28×28px radial-gradient orb (`radial-gradient(circle at 35% 30%, #ff8e3a, #c2410c 70%, #2a1006 100%)`) with a 6s `breathe` animation on its box-shadow glow. Wordmark: `Fraunces` 20px 300-weight, "Fusion" in italic amber. Version tag `v0.5.2` in mono 10px.
- **Nav links** (mono 11px, `letter-spacing: .06em`, color `--ivory-3`, hover amber): `develop` / `install` / `receipts`.
- **GitHub button**: bordered pill, mono 11px, hover fills amber.
- **Escape link** (`.nav-escape`): "i prefer the cli" in amber with a `→` arrow that nudges 4px on hover.

### 2. Hero (`section.hero`)
- **Layout**: 2-column grid, 1.35fr / 1fr, 60px gap, center-aligned. 90px top / 80px bottom padding.
- **Safelight glow**: `.hero::before` is an 800×800 radial-gradient amber circle positioned top-right (`-200px, -200px`) with an 8s `pulse` animation (opacity 1 ↔ .7).
- **Slate top**: mono 10px uppercase tracking .24em — pulsing amber pip · "Plate 00" · "Cross-session memory" · "Safelight · 590nm".
- **Headline**: `Fraunces` 200-weight, clamp(56px, 7vw, 96px), line-height .94, tracking -.035em. Three `.emerge` spans animate in sequence (0s / .6s / 1.2s delay), 2.4s `cubic-bezier(.4,0,.2,1)` ease — they fade from blur(6px) + dim color to sharp + ivory, as if developing on film. The middle span "that develops" is italic amber. Copy:
  > A memory *that develops* between sessions.
- **Lede**: Inter Tight 18px 300, max-width 52ch, `--ivory-2`. Delayed emerge at 1.8s. Copy:
  > DepthFusion is an open-source retrieval layer for Claude Code. It writes every session to disk and, on the next turn, hands the relevant parts back in — slowly, patiently, through three layers of recall. Context no longer disappears when the window compacts. It keeps developing.
- **CTAs**: primary button "Begin the installer →" (amber bg `#ff8e3a`, near-black text, `border-radius: 2px`, mono 12px tracking .1em, large amber glow box-shadow, translateY(-1px) on hover) and a secondary text link "or read the sequence" (mono 12px, border-bottom).
- **Developing-print tray (right column)**:
  - `.tray`: 420×420 max, `aspect-ratio: 1/1`, `border-radius: 8px`, radial-gradient paper with inset shadows and outer amber glow.
  - `.tray::after`: the liquid surface — ripples via 9s scale animation (1 ↔ 1.03).
  - `.print`: an SVG memory-graph at 18% inset, animated with a 4s `develop` keyframe (`filter: blur(8px) brightness(.4) sepia(.8)` → sharp) starting at 1.2s. Five nodes pulse staggered (`nodeBreath` 4s, r: 3→5, opacity .5→1, `filter: url(#glow)`).
  - `.tray-label`: mono 10px tracking .18em, color `--ivory-4`. Copy: `tray 01 · developer · 20°C · 47 fragments emerging`.
- **Meta strip**: 4-column grid below hero, 80px top margin, 28px padding-top, top border `1px var(--warm-rule)`. Each cell is k/v/sub — mono label 10px .2em, serif 22px value with italic amber spans, mono 11px sub. Cells: release · install · runtime · footprint. Delayed emerge at 2.6s.

### 3. Plates — Comparison Animation (`#how`)
- **Section header** pattern used everywhere: 170px/1fr grid. Left: mono slate (`Plate 01 · Comparison •••`) with top border. Right: `Fraunces` 200-weight 56px headline with amber italic span ("it comes back.") and 15px 300 sub-copy max-width 58ch.
- **Plates grid**: two cards (`.plate.lost` and `.plate.kept`), 32px gap. Each plate is 28px padded paper-colored card with large drop-shadow. `.plate.kept` has an additional amber glow shadow.
- **Plate head**: flex-justify between a title block (stamp caption + serif 24px title with italic amber "develops") and a fact counter (serif 28px "47" over mono "facts kept · of 47"). `.plate.lost` counter turns cold teal (`--cold`); `.plate.kept` counter stays amber.
- **Stage**: 340px tall SVG canvas (`viewBox="0 0 500 340"`) with annotations absolutely positioned on top.
  - **Left stage (lost)**: context-window dotted rectangle (`stroke-dasharray: 4 4`, `--warm-rule`), with a `lostGlow` radial that fades in post-compact. 10 `orbsL` DOM elements (6px ivory dots) drift toward the right edge; at compact (60% through loop) all but the first two fade away with increasing blur.
  - **Right stage (kept)**: central 16px amber `#llmR` circle with `llmGlow` 70r radial behind it. Three labeled layer boxes on right (BM25 / EMBEDDINGS / KNOWLEDGE GRAPH), each pulsing on stagger (ix × 0.18s offset, fill `#2a1a10` + stroke amber when firing). Dashed arrows from each layer to the LLM pulse opacity 0.3→0.9. 5 `.frags` (4px dots) ride along the arrows on each turn. Orbs orbit the LLM on an `ang = i/TURN * 2π + t * 1.4` trajectory; at compact they drift into a vault rectangle at the bottom (`#vaultFillR` grows amber). Save arrow fades in post-compact.
- **Annotations** (`.annot`): Fraunces italic 13px amber-soft (`--amber-soft`), with `↳ ` prefix. Cold variant for the "post-compact" annotation on the left plate.
- **Timeline**: full-width strip across both plates — play/pause button (bordered, amber on hover), progress track with amber fill + ember compact marker at 60%, mono "00.0 / 20.0 s" readout, reset button. Loop length 20s; compaction at 12s.

### 4. Install Cards (`#install`)
- **Probe banner**: full-width 18×24px padded mono strip with an animated amber orb, live detection text ("detected NVIDIA A10G · 24 GB VRAM · CUDA 12.4 — recommending vps-gpu"), and a 3-way toggle on the right (no-gpu / cpu vps / gpu · 24gb). The strip has a gradient left border (amber→transparent) via the dual `background-image` / `background-origin` technique.
- **Cards grid**: 3 equal columns, 28px gap, min-height 620px. Each card is `--paper` bg, 32×30 padded, `border-radius: 6px`, with a recommended badge absolutely positioned top-right ("Recommended · fits your host"). Selected card gets an amber inset border (`inset 0 0 0 1px rgba(255,142,58,.3)`) and amplified shadow.
- **Card structure**:
  - Slate: "tier 0/1/2" · "local/vps-cpu/vps-gpu" (mono 10px tracking .22em, amber-soft tier)
  - Name: `Fraunces` 200 42px with italic amber (e.g., `*vps-gpu*`)
  - Tag: 15px 300 ivory-2
  - Spec list: mono 13px. Each `li` is 18px/1fr grid — amber ✓ marker or ivory · marker for "no" rows or `#d8934a` ⚠ for warnings. `code` gets paper-2 bg, amber-soft color.
  - Demo visual: 88px tall panel with a horizontal track, a 14px amber→ember gradient "LLM" orb on the right, and 1-4 drifting token dots (`@keyframes drift`, 3.5s linear, staggered delays 0/.9/1.8/2.6s).
  - CLI block: `--night` bg, warm-rule border, mono 12px amber with `$` prompt in ivory-4. Copy button top-right (bordered, turns amber on copy).
  - Tier 2 has an extra smoke-test CLI block and a `.warn-note` that appears when user toggles to "no gpu" probe.

### 5. Filmstrip Walkthrough
- **Container** (`.filmstrip`): paper bg with gradient fade on left/right edges, 20×40 padding. Top and bottom have sprocket-hole pseudo-elements (`radial-gradient(ellipse at center, var(--night) 6px 5px, transparent 7px)` tiled at 54×14).
- **Steps**: 4-column grid with 1px warm-rule dividers. Each step is 32×26 padded paper.
- **Step number**: `Fraunces` 200 64px amber, with mono 10px `/04` superscript in ivory-4.
- **Step title**: `Fraunces` 300 20px.
- **Step body**: 14px 300 ivory-2, with inline `code` in paper-2 bg.
- **Step asset**: mono 11px code block (`--night` bg, warm-rule border), with syntax highlight classes `.k` (amber keys) / `.s` (light-amber strings) / `.v` (ivory values) / `.c` (ivory-4 comments). Contents swap per selected tier (see Interactions).

### 6. Callouts (`.callouts`)
- **Tier picker**: 3-button pill group (`#coPick`) — inactive mono ivory-3, active amber bg.
- **Grid**: 2 columns, 24px gap. Each callout is paper, 30×32 padded, 220px min-height.
- **Content**:
  - Stamp (top-right): mono 10px ivory-4 — "№ 01 · hover to develop"
  - Headline: `Fraunces` 200 26px with italic amber emphasis
  - Hint: mono 10px ivory-4 "↓ expand"
  - `.reveal`: max-height 0 → 260px on hover, 0.8s cubic-bezier ease. 14px 300 ivory-2 body with amber-soft inline code.

### 7. Contact Sheet — Metrics (`#metrics`)
- **Layout**: 1.5fr/1fr 2-column grid inside a `.contact` card.
- **Left (sheet)**: near-black bg. Head strip with file path `~/.claude/metrics/2026-04-21-recall.jsonl` + pulsing "streaming" indicator. Body is 420px tall, 18×22 padded, mono 11.5px. Entries are paper-colored JSON blocks with a 2px amber left border and a soft left-glow box-shadow. New entries prepend with an `appear` animation (slide-in 18px + blur-3px fade, 1s cubic-bezier). Keep only 3 visible. Syntax classes `.k` / `.s` / `.n` (tabular-nums number) / `.p` (punctuation).
- **Right (receipts)**: 32×36 padded column. Lede in `Fraunces` 200 22px with italic amber. Sub-copy 13px 300 with amber-soft inline code. Three stat blocks, each separated by a warm-rule top border:
  - **p50 · vps-gpu** → `Fraunces` 200 44px amber "201.7" + 18px ivory-3 "ms"
  - **queries · session** → numeric counter, increments on each metric push
  - **backends in use** → mono 16px "gemma · local_embedding"
  Stream pushes a new JSON line every 3.2s (skipped if `prefers-reduced-motion`).

### 8. Footer
- Signoff left (`Fraunces` italic 13px): "DepthFusion · open source · apache-2.0 · printed in the dark."
- Link list right: docs · changelog · github · discord (mono 10px uppercase, amber hover).

## Interactions & Behavior

### Global
- `prefers-reduced-motion: reduce` disables ALL animations/transitions via a single CSS rule. Honor this.
- Film-grain overlay is a fixed-position SVG noise `<filter>` in a data-URL, `mix-blend-mode: screen`, 0.55 opacity, `z-index: 1000` with `pointer-events: none`.

### Comparison loop (Part 3)
- 20-second loop. Compact boundary at 60%. Play/pause toggles and swaps button glyph (`⏸ pause` ↔ `▶ play`). Track click seeks. Reset resets `t = 0`.
- Physics is all manual `left`/`top`/`opacity` writes on DOM orbs + SVG attribute writes on layer rects. No transitions on the orbs — motion is per-frame.

### Probe simulator (Part 4)
- Clicking a `[data-probe]` button updates the probe banner text and orb style (default amber / `.warn` dim-amber / `.dim` ivory-4). It also updates which card has the `.recommended` badge, and if the user hasn't explicitly selected a card (`window._userSel === false`), auto-selects the recommended card.
- GPU card shows `.warn-note` only when probe is `nogpu`.

### Card selection
- Clicking a `.card` (but not the copy button) sets `_userSel = true`, marks that card `.selected`, swaps the walkthrough label + step 1/2 asset contents per `modeData`, re-renders callouts for that tier, and toggles the `.callout-tier` button.
- `modeData` holds the per-tier copy for extras-to-install, env-file contents, and label string.

### Copy buttons
- Use `navigator.clipboard.writeText`. On success: text → "✓ copied", add `.copied` class (amber bg, near-black text) for 1.4s.

### Callouts
- CSS-driven hover reveal (max-height + opacity). No JS needed except when the tier changes — at which point `renderCallouts(tier)` rebuilds the grid from `calloutsData`.

### Metrics stream
- First entry pushes immediately. Subsequent entries every 3.2s via `setInterval`. Each pushes to the top; body prunes to 3 children.
- Values are randomized within realistic bands: vector 30-70ms, gates 8-18ms, reranker 100-160ms. Hashes cycle through a fixed 5-item list.
- Counter (`#queryCt`) = total pushes since page load.

## State Management

Minimal — each section owns its state in plain closure-scoped variables:

- **Comparison loop**: `anim = { t, playing, last }`. RAF-driven.
- **Probe/card**: `_userSel: boolean`, implicit in DOM classes. `setProbe(which)` and `selectCard(mode)` are the only two mutators.
- **Callouts**: rendered from static `calloutsData[tier]`. Re-render on tier change.
- **Metrics**: `mCount`, `hashes[]`. `pushMetric()` mutates DOM.

For a React port: lift `selectedMode`, `probeState`, and `isPlaying` to a top-level context. Everything else can stay component-local. The comparison animation is imperative enough that I'd keep it in a `useRef` + `useEffect(RAF)` rather than fighting React's reconciler.

## Design Tokens

### Colors

```
--night:      #09070a   /* deepest bg */
--paper:      #120e0b   /* panel bg */
--paper-2:    #1a140f   /* raised panel */
--paper-3:    #251c14   /* lifted */
--warm-rule:  #3a2a1e   /* borders, rules */
--amber:      #ff8e3a   /* primary accent — sodium safelight */
--amber-soft: #c2701f   /* secondary */
--ember:      #c2410c   /* deep flame */
--ember-glow: rgba(255,142,58,.22)
--cold:       #3e5b5e   /* single cold accent for 'missing' states */
--ivory:      #efe6d6   /* warm white, primary text */
--ivory-2:    #c7b89a   /* secondary text */
--ivory-3:    #8a7a5f   /* tertiary */
--ivory-4:    #5a4e3e   /* quaternary / hints */
```

### Typography
- **Serif** (`--serif`): `Fraunces` — variable font, use axes `opsz` 48 (body) / 72 (mid) / 96 (large) / 144 (hero). Weights 200–400.
- **Sans** (`--sans`): `Inter Tight` 300–600.
- **Mono** (`--mono`): `JetBrains Mono` 300–500.

Google Fonts import:
```
https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,200;9..144,300;9..144,400;9..144,500&family=JetBrains+Mono:wght@300;400;500&family=Inter+Tight:wght@300;400;500;600&display=swap
```

### Spacing
- Shell padding: 44px inline
- Section padding: 110px block
- Grid gaps: 24–32px primary, 40–60px between major regions
- Card padding: 28–32px
- Card min-height (install tier): 620px

### Border radius
- Buttons, cli blocks, small chips: 2–3px (deliberately sharp)
- Cards, panels: 4–6px
- Tray / large containers: 6–8px

### Shadows
- Card base: `0 1px 0 rgba(255,142,58,.04), 0 4px 30px rgba(0,0,0,.3)`
- Card hover: `0 1px 0 rgba(255,142,58,.1), 0 20px 50px rgba(0,0,0,.5), 0 0 50px rgba(255,142,58,.08)`
- Card selected: `0 1px 0 rgba(255,142,58,.25), 0 20px 60px rgba(0,0,0,.5), 0 0 70px rgba(255,142,58,.22), inset 0 0 0 1px rgba(255,142,58,.3)`
- Primary button: `0 0 30px rgba(255,142,58,.4), inset 0 1px 0 rgba(255,255,255,.2)`
- Metric entry: `-12px 0 24px -12px rgba(255,142,58,.1)` (left-side amber glow)

### Motion
- Standard ease: `cubic-bezier(.4, 0, .2, 1)`
- Hero "emerge": 2.4s (intentionally slow — the darkroom metaphor)
- Card hover: 0.4s
- Callout reveal: 0.8s
- Orb breathe: 2.4s ease-in-out infinite
- Ambient pulses: 4–9s

## Assets

- All imagery is SVG, drawn inline. No external images.
- **Logo mark**: a 28×28 radial-gradient circle with breathing glow animation. The "print" in the hero tray is an inline `<svg>` memory-graph with 7 nodes and 7 connective strokes.
- **Film grain**: a data-URI SVG `<feTurbulence>` filter, applied as a body pseudo-element. Replace with a static PNG if grain performance is a concern in production.
- **Sprocket holes**: CSS `radial-gradient` backgrounds, tiled. No raster assets needed.

## Copy / Content

All copy is final and codebase-accurate. Do not paraphrase the tier descriptions, env-var names, env-file fragments, or the `backend_summary()` references — they mirror real DepthFusion semantics (`DEPTHFUSION_MODE`, `DEPTHFUSION_EMBEDDING_BACKEND`, `DEPTHFUSION_RERANKER_BACKEND`, `DEPTHFUSION_GEMMA_URL`, `DEPTHFUSION_API_KEY`, `recall_query` events, `~/.claude/metrics/*-recall.jsonl`, `~/.claude/shared/discoveries/`).

## Files

- `darkroom_landing.html` — full self-contained landing page. Open directly in a browser; no build step required.

## Implementation Notes for the Developer

1. **Frameworks**: If this is going into an existing React/Next app, split as: `<Nav/>`, `<Hero/>` (with `<DevelopingTray/>` child), `<Comparison/>` (heavy; keep imperative), `<InstallTiers/>` (stateful), `<Filmstrip/>` (pure render of per-tier data), `<Callouts/>` (pure render), `<MetricsStream/>` (setInterval hook). Hoist `{mode, probe}` to context.
2. **Fonts**: Fraunces is the irreplaceable part of the identity. The `opsz` axis values in the CSS are load-bearing — don't drop to a static cut of Fraunces.
3. **Variable font fallback**: specify `'PP Editorial New'` or system serif fallback, but verify Fraunces loads before first paint.
4. **Animation accessibility**: the `prefers-reduced-motion` rule at the bottom of the stylesheet is intentional. Keep it.
5. **SEO / metadata**: the prototype doesn't include meta tags beyond `<title>`. Add OG image, description, canonical URL in the real site.
6. **Perf**: The film-grain filter and the 20s RAF loop are the two biggest per-frame costs. On low-power devices consider gating the RAF loop on an `IntersectionObserver` so it stops running when the Comparison section is off-screen.
