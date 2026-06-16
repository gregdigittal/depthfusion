import { useState } from 'react'

export interface FacetState {
  dateRange: 'all' | '7d' | '30d' | '90d'
  sourceType: string[]
  classification: string[]
}

interface FacetPanelProps {
  facets: FacetState
  onChange: (facets: FacetState) => void
  collapsed: boolean
}

const DATE_OPTIONS: { label: string; value: FacetState['dateRange'] }[] = [
  { label: 'All time', value: 'all' },
  { label: 'Last 7 days', value: '7d' },
  { label: 'Last 30 days', value: '30d' },
  { label: 'Last 90 days', value: '90d' },
]

const SOURCE_TYPES = ['Document', 'Web', 'Email', 'Slack', 'Code', 'Database']
const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted']

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="df-facets__group">
      <button
        onClick={() => setOpen((o) => !o)}
        className="df-facets__head"
        aria-expanded={open}
      >
        {title}
        <span className="df-facets__chevron">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="df-facets__items">{children}</div>}
    </div>
  )
}

function toggleMulti(arr: string[], value: string): string[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value]
}

export function FacetPanel({ facets, onChange, collapsed }: FacetPanelProps) {
  if (collapsed) return null

  return (
    <aside className="df-facets">
      {/* Date Range */}
      <Section title="Date">
        {DATE_OPTIONS.map((opt) => (
          <label key={opt.value} className="df-facet-item">
            <input
              type="radio"
              name="dateRange"
              value={opt.value}
              checked={facets.dateRange === opt.value}
              onChange={() => onChange({ ...facets, dateRange: opt.value })}
            />
            {opt.label}
          </label>
        ))}
      </Section>

      {/* Source Type */}
      <Section title="Source">
        {SOURCE_TYPES.map((src) => (
          <label key={src} className="df-facet-item">
            <input
              type="checkbox"
              checked={facets.sourceType.includes(src)}
              onChange={() =>
                onChange({
                  ...facets,
                  sourceType: toggleMulti(facets.sourceType, src),
                })
              }
            />
            {src}
          </label>
        ))}
      </Section>

      {/* Classification */}
      <Section title="Classification">
        {CLASSIFICATIONS.map((cls) => (
          <label key={cls} className="df-facet-item" style={{ textTransform: 'capitalize' }}>
            <input
              type="checkbox"
              checked={facets.classification.includes(cls)}
              onChange={() =>
                onChange({
                  ...facets,
                  classification: toggleMulti(facets.classification, cls),
                })
              }
            />
            {cls}
          </label>
        ))}
      </Section>
    </aside>
  )
}
