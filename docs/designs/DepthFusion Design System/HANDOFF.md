# Handoff: DepthFusion Design System — "Darkroom Amber"

## Overview
This bundle is the complete **DepthFusion** design system in the **Darkroom Amber** direction — design tokens, motion system, reusable React component primitives, and a full interactive recreation of the DepthFusion desktop app (auth · dashboard · search · knowledge graph · settings). DepthFusion is an open-source, depth-aware memory/retrieval layer for Claude Code: it persists each session and hands the relevant context back on the next turn through three recall layers (BM25 · embeddings · knowledge graph). It ships as a CLI/daemon plus a **Tauri v2 + React 18** desktop app.

The brand idea is photographic: a darkroom under sodium safelight. Warm near-black "paper", a single amber accent (the safelight), ember flame, and warm-ivory type. Memory **develops** into view rather than snapping. There is no blue/indigo and no neutral-gray dark mode.

## About the Design Files
This document sits at the **root of the DepthFusion design-system project**; every file it references is a sibling in the same project (download the whole project to get them all). The source files here are **design references created in HTML/CSS/JSX** — they show the intended look, tokens, and behavior. They are **not** meant to be dropped into production as-is. Your task is to **recreate these designs in the target codebase** (the real app is **Tauri v2 + React 18**), using its established patterns, router, and build setup.

Two parts of the bundle are directly reusable with light adaptation:
- **`styles.css` + `tokens/` + `motion.css` + `components.css`** — plain CSS custom properties and classes. These can be adopted nearly verbatim (they depend on nothing). This is the fastest path to brand fidelity.
- **`components/**/*.jsx`** — small, dependency-free React function components (React only, no CSS-in-JS, no npm packages). They reference styling purely through the CSS custom properties / `.df-*` classes, so they port cleanly. Treat them as reference implementations to match against your component conventions (TypeScript, your prop patterns, your test setup).

The `ui_kits/depthfusion-desktop/` app is a **prototype** that wires the primitives into real screens with mock data — open its `index.html` (it loads the compiled `_ds_bundle.js`) to see layout, states, and the Time Machine graph interaction, then rebuild those screens in the app.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, radii, shadows, motion, and interactions are all specified here with exact token values. Recreate the UI pixel-for-pixel using these tokens and the codebase's existing libraries/patterns. Where the prototype uses inline SVG icons (Lucide-style), substitute the real **Lucide** icon set (see Assets).

---

## Design Tokens
All tokens are CSS custom properties. Source of truth: `tokens/*.css`. Link `styles.css` (it `@import`s everything) or copy the token files into your build. Wrap any branded subtree in an element with `class="df"` to inherit the base font/color/background.

### Colors — Surfaces (warm near-black "paper")
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#09070a` | App background (deepest) |
| `--surface` | `#120e0b` | Panels / cards |
| `--surface-2` | `#1a140f` | Raised panel |
| `--surface-3` | `#251c14` | Lifted / hover |
| `--border` | `#3a2a1e` | Rules, borders |
| `--border-strong` | `#4a3527` | Input borders, dividers |

### Colors — Text (warm ivory)
| Token | Hex | Use |
|---|---|---|
| `--text` | `#efe6d6` | Primary |
| `--text-2` | `#c7b89a` | Secondary |
| `--muted` | `#8a7a5f` | Tertiary / labels |
| `--faint` | `#6f6047` | Quaternary |
| `--faintest` | `#5a4e3e` | Hints / disabled |

### Colors — Accent (the safelight)
| Token | Hex | Use |
|---|---|---|
| `--accent` | `#ff8e3a` | Primary amber (sodium, ~590nm) |
| `--accent-hover` | `#ffa45c` | Hover |
| `--accent-bright` | `#ffb877` | Highlight / selected edge |
| `--accent-soft` | `#c2701f` | Secondary amber |
| `--ember` | `#c2410c` | Deep flame |
| `--on-accent` | `#1a0d04` | Text/marks on an amber fill |
| `--accent-wash` | `rgba(255,142,58,.14)` | Tint fills, highlight marks |

