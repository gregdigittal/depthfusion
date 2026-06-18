# DepthFusion Design System — "Darkroom Amber"

A brand + UI design system for **DepthFusion**, an open-source, depth-aware memory-fusion / retrieval layer for Claude Code. DepthFusion writes every session to disk and, on the next turn, hands the relevant parts back in — through three layers of recall (BM25 · embeddings · knowledge graph) — so context survives session-compact boundaries. It ships as a CLI/daemon plus a **Tauri desktop app** (search · knowledge graph · dashboard) across three install tiers: `local · vps-cpu · vps-gpu`.

This system encodes the **Darkroom Amber** direction: a sodium-safelight palette (warm near-black "paper", amber glow, ember flame, warm ivory type) with Fraunces display type and patient, photographic motion — *memory develops into view rather than snapping into place.*

## Sources

These informed the system. The reader may not have access, but they are recorded for those who do — exploring them will yield more faithful work:

- **GitHub:** https://github.com/gregdigittal/depthfusion  (branch `main`)
  - `docs/design/prototype/design_handoff_depthfusion_landing/README.md` + `darkroom_landing.html` — the authoritative **Darkroom Amber** spec (tokens, motion, copy). This is the brand source of truth.
  - `app/src/**` — the actual Tauri/React desktop app (pages, components, LogoMark).
  - `docs/depthfusion-marketing.html`, `docs/depthfusion-interactive.html` — additional product surfaces.
- **Uploaded handoff:** `uploads/depthfusion-design-handoff-2026-06-15.html` — a full design-review doc of the shipped app (originally an indigo Tailwind theme). Its **structure, components, states, and copy** are authoritative; its *palette* was superseded by Darkroom Amber per the chosen direction.

> Browse the GitHub repo to go deeper on real env-var names, metric events (`recall_query`), and tier semantics before producing production copy.

---

## CONTENT FUNDAMENTALS

**Voice.** Technical, confident, unhurried. The product has a photographic metaphor running through it — memory *develops*, *emerges*, *comes back*. Lean into it for marketing/empty states; stay plain and precise for UI.

**Casing.**
- **Display headlines:** sentence case, set in Fraunces, with **one** phrase in *italic amber* for emphasis — e.g. "A memory *that develops* between sessions." Never more than one emphasis per headline.
- **UI chrome / nav / labels / captions:** lowercase or small-caps **mono** ("slate") with wide tracking — `plate 01 · comparison`, `tray 01 · developer · 20°C`. These read like darkroom annotations.
- **Body copy:** sentence case, Inter Tight, calm and concrete.
- **Code-accurate tokens** (`DEPTHFUSION_MODE`, `recall_query`, `~/.claude/metrics/*-recall.jsonl`, tier names) are never paraphrased.

**Person.** Address the developer as "you" in marketing ("which tier fits your host"); describe the system in third person in UI ("Last sync: 3 min ago").

**Emoji.** **None** in the Darkroom Amber direction. The original app handoff used emoji for node types and search; this system replaces them with stroke SVG icons. (Unicode arrows `→ ↳ ▾` are used sparingly as typographic marks.)

**Vibe.** A darkroom under safelight: patient, warm, precise, a little cinematic. Numbers and metrics are "receipts," shown as JSON/mono. Avoid hype words and exclamation marks.

---

## VISUAL FOUNDATIONS

**Palette.** Warm near-black surfaces (`--bg #09070a` → `--surface #120e0b` → `--surface-2/3`) with warm-brown rules (`--border #3a2a1e`). One accent: sodium amber `--accent #ff8e3a` (hover `#ffa45c`, soft `#c2701f`, deep ember `#c2410c`). Type is warm ivory (`--text #efe6d6` down to `--faintest`). A **single cold accent** `--cold #3e5b5e` exists only for "lost / missing" states. No blue, no indigo, no neutral-gray dark mode.

**Type.** Fraunces (variable serif) for display — light weight (300), tight tracking (`-0.035em`), the `opsz` axis is load-bearing; italic-amber for emphasis. Inter Tight for body/UI. JetBrains Mono for code, metrics, and slate captions. Display numerals (stats) are set in Fraunces too.

**Spacing & radius.** 4px grid. Radii are deliberately **sharp** — 2px chips, 3px buttons/inputs, 5–6px cards, 7px the app window. Sharpness reads as darkroom precision.

