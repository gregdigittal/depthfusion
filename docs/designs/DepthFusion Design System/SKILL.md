---
name: depthfusion-design
description: Use this skill to generate well-branded interfaces and assets for DepthFusion (the "Darkroom Amber" direction), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.
If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.
If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Where things are
- `readme.md` — the full design guide: content fundamentals, visual foundations, iconography, and a file index. Read it first.
- `styles.css` — the one stylesheet to link; it `@import`s `fonts.css`, the `tokens/*` files, `motion.css`, and `components.css`. Linking it gives you every token, the webfonts, the motion keyframes, and the `.df-*` component classes.
- `tokens/` — color, typography, spacing, and effects custom properties (`:root`).
- `motion.css` — keyframes + the LogoMark animation classes (`.df-logo--breathe|develop|pulse|draw`) and `.df-emerge` utilities.
- `components/<group>/` — React primitives (`LogoMark`, `Button`, `Badge`, `Avatar`, `Card`, `Input`, `Checkbox`, `Radio`, `Tabs`, `ResultCard`, `NodeChip`). Each has a `.d.ts` contract and a `.prompt.md` with usage.
- `ui_kits/depthfusion-desktop/` — a full interactive recreation of the desktop app to copy patterns from.
- `foundations/*.html` — specimen cards for colors, type, spacing, and motion.

## Working in plain HTML (mocks, slides, prototypes)
1. Link `styles.css` and wrap your content in an element with `class="df"`.
2. Use the CSS custom properties (`var(--accent)`, `var(--surface)`, `var(--font-display)`, …) — never hardcode hex.
3. Use the `.df-*` classes for chrome (`.df-btn`, `.df-result`, `.df-tile`, `.df-badge--*`, …), or load `_ds_bundle.js` and mount the React components via `window.DepthFusionDesignSystem_ab16e1`.
4. For the logo, use the `LogoMark` component (or copy its SVG from `components/brand/LogoMark.jsx`) and add an `animation` (`"develop pulse"`, `"breathe"`, …).

## Brand musts
- Darkroom Amber only: warm near-black surfaces, sodium amber accent, ember, warm ivory type. No blue/indigo, no neutral-gray dark mode.
- Fraunces (display, light weight, italic-amber emphasis) · Inter Tight (body) · JetBrains Mono (code/labels/slate captions).
- Sharp radii (2–7px). Warm shadows with an amber rim. Patient, photographic motion. No emoji — stroke SVG icons only.