### Colors — Status (warm-shifted) & classification
| Token | Hex | Token | Hex |
|---|---|---|---|
| `--ok` | `#c2701f` | `--cls-public` | `#8a9a52` (olive) |
| `--warn` | `#d8934a` | `--cls-internal` | `#6f9396` (cold teal) |
| `--danger` | `#c2410c` | `--cls-confidential` | `#d8934a` (amber) |
| `--cold` | `#3e5b5e` (only for "lost/missing") | `--cls-restricted` | `#c2410c` (ember) |

Graph node types: `--node-doc #ff8e3a` · `--node-concept #c2701f` · `--node-decision #d8934a`.

### Typography
- Families: `--font-display: 'Fraunces'` (variable serif; `opsz` axis is load-bearing) · `--font-sans: 'Inter Tight'` · `--font-mono: 'JetBrains Mono'`.
- Display tuning: weight **300**, letter-spacing **−0.035em**, with **one** phrase per headline in *italic amber*.
- Scale (px): `--fs-hero 72` · `--fs-h1 30` · `--fs-stat 26` · `--fs-h2 20` · `--fs-title 15` · `--fs-body 13` · `--fs-snippet 12` · `--fs-label 11` · `--fs-micro 10`.
- Weights: 400 / 500 / 600 / 700. Mono "slate" captions: uppercase, `letter-spacing: 0.18em`.
- Fonts currently load from Google Fonts (`fonts.css`). These are the genuine families. To self-host, replace the `@import` with local `@font-face` using the **same family names**.

### Spacing, radius, shadow, motion
- Spacing (4px grid): `--sp-1`…`--sp-16` = 4, 8, 12, 16, 20, 24, 32, 40, 48, 64. Layout: `--shell-pad 44px`, `--section-pad 110px`.
- Radius (deliberately **sharp**): `--r-sm 2` (chips) · `--r-md 3` (buttons/inputs) · `--r-lg 5` (cards) · `--r-xl 6` (panels) · `--r-window 7` (app window).
- Shadows: `--shadow-card` (resting), `--shadow-card-hover` (adds amber bloom), `--shadow-pop` (windows/overlays). Accent surfaces carry `--accent-glow` (amber halo + inner top highlight).
- Motion: `--ease cubic-bezier(.4,0,.2,1)` · `--ease-develop cubic-bezier(.2,.6,.2,1)`. Durations: `--dur-quick 150ms` · `--dur-medium 400ms` · `--dur-develop 2400ms` · `--dur-breathe 6s` · `--dur-node 4s`.

---

## Components
React function components, PascalCase named exports, React-only. Each has a `.d.ts` (props contract) and `.prompt.md` (usage + variants) beside it. Styling is entirely via the CSS layer.

| Component | Dir | Props (summary) |
|---|---|---|
| **LogoMark** | `components/brand/` | `size`, `flat`, `plate`, `mark`, `animation` (`breathe`/`develop`/`pulse`/`draw`, space-separated). The memory-graph mark + the brand's animation artifact. |
| **Button** | `components/core/` | `variant: primary \| secondary \| danger \| ghost`; passes through native button props. |
| **Badge** | `components/core/` | `variant: public \| internal \| confidential \| restricted \| source`. |
| **Avatar** | `components/core/` | `name` (first letter shown), `size`. Amber fill + ember glow. |
| **Card** | `components/core/` | `title?`, children. Surface container. |
| **Input** | `components/forms/` | native input props + `icon?` (renders the leading-icon search layout). |
| **Checkbox / Radio** | `components/forms/` | `label` + native input props. Facet-panel style. |
| **Tabs** | `components/navigation/` | `tabs: (string\|{id,label})[]`, `value`, `onChange(id)`. App-shell primary nav. |
| **ResultCard** | `components/data/` | `result: {title, cls?, source?, snippet?, score?, date?, loc?}`. Wrap query terms in `{curly braces}` in `snippet` to highlight. Score bar: ember <50, amber 50–80, glow >80. |
| **NodeChip** | `components/data/` | `type: doc \| concept \| decision`. Graph node-type chip. |

