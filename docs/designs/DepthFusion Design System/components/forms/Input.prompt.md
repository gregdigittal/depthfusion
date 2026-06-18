**Input** — text field. Pass `icon` to get the search-field layout.

```jsx
<Input placeholder="Server URL" defaultValue="http://127.0.0.1:7474" />
<Input icon={<SearchIcon/>} placeholder="Search knowledge graph… (⌘K)" />
```

- Without `icon` it's a plain input; with `icon` it renders the leading-icon wrapper. Forwards all native input props.
