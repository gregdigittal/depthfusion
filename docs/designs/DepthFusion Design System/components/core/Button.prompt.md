**Button** — primary action control.

```jsx
<Button>Begin the installer →</Button>
<Button variant="secondary">Open Document</Button>
<Button variant="danger">Sign out</Button>
<Button variant="ghost">Hide filters</Button>
<Button disabled>Opening browser…</Button>
```

- `variant`: `primary` (amber fill + glow), `secondary` (amber outline), `danger` (ember), `ghost` (subtle bordered). Passes through all native button props (`onClick`, `disabled`, …).
