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
  const serverUrl = getServerUrl()
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
    if (
      facets.sourceType.length > 0 &&
      !facets.sourceType.includes(r.source)
    ) {
      return false
    }
    if (
      facets.classification.length > 0 &&
      !facets.classification.includes(r.classification)
    ) {
      return false
    }
    return true
  })

  const highlightTerms = query.trim().length > 0 ? query.trim().split(/\s+/) : []

  return (
    <div className="flex flex-col h-full">
      {/* Search bar */}
      <div className="px-6 py-4 border-b border-gray-800 shrink-0">
        <div className="flex items-center gap-3 max-w-2xl">
          <div className="relative flex-1">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-sm select-none">
              🔍
            </span>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search knowledge graph… (⌘K)"
              className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500 transition-colors"
            />
          </div>
          <button
            onClick={() => setFacetCollapsed((c) => !c)}
            className="px-3 py-2.5 text-xs rounded-lg border border-gray-700 text-gray-400 hover:text-white hover:border-gray-600 transition-colors whitespace-nowrap"
          >
            {facetCollapsed ? 'Show filters' : 'Hide filters'}
          </button>
        </div>

        {/* Status bar */}
        {(isLoading || error || results.length > 0 || query.trim()) && (
          <div className="mt-2 text-xs text-gray-500 max-w-2xl">
            {isLoading && 'Searching…'}
            {!isLoading && error && (
              <span className="text-red-400">{error}</span>
            )}
            {!isLoading && !error && query.trim() && (
              <>
                {filtered.length} result{filtered.length !== 1 ? 's' : ''}
                {latencyMs !== null && ` · ${latencyMs}ms`}
              </>
            )}
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex flex-1 gap-4 p-6 overflow-hidden min-h-0">
        {/* Facet sidebar */}
        <FacetPanel
          facets={facets}
          onChange={setFacets}
          collapsed={facetCollapsed}
        />

        {/* Results list */}
        <div className="flex-1 overflow-y-auto space-y-3 pr-1">
          {!isLoading && !error && query.trim() && filtered.length === 0 && (
            <div className="text-center py-16 text-gray-500">
              <p className="text-4xl mb-3">🔍</p>
              <p className="text-sm">No results for "{query}"</p>
              {results.length > 0 && filtered.length === 0 && (
                <p className="text-xs mt-1 text-gray-600">
                  Try adjusting your filters
                </p>
              )}
            </div>
          )}

          {!query.trim() && (
            <div className="text-center py-16 text-gray-600">
              <p className="text-4xl mb-3">🧠</p>
              <p className="text-sm">
                Start typing to search your knowledge graph
              </p>
              <p className="text-xs mt-1">
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