---

## Screens / Views (from `ui_kits/depthfusion-desktop/index.html`)

### App shell
- **Title bar** (30px, `--surface`, bottom border): macOS traffic-light dots + centered mono app title "DepthFusion".
- **Header** (`.df-header`, 11px/18px padding, bottom border): brand (LogoMark `size=23 flat animation="breathe"` + name at `--fs-title`/600), `Tabs` nav (Dashboard/Search/Graph, each label is icon+text), right cluster (version mono label, settings icon-button, "Sign out" text button) separated by a left border.

### Auth / Sign-in
- Centered column (`.df-auth`), radial amber `--accent-wash` glow behind it. LogoMark `size=64 animation="breathe pulse"`, title in Fraunces 300 at `--fs-h1`, a ≤280px description in `--muted`, a primary Button ("Sign in" → "Opening browser…" pending → authenticated after ~850ms), version label pinned at the bottom in mono `--faintest`.

### Dashboard
- 2-column tile grid (`.df-tiles`, 16px gap, 22px padding). Tiles: **Recent Activity** (wide, `span 2`; list with label + right-aligned time, rows divided by faint borders), **Search Stats** (big Fraunces numeral + label), **Storage Usage** (numeral + progress bar `.df-progress` filled with `--accent`), **Sync Status** (status dot + last-sync + indexed count). Tile = `--surface` + `--border` + `--r-xl`, head row with bottom border.

### Search
- Search bar row: `Input` with leading search icon (max 560px) + ghost Button to toggle filters. Meta line shows result count + latency.
- Body = facet panel (210px `.df-facets`: collapsible groups of `Radio`/`Checkbox`, chevron rotates when collapsed) + results column (`ResultCard` list, live-filtered on the first query term; empty state with brain icon when no query/results).

### Graph — **Time Machine knowledge graph** (the signature interaction)
A radial dependency constellation rendered in an SVG `viewBox="0 0 600 384"`, centered at (300,188):
- **Center (focus) node**: glowing amber, `r=44`, Fraunces label, `filter: url(#tm-glow)`. **Child nodes** arranged on a ring (`radius=130`, `r=30`) around it, Inter Tight labels below each.
- **Edges attach to circle perimeters** — never cut through. Computed by trimming each segment by the source/target radius (`edgeSeg()`): an 18%-opacity 5px under-stroke + an 85%-opacity 1.6px line.
- **Dependency-count badge**: child nodes with their own deps show a small amber badge with the count at the top-right.
- **Backdrop**: twinkling starfield (90 stars, `tm-twinkle` animation) + concentric "depth tunnel" rings + a radial core glow.
- **Interactions**:
  - **Drag** on canvas → rotate the constellation (`rot` updated from pointer dx × 0.008; cursor `grab`/`grabbing`).
  - **Scroll / wheel** → zoom the active layer (`scale` 0.6–2.2; non-passive listener calling `preventDefault`). Plus a `− / % / +` zoom control in the toolbar.
  - **Click a child node** → drill in: it becomes the new center showing its dependencies; the **parent constellation recedes** into depth (rendered as up-to-two ghost rings at larger radii + lower opacity), and a **right-side depth rail** + a **breadcrumb** grow.
  - **Recede / zoom out** → the `↑ out` button, any breadcrumb segment, any depth-rail tick, or **`Esc`** pops one level (clicking the center node also recedes when not at root; cursor `zoom-out`).
