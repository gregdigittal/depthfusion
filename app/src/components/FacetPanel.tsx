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
    <div className="border-b border-gray-800 last:border-b-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-300 hover:text-white transition-colors"
      >
        {title}
        <span className="text-gray-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  )
}

function toggleMulti(arr: string[], value: string): string[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value]
}

export function FacetPanel({ facets, onChange, collapsed }: FacetPanelProps) {
  if (collapsed) return null

  return (
    <aside className="w-56 shrink-0 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden h-fit">
      <div className="px-4 py-3 border-b border-gray-800">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Filters
        </h3>
      </div>

      {/* Date Range */}
      <Section title="Date">
        <div className="space-y-1.5">
          {DATE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className="flex items-center gap-2 cursor-pointer group"
            >
              <input
                type="radio"
                name="dateRange"
                value={opt.value}
                checked={facets.dateRange === opt.value}
                onChange={() =>
                  onChange({ ...facets, dateRange: opt.value })
                }
                className="accent-indigo-500"
              />
              <span className="text-sm text-gray-300 group-hover:text-white transition-colors">
                {opt.label}
              </span>
            </label>
          ))}
        </div>
      </Section>

      {/* Source Type */}
      <Section title="Source">
        <div className="space-y-1.5">
          {SOURCE_TYPES.map((src) => (
            <label
              key={src}
              className="flex items-center gap-2 cursor-pointer group"
            >
              <input
                type="checkbox"
                checked={facets.sourceType.includes(src)}
                onChange={() =>
                  onChange({
                    ...facets,
                    sourceType: toggleMulti(facets.sourceType, src),
                  })
                }
                className="accent-indigo-500"
              />
              <span className="text-sm text-gray-300 group-hover:text-white transition-colors">
                {src}
              </span>
            </label>
          ))}
        </div>
      </Section>

      {/* Classification */}
      <Section title="Classification">
        <div className="space-y-1.5">
          {CLASSIFICATIONS.map((cls) => (
            <label
              key={cls}
              className="flex items-center gap-2 cursor-pointer group"
            >
              <input
                type="checkbox"
                checked={facets.classification.includes(cls)}
                onChange={() =>
                  onChange({
                    ...facets,
                    classification: toggleMulti(
                      facets.classification,
                      cls
                    ),
                  })
                }
                className="accent-indigo-500"
              />
              <span className="text-sm text-gray-300 group-hover:text-white transition-colors capitalize">
                {cls}
              </span>
            </label>
          ))}
        </div>
      </Section>
    </aside>
  )
}