**Backgrounds.** Solid warm near-black, lifted by **radial "safelight" glows** (amber, low-opacity, top-right or behind focal points) that pulse slowly. Marketing surfaces add film-grain (SVG `feTurbulence`, screen blend, ~0.55) and film-sprocket-hole motifs. No gradients-as-decoration beyond the safelight wash.

**Shadows.** Warm and deep, with a faint amber rim light: `--shadow-card` for resting cards, `--shadow-card-hover` adds an amber bloom, `--shadow-pop` for windows/overlays. Accent surfaces (primary buttons, the logo, selected cards) carry `--accent-glow` (an amber halo + inner top highlight).

**Borders & cards.** 1px warm-rule borders; cards are `--surface` fills with sharp radii, a drop shadow, and the amber rim. Selected/recommended cards gain an inset amber border + amplified glow.

**Hover / press.** Hover → amber tint or amber text; primary buttons lift `translateY(-1px)` and brighten. Focus-visible → amber outline (2px, offset 2px) on every interactive (a deliberate fix from the app review). No aggressive scale on press.

**Transparency & blur.** Backdrop-blur on sticky bars/overlays; low-alpha amber washes (`--accent-wash`) for tints and highlight marks.

**Motion.** Slow and patient — the darkroom metaphor. Standard ease `cubic-bezier(.4,0,.2,1)`; a softer settle `--ease-develop`. Signature moves: **develop** (blur+dim+sepia → sharp, ~2.4s, on mount), **breathe** (6s amber glow loop), **pulse** (staggered node breathing), **emerge** (blur+rise fade-in for content). All gated behind `prefers-reduced-motion`. See the **LogoMark** — the brand's animation artifact — and the Motion specimen card.

**Imagery.** Warm, dim, low-key; things resolve out of darkness. Prefer the inline memory-graph mark and node diagrams over photography.

---

## ICONOGRAPHY

- **Logo / mark:** the **LogoMark** — a memory-graph (three outer nodes forming a triangle + a center hub + spokes) on a rounded amber plate. It is also the system's **animated artifact**: `breathe`, `develop`, `pulse`, `draw` (see `components/brand/`). A `flat` variant (hub + nodes only) is used ≤16px where the spokes/triangle would muddy.
- **Chrome icons:** **stroke SVG, Lucide-style** — 24×24 viewBox, ~1.7 stroke, round caps/joins, `currentColor`. Set: search · settings · graph · dashboard · doc · concept · decision · brain · close. Drawn inline in the UI kit's `icons.jsx`. (If you need the full set in production, install **Lucide** from CDN and match the 1.7 stroke — a documented, like-for-like substitution.)
- **Node-type glyphs:** document / concept / decision each pair a stroke icon with a color token (`--node-doc/-concept/-decision`) via the `NodeChip` component.
- **No emoji**, no icon font, no raster icons. Unicode arrows used only as typographic marks.

---

## Fonts — substitution flag

Fraunces, Inter Tight, and JetBrains Mono load from **Google Fonts** (`fonts.css`). These are the genuine families specified by the brand, not lookalikes. To self-host (recommended for production / offline), drop the binaries into `assets/fonts/` and replace the `@import` in `fonts.css` with local `@font-face` rules using the **same family names**. If you have licensed cuts, share them and they'll be wired in.

---

## Index / manifest

**Foundations (global CSS — consumers link `styles.css`):**
- `styles.css` — entry point (only `@import`s).
- `fonts.css` — webfont import.
- `tokens/colors.css` · `tokens/typography.css` · `tokens/spacing.css` · `tokens/effects.css` — design tokens (84).
- `motion.css` — keyframes + the LogoMark animation vocabulary + entrance utilities.
- `components.css` — the component visual layer (`.df-*`).

**Components** (React, in `components/<group>/`, exposed on `window.DepthFusionDesignSystem_ab16e1`):
- `brand/` — **LogoMark** (animated).
- `core/` — **Button · Badge · Avatar · Card**.
- `forms/` — **Input · Checkbox · Radio**.
- `navigation/` — **Tabs**.
- `data/` — **ResultCard · NodeChip**.

**UI kit:** `ui_kits/depthfusion-desktop/` — interactive recreation of the desktop app (auth · dashboard · search · graph · settings).

**Specimen cards (Design System tab):** `foundations/*.html` (Colors · Type · Spacing · Motion) and one `*.card.html` per component group, plus the brand LogoMark showcase.

**Skill:** `SKILL.md` — makes this folder usable as a downloadable Agent Skill.
