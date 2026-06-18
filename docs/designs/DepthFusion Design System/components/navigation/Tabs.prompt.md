**Tabs** — horizontal tab / segment navigation (the app shell's primary nav).

```jsx
const [tab, setTab] = React.useState('dashboard');
<Tabs
  value={tab}
  onChange={setTab}
  tabs={[
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'search', label: 'Search' },
    { id: 'graph', label: 'Graph' },
  ]}
/>
```

- Active tab gets the amber treatment. `tabs` accepts plain strings too.
