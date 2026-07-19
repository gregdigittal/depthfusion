**ResultCard** — a single search result (title, classification + source badges, highlighted snippet, score bar, date, locator).

{% raw %}
```jsx
<ResultCard result={{
  title: 'Microservices Architecture Patterns — ADR-003',
  cls: 'internal', source: 'Document',
  snippet: 'The {microservices} {patterns} documented in this ADR…',
  score: 92, date: '2026-05-14', loc: 'docs/adr/ADR-003.md',
}} />
```
{% endraw %}

- Wrap query terms in `{curly braces}` inside `snippet` to highlight them. Score bar turns ember <50%, amber 50–80%, glow >80%.
