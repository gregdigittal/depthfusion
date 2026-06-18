**LogoMark** — the DepthFusion memory-graph mark; use for app headers, sign-in, favicons, and as the brand's animated artifact.

```jsx
<LogoMark size={64} animation="develop pulse" />
<LogoMark size={23} flat />                {/* header, small */}
<LogoMark size={28} animation="breathe" /> {/* ambient glow */}
```

Variants & props:
- `size` (px), `flat` (hub+nodes only — use ≤16px), `plate` / `mark` (override fills).
- `animation` accepts space-separated tokens: `breathe` (ambient glow loop), `develop` (blur→sharp on mount), `pulse` (staggered node breathing), `draw` (spokes draw in). Stack them, e.g. `"develop pulse"`.
- Respects `prefers-reduced-motion` automatically (renders the resolved end-state).
- Default plate = amber accent, mark = dark ember (`--on-accent`). On dark surfaces this reads as an ember-on-amber tile.
