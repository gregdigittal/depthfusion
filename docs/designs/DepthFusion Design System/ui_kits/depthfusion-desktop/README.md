# DepthFusion Desktop — UI Kit

An interactive, high-fidelity recreation of the **DepthFusion** desktop app (Tauri v2 · React 18) in the **Darkroom Amber** brand direction. It composes the design system's component primitives from `_ds_bundle.js` and is styled entirely by the root `styles.css`.

## Run
Open `index.html`. No build step. It loads React + Babel + the compiled DS bundle, then `data.js` (mock data), `icons.jsx` (chrome glyphs), and `screens.jsx` (the app).

## Surfaces
- **Auth** — centered sign-in with the animated LogoMark (`develop` + `pulse`), idle → pending → authenticated.
- **App shell** — title bar, brand, `Tabs` nav, version, settings, sign out.
- **Dashboard** — Recent Activity, Search Stats, Storage Usage, Sync Status tiles (mock data).
- **Search** — `Input` search field, collapsible `FacetPanel` (`Radio`/`Checkbox`), `ResultCard` list with live first-term filtering, empty state.
- **Graph** — a **Time Machine** knowledge graph: a radial constellation on a starfield, **drag to rotate**, **scroll to zoom**, **click a node to drill into its dependencies** (the parent constellation recedes into depth behind you), and recede back out via the breadcrumb, the right-side depth rail, the `↑ out` button, or `Esc`. Links attach to node perimeters; dependency counts badge each node. The inspector lists the focused node's dependencies (click to drill).
- **Settings** — Profile (`Avatar`), Server URL (`Input` + save state), Account (destructive `Button`).

## DS components used
`LogoMark · Button · Badge · Tabs · ResultCard · NodeChip · Avatar · Input · Checkbox · Radio · Card` — all from `window.DepthFusionDesignSystem_ab16e1`.

## Fidelity notes
- This is a cosmetic recreation, not production code. Layout/states mirror the 2026-06-15 handoff; the auth state machine, IPC, and real data wiring are out of scope.
- Recolored from the handoff's indigo to Darkroom Amber per the chosen brand direction. Classification colors were warm-shifted (no blue) to stay cohesive.
