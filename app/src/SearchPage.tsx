import { useEffect, useState } from 'react'
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
  const [serverUrl, setServerUrl] = useState('https://localhost:8000')
  const [facets, setFacets] = useState<FacetState>(DEFAULT_FACETS)
  const [facetCollapsed, setFacetCollapsed] = useState(false)

  const { query, setQuery, results, isLoading, error, latencyMs } =
    useSearch(serverUrl)

  useEffect(() => {
    getServerUrl().then(setServerUrl).catch(console.error)
  }, [])

  // Keyboard shortcut: ⌘K / Ctrl+K focuses the search input
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        const input = document.getElementById('search-input') as HTMLInputElement | null
        input?.focus()
        input?.select()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const queryTerms = query.trim().split(/\s+/).filter(Boolean)

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Search bar */}
      <div className="px-6 py-4 border-b border-gray-800">
        <div className="relative max-w-2xl">
          <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none">
            🔍
          </span>
          <input
            id="search-input"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search knowledge base…"
            autoFocus
            className="w-full pl-10 pr-28 py-3 bg-gray-900 border border-gray-700 rounded-xl text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm"
          />
          <kbd className="absolute right-4 top-1/2 -translate-y-1/2 hidden sm:flex items-center gap-1 px-2 py-0.5 text-xs text-gray-500 bg-gray-800 border border-gray-700 rounded">
            ⌘K
          </kbd>
        </div>

        {latencyMs !== null && !isLoading && (
          <p className="mt-2 text-xs text-gray-500">
            {results.length} result{results.length !== 1 ? 's' : ''} in {latencyMs}ms
          </p>
        )}
      </div>

      {/* Body: facets + results */}
      <div className="flex-1 flex gap-6 px-6 py-6 overflow-hidden">
        {/* Toggle button + FacetPanel */}
        <div className="flex flex-col gap-2">
          <button
            onClick={() => setFacetCollapsed((c) => !c)}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors self-start"
            aria-label={facetCollapsed ? 'Show filters' : 'Hide filters'}
          >
            {facetCollapsed ? '▶ Filters' : '◀ Hide'}
          </button>
          <FacetPanel
            facets={facets}
            onChange={setFacets}
            collapsed={facetCollapsed}
          />
        </div>

        {/* Results area */}
        <div className="flex-1 overflow-y-auto space-y-3 pr-1">
          {isLoading && (
            <div className="flex items-center justify-center py-20">
              <svg
                className="animate-spin h-8 w-8 text-indigo-500"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                />
              </svg>
            </div>
          )}

          {!isLoading && error && (
            <div className="rounded-xl bg-red-500/10 border border-red-500/30 px-5 py-4 text-sm text-red-400">
              <strong>Search error:</strong> {error}
            </div>
          )}

          {!isLoading && !error && query.trim() === '' && (
            <div className="flex flex-col items-center justify-center py-24 text-center text-gray-500">
              <span className="text-4xl mb-4">🔍</span>
              <p className="text-lg font-medium mb-1">Start typing to search…</p>
              <p className="text-sm">
                Use <kbd className="px-1.5 py-0.5 bg-gray-800 rounded border border-gray-700 text-xs">⌘K</kbd> to focus the search bar.
              </p>
            </div>
          )}

          {!isLoading && !error && query.trim() !== '' && results.length === 0 && (
            <div className="flex flex-col items-center justify-center py-24 text-center text-gray-500">
              <span className="text-4xl mb-4">📭</span>
              <p className="text-lg font-medium">No results found</p>
              <p className="text-sm">Try adjusting your query or filters.</p>
            </div>
          )}

          {!isLoading &&
            results.map((result: SearchResult) => (
              <ResultCard
                key={result.id}
                result={result}
                highlightTerms={queryTerms}
                onClick={(r) => onOpenDocument?.(r.id)}
              />
            ))}
        </div>
      </div>
    </div>
  )
}
