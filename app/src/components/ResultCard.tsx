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

function highlightSnippet(text: string, terms: string[]): React.ReactNode {
  if (!terms.length) return text
  const pattern = new RegExp(
    `(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`,
    'gi'
  )
  const parts = text.split(pattern)
  return parts.map((part, i) =>
    pattern.test(part) ? <mark key={i}>{part}</mark> : part
  )
}

function scoreFileTier(score: number): 'low' | 'mid' | 'high' {
  const pct = Math.round(score * 100)
  if (pct >= 70) return 'high'
  if (pct >= 40) return 'mid'
  return 'low'
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const tier = scoreFileTier(score)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
      <div className="df-scorebar">
        <div
          className={`df-scorebar__fill df-scorebar__fill--${tier}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span style={{ fontSize: 'var(--fs-micro)', color: 'var(--muted)' }}>{pct}%</span>
    </div>
  )
}

export function ResultCard({ result, highlightTerms = [], onClick }: ResultCardProps) {
  const ts = new Date(result.timestamp).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })

  return (
    <article
      className="df-result"
      onClick={() => onClick?.(result)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick?.(result)
      }}
    >
      {/* Title row */}
      <div className="df-result__top">
        <h3 className="df-result__title">{result.title}</h3>
        <span className={`df-badge df-badge--${result.classification}`}>
          {result.classification.charAt(0).toUpperCase() + result.classification.slice(1)}
        </span>
      </div>

      {/* Source badge */}
      <div style={{ marginBottom: 'var(--sp-2)' }}>
        <span className="df-badge df-badge--source">{result.source}</span>
      </div>

      {/* Snippet */}
      <p className="df-result__snippet">
        {highlightSnippet(result.snippet, highlightTerms)}
      </p>

      {/* Bottom meta row */}
      <div className="df-result__meta">
        <ScoreBar score={result.score} />
        <span className="df-result__date">{ts}</span>
        <code
          className="df-inspector__value--mono"
          style={{ maxWidth: '10rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          title={result.locator}
        >
          {result.locator}
        </code>
      </div>
    </article>
  )
}
