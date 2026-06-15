export interface SearchResult {
  id: string
  title: string
  snippet: string
  score: number
  source: string
  classification: 'public' | 'internal' | 'confidential' | 'restricted'
  locator: string
  timestamp: string
}

interface ResultCardProps {
  result: SearchResult
  highlightTerms?: string[]
  onClick?: (result: SearchResult) => void
}

const CLASSIFICATION_STYLES: Record<
  SearchResult['classification'],
  { chip: string; label: string }
> = {
  public: { chip: 'bg-green-500/20 text-green-300 border-green-500/30', label: 'Public' },
  internal: { chip: 'bg-blue-500/20 text-blue-300 border-blue-500/30', label: 'Internal' },
  confidential: { chip: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30', label: 'Confidential' },
  restricted: { chip: 'bg-red-500/20 text-red-300 border-red-500/30', label: 'Restricted' },
}

function highlightSnippet(text: string, terms: string[]): React.ReactNode {
  if (!terms.length) return text
  const pattern = new RegExp(
    `(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`,
    'gi'
  )
  const parts = text.split(pattern)
  return parts.map((part, i) =>
    pattern.test(part) ? (
      <mark key={i} className="bg-indigo-500/40 text-indigo-100 rounded px-0.5">
        {part}
      </mark>
    ) : (
      part
    )
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-500">{pct}%</span>
    </div>
  )
}

export function ResultCard({ result, highlightTerms = [], onClick }: ResultCardProps) {
  const cls = CLASSIFICATION_STYLES[result.classification]
  const ts = new Date(result.timestamp).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })

  return (
    <article
      className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-600 transition-colors cursor-pointer"
      onClick={() => onClick?.(result)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick?.(result)
      }}
    >
      {/* Title row */}
      <div className="flex items-start justify-between gap-3 mb-2">
        <h3 className="font-semibold text-white leading-snug line-clamp-2">
          {result.title}
        </h3>
        <span
          className={`shrink-0 inline-block px-2 py-0.5 rounded-full text-xs font-medium border ${cls.chip}`}
        >
          {cls.label}
        </span>
      </div>

      {/* Source badge */}
      <div className="mb-2">
        <span className="inline-block px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-400 border border-gray-700">
          {result.source}
        </span>
      </div>

      {/* Snippet */}
      <p className="text-sm text-gray-400 line-clamp-3 mb-3">
        {highlightSnippet(result.snippet, highlightTerms)}
      </p>

      {/* Bottom row */}
      <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-800">
        <ScoreBar score={result.score} />
        <span className="text-xs text-gray-500">{ts}</span>
        <code className="text-xs text-indigo-400 font-mono truncate max-w-[10rem]" title={result.locator}>
          {result.locator}
        </code>
      </div>
    </article>
  )
}