- **Inspector** (248px, right): NodeChip type, label/ID/depth/provenance rows, then a **clickable dependency list** (each row drills into that dep), and an "Open Document" Button.
- State: `path` (array of node ids = drill stack), `rot`, `scale`, plus a `tick` to re-key the active layer. Geometry constants: `RING=130, RC=44, RCH=30`.

### Settings
- Centered ≤560px column of `Card`s: **Profile** (Avatar + name/email/role pill), **Server** (URL `Input` + Save with a transient "✓ Saved"), **Account** (description + destructive Button "Sign out" → returns to auth).

---

## Interactions & Behavior
- **Hover**: tabs/icon-buttons → amber text on `--border` background; result cards → border lightens + `--shadow-card`; primary buttons lift `translateY(-1px)` and brighten to `--accent-hover`.
- **Focus**: every interactive gets `:focus-visible` → 2px `--accent-soft` outline, offset 2px (accessibility fix carried from the app review).
- **Press**: no scale; color shift only.
- **Motion** (all gated behind `prefers-reduced-motion: reduce`, which renders the resolved end-state): `develop` (blur+dim+sepia → sharp, ~2.4s, once on mount), `breathe` (6s amber glow loop), `pulse` (staggered node breathing), `draw` (spokes draw in), `emerge` (blur+rise content fade-in), `df-pip` (safelight pip pulse), `df-shimmer` (skeleton).
  - ⚠️ Implementation note: drive entrance/transition animations with **JS-managed state**, not CSS `opacity:0` keyframes whose end state assumes the animation runs — a frozen first frame leaves content invisible. (This is why the prototype's graph layer has no entrance animation.)
- **Auth state machine**: `authed` (default true in the prototype for demo) · `pending` (≈850ms fake browser-auth delay) → authenticated. Sign out clears to the auth screen.

## State Management
- App shell: `authed`, `pending`, `tab` (`dashboard|search|graph|settings`).
- Search: `query`, `showFacets`; facet groups hold their own checked `Set` (radio groups clear to single-select).
- Graph: `path` (drill stack), `rot`, `scale`, `tick`. Drill pushes an id; recede slices `path`.
- Settings: `url`, `saved`.
All data in the prototype is mock (`DF_DATA`, defined inline in `index.html`). Replace with real recall/graph API calls.

## Assets
- **LogoMark**: vector, defined in `components/brand/LogoMark.jsx` (rounded amber plate + 3 nodes + hub + spokes). No external file needed.
- **Icons**: the prototype draws a small inline Lucide-style set (search, settings, graph, dashboard, doc, concept, decision, brain, close) at 24×24, ~1.7 stroke, `currentColor`. In production, install **[Lucide](https://lucide.dev)** and use those names — same stroke style. No emoji, no icon font, no raster icons.
- **Fonts**: Fraunces, Inter Tight, JetBrains Mono (Google Fonts; self-host for production).
- `uploads/depthfusion-design-handoff-2026-06-15.html` — the original shipped-app design review (indigo Tailwind theme). Structure/components/copy are authoritative; its palette was superseded by Darkroom Amber.

## Files
This spec (`HANDOFF.md`) plus the full design guide (`readme.md`) sit at the project root. Key paths:
Foundation (adopt nearly verbatim): `styles.css`, `fonts.css`, `tokens/colors.css`, `tokens/typography.css`, `tokens/spacing.css`, `tokens/effects.css`, `motion.css`, `components.css`.
Components (reference implementations): `components/<group>/<Name>.jsx` + `.d.ts` + `.prompt.md`.
Prototype app: `ui_kits/depthfusion-desktop/index.html` (+ its `README.md`) — runs against `_ds_bundle.js`.
Docs: `readme.md` (full brand/visual/content guide), `SKILL.md` (downloadable Agent Skill).
Specimen cards (visual reference for tokens): `foundations/*.html`.
Original app review: `uploads/depthfusion-design-handoff-2026-06-15.html`.
