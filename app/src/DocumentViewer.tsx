import { useEffect, useRef, useState } from 'react'
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

interface DocumentViewerProps {
  documentId: string | null
  onClose: () => void
  highlightLocator?: string
}

interface OutlineItem {
  text: string
  locator: string
  depth: number
}

export function DocumentViewer({
  documentId,
  onClose,
  highlightLocator,
}: DocumentViewerProps) {
  const serverUrl = getServerUrl()
  const [doc, setDoc] = useState<DocumentContent | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const blocksRef = useRef<Map<string, HTMLElement>>(new Map())

  // Fetch document on id change
  useEffect(() => {
    if (!documentId) {
      setDoc(null)
      return
    }
    let cancelled = false
    void (async () => {
      setIsLoading(true)
      setError(null)
      setDoc(null)
      try {
        const resp = await fetch(`${serverUrl}/api/v1/documents/${documentId}`)
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
        const data: DocumentContent = await resp.json() as DocumentContent
        if (!cancelled) setDoc(data)
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [documentId, serverUrl])

  // Scroll to highlighted locator
  useEffect(() => {
    if (!highlightLocator) return
    const el = blocksRef.current.get(highlightLocator)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlightLocator, doc])

  async function handleDownload() {
    if (!documentId) return
    setIsDownloading(true)
    try {
      await invoke<void>('download_document', { documentId })
    } catch {
      // Tauri invoke errors are surfaced via the OS download handler
    } finally {
      setIsDownloading(false)
    }
  }

  // Build heading outline from blocks
  const outline: OutlineItem[] = doc
    ? doc.blocks
        .filter((b) => b.heading_path.length > 0)
        .map((b) => ({
          text: b.heading_path[b.heading_path.length - 1],
          locator: b.locator,
          depth: b.heading_path.length - 1,
        }))
    : []

  if (!documentId) return null

  return (
    <div className="h-full flex flex-col bg-gray-950 border-l border-gray-800">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 shrink-0">
        <h2 className="text-sm font-semibold text-white truncate flex-1 mr-3">
          {doc?.title ?? 'Loading…'}
        </h2>
        <div className="flex items-center gap-2 shrink-0">
          {doc?.canDownload && (
            <button
              onClick={() => void handleDownload()}
              disabled={isDownloading}
              className="px-3 py-1.5 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50 transition-colors"
            >
              {isDownloading ? 'Downloading…' : 'Download'}
            </button>
          )}
          {doc?.classification && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-gray-800 text-gray-400 border border-gray-700 uppercase tracking-wide">
              {doc.classification}
            </span>
          )}
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors p-1"
            aria-label="Close document"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Outline sidebar */}
        {outline.length > 0 && (
          <div className="w-44 shrink-0 border-r border-gray-800 overflow-y-auto py-3">
            <p className="px-3 pb-2 text-xs text-gray-600 uppercase tracking-wider">
              Outline
            </p>
            {outline.map((item, i) => (
              <button
                key={i}
                onClick={() => {
                  const el = blocksRef.current.get(item.locator)
                  el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}
                className="w-full text-left px-3 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-800 transition-colors truncate"
                style={{ paddingLeft: `${12 + item.depth * 12}px` }}
                title={item.text}
              >
                {item.text}
              </button>
            ))}
          </div>
        )}

        {/* Document content */}
        <div className="flex-1 overflow-y-auto px-6 py-5 relative">
          {/* Watermark for restricted documents */}
          {doc?.classification === 'restricted' && (
            <WatermarkOverlay label="RESTRICTED" opacity={0.07} />
          )}

          {isLoading && (
            <div className="flex items-center justify-center h-32 text-gray-500 text-sm">
              Loading document…
            </div>
          )}

          {!isLoading && error && (
            <div className="text-center py-16">
              <p className="text-red-400 text-sm mb-2">Failed to load document</p>
              <p className="text-gray-600 text-xs">{error}</p>
            </div>
          )}

          {!isLoading && doc && (
            <article className="prose prose-invert prose-sm max-w-none">
              {doc.blocks.map((block) => {
                const isHeading = block.heading_path.length > 0
                const isHighlighted =
                  highlightLocator && block.locator === highlightLocator

                const Tag: keyof JSX.IntrinsicElements =
                  isHeading
                    ? (`h${Math.min(block.heading_path.length + 1, 6)}` as keyof JSX.IntrinsicElements)
                    : 'p'

                return (
                  <Tag
                    key={block.id}
                    ref={(el) => {
                      if (el) blocksRef.current.set(block.locator, el)
                      else blocksRef.current.delete(block.locator)
                    }}
                    className={
                      isHighlighted
                        ? 'bg-indigo-900/40 rounded -mx-2 px-2'
                        : undefined
                    }
                  >
                    {block.text}
                  </Tag>
                )
              })}
            </article>
          )}
        </div>
      </div>
    </div>
  )
}
