import { useEffect, useState, useRef, useCallback } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { WatermarkOverlay } from './components/WatermarkOverlay'
import { getServerUrl } from './lib/ipc'

export interface Block {
  id: string
  text: string
  heading_path: string[]
  locator: string
}

export interface DocumentContent {
  id: string
  title: string
  blocks: Block[]
  classification: string
  acl_allow: string[]
  canDownload: boolean
}

type ClassChip =
  | 'public'
  | 'internal'
  | 'confidential'
  | 'restricted'

const CLASSIFICATION_STYLES: Record<string, { chip: string }> = {
  public: { chip: 'bg-green-500/20 text-green-300 border-green-500/30' },
  internal: { chip: 'bg-blue-500/20 text-blue-300 border-blue-500/30' },
  confidential: { chip: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30' },
  restricted: { chip: 'bg-red-500/20 text-red-300 border-red-500/30' },
}

interface DocumentViewerProps {
  documentId: string | null
  onClose: () => void
  highlightLocator?: string
}

interface OutlineNode {
  heading: string
  locator: string
}

export function DocumentViewer({
  documentId,
  onClose,
  highlightLocator,
}: DocumentViewerProps) {
  const [serverUrl, setServerUrl] = useState('https://localhost:8000')
  const [doc, setDoc] = useState<DocumentContent | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getServerUrl().then(setServerUrl).catch(console.error)
  }, [])

  useEffect(() => {
    if (!documentId) return
    setIsLoading(true)
    setError(null)
    setDoc(null)

    fetch(`${serverUrl}/api/v1/documents/${encodeURIComponent(documentId)}`)
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`)
        return resp.json() as Promise<DocumentContent>
      })
      .then((data) => {
        setDoc(data)
        setIsLoading(false)
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        setIsLoading(false)
      })
  }, [documentId, serverUrl])

  // Scroll to highlighted block
  useEffect(() => {
    if (!highlightLocator || !contentRef.current) return
    const el = contentRef.current.querySelector(
      `[data-locator="${CSS.escape(highlightLocator)}"]`
    ) as HTMLElement | null
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlightLocator, doc])

  const handleDownload = useCallback(async () => {
    if (!documentId) return
    setDownloading(true)
    setDownloadError(null)
    try {
      await invoke('download_document', { documentId })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setDownloadError(msg)
    } finally {
      setDownloading(false)
    }
  }, [documentId])

  // Build outline from blocks that have heading_path
  const outline: OutlineNode[] =
    doc?.blocks
      .filter((b) => b.heading_path.length > 0)
      .map((b) => ({
        heading: b.heading_path[b.heading_path.length - 1],
        locator: b.locator,
      })) ?? []

  const chipStyle =
    CLASSIFICATION_STYLES[doc?.classification ?? ''] ??
    CLASSIFICATION_STYLES['internal']

  if (!documentId) return null

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-800">
        {isLoading ? (
          <span className="text-gray-400 text-sm">Loading…</span>
        ) : doc ? (
          <>
            <h2 className="font-semibold text-white truncate flex-1">{doc.title}</h2>
            <span
              className={`shrink-0 inline-block px-2 py-0.5 rounded-full text-xs font-medium border ${chipStyle.chip} capitalize`}
            >
              {doc.classification}
            </span>
            {/* Download button (T-642) */}
            {doc.canDownload ? (
              <button
                onClick={() => void handleDownload()}
                disabled={downloading}
                className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {downloading ? 'Downloading…' : 'Download Original'}
              </button>
            ) : (
              <div className="relative group">
                <button
                  disabled
                  className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-gray-700 text-gray-500 cursor-not-allowed opacity-60"
                >
                  Download
                </button>
                <div className="absolute right-0 top-full mt-1 z-20 hidden group-hover:block bg-gray-800 text-gray-300 text-xs rounded-lg px-3 py-2 w-52 border border-gray-700 shadow-xl pointer-events-none">
                  Requires WRITE_OWN_RECORDS permission
                </div>
              </div>
            )}
          </>
        ) : null}
        <button
          onClick={onClose}
          className="shrink-0 text-gray-400 hover:text-white transition-colors ml-1"
          aria-label="Close document"
        >
          ✕
        </button>
      </div>

      {downloadError && (
        <div className="mx-6 mt-3 px-4 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
          Download failed: {downloadError}
        </div>
      )}

      {isLoading && (
        <div className="flex-1 flex items-center justify-center">
          <svg className="animate-spin h-8 w-8 text-indigo-500" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
        </div>
      )}

      {error && (
        <div className="m-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">
          <strong>Failed to load document:</strong> {error}
        </div>
      )}

      {doc && (
        <div className="flex-1 flex min-h-0 overflow-hidden relative">
          {/* Watermark for restricted documents (T-643) */}
          {(doc.classification as ClassChip) === 'restricted' && (
            <WatermarkOverlay />
          )}

          {/* Left sidebar: heading outline */}
          {outline.length > 0 && (
            <nav className="w-52 shrink-0 overflow-y-auto border-r border-gray-800 py-4 px-3">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                Contents
              </p>
              <ul className="space-y-1">
                {outline.map((node, i) => (
                  <li key={i}>
                    <button
                      className="w-full text-left text-sm text-gray-400 hover:text-white truncate transition-colors px-2 py-1 rounded hover:bg-gray-800"
                      onClick={() => {
                        const el = contentRef.current?.querySelector(
                          `[data-locator="${CSS.escape(node.locator)}"]`
                        ) as HTMLElement | null
                        el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                      }}
                    >
                      {node.heading}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}

          {/* Main content: blocks */}
          <div
            ref={contentRef}
            className="flex-1 overflow-y-auto px-8 py-6 space-y-4"
          >
            {doc.blocks.map((block) => {
              const isHighlighted = block.locator === highlightLocator
              return (
                <div
                  key={block.id}
                  id={`block-${block.id}`}
                  data-locator={block.locator}
                  className={`rounded-lg p-4 transition-colors ${
                    isHighlighted
                      ? 'bg-indigo-500/10 border border-indigo-500/40'
                      : 'hover:bg-gray-900'
                  }`}
                >
                  {block.heading_path.length > 0 && (
                    <p className="text-xs text-gray-500 mb-1">
                      {block.heading_path.join(' › ')}
                    </p>
                  )}
                  <p className="text-sm text-gray-300 leading-relaxed">{block.text}</p>
                  <p className="mt-2 text-xs font-mono text-indigo-400/60">
                    {block.locator}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
