import { useEffect, useRef, useState } from 'react'
import { useSearch } from './hooks/useSearch'
import { FacetPanel, type FacetState } from './components/FacetPanel'
import { ResultCard, type SearchResult } from './components/ResultCard'
import { getServerUrl } from './lib/ipc'

interface SearchPageProps {
  onOpenDocument?: (id: string) => void
}

const DEFAULT_FACETS: FacetState = {
  dateRange: 'all',
  sourceType: [],
  classification: [],
}

export function SearchPage({ onOpenDocument }: SearchPageProps) {
  const [serverUrl, setServerUrlState] = useState<string>('')
  useEffect(() => {
    getServerUrl().then(setServerUrlState).catch(console.error)
  }, [])
  const { query, setQuery, results, isLoading, error, latencyMs } =
    useSearch(serverUrl)
  const [facets, setFacets] = useState<FacetState>(DEFAULT_FACETS)
  const [facetCollapsed, setFacetCollapsed] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // ⌘K / Ctrl+K global shortcut
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  // Filter results by active facets (client-side for now)
  const filtered = results.filter((r) => {
    if (facets.sourceType.length > 0 && !facets.sourceType.includes(r.source)) {
      return false
    }
    if (facets.classification.length > 0 && !facets.classification.includes(r.classification)) {
      return false
    }
    return true
  })

  const highlightTerms = query.trim().length > 0 ? query.trim().split(/\s+/) : []

  return (
    <div className="flex flex-col h-full">
      {/* Search bar */}
      <div className="df-searchbar">
        <div className="df-searchbar__wrap">
          <span className="df-searchbar__icon" aria-hidden="true">⌕</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search knowledge graph… (⌘K)"
            className="df-input df-input--icon"
          />
        </div>
        <button
          onClick={() => setFacetCollapsed((c) => !c)}
          className="df-btn df-btn--ghost df-btn--sm"
        >
          {facetCollapsed ? 'Show filters' : 'Hide filters'}
        </button>
      </div>

      {/* Status bar */}
      {(isLoading || error || results.length > 0 || query.trim()) && (
        <div className="df-search-meta">
          {isLoading && 'Searching…'}
          {!isLoading && error && (
            <span style={{ color: 'var(--danger)' }}>{error}</span>
          )}
          {!isLoading && !error && query.trim() && (
            <>
              {filtered.length} result{filtered.length !== 1 ? 's' : ''}
              {latencyMs !== null && ` · ${latencyMs}ms`}
            </>
          )}
        </div>
      )}

      {/* Body: facets + results */}
      <div className="df-search-layout">
        <FacetPanel
          facets={facets}
          onChange={setFacets}
          collapsed={facetCollapsed}
        />

        <div className="df-search-results">
          {!isLoading && !error && query.trim() && filtered.length === 0 && (
            <div className="df-search-empty">
              <p style={{ fontSize: 'var(--fs-h2)', marginBottom: 'var(--sp-2)' }}>⌕</p>
              <p>No results for "{query}"</p>
              {results.length > 0 && (
                <p style={{ fontSize: 'var(--fs-small)', marginTop: 'var(--sp-1)', color: 'var(--muted)' }}>
                  Try adjusting your filters
                </p>
              )}
            </div>
          )}

          {!query.trim() && (
            <div className="df-search-empty">
              <p style={{ fontSize: 'var(--fs-h2)', marginBottom: 'var(--sp-2)' }}>⧫</p>
              <p>Start typing to search your knowledge graph</p>
              <p style={{ fontSize: 'var(--fs-small)', marginTop: 'var(--sp-1)', color: 'var(--muted)' }}>
                Use ⌘K to focus the search box
              </p>
            </div>
          )}

          {filtered.map((result: SearchResult) => (
            <ResultCard
              key={result.id}
              result={result}
              highlightTerms={highlightTerms}
              onClick={(r) => onOpenDocument?.(r.id)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
