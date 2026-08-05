import { useState, useCallback } from 'react'

export interface TileConfig {
  id: string
  title: string
  width: 1 | 2
  height: 1 | 2
}

/**
 * Bumped from 'depthfusion-dashboard-layout' when the 'checkpoint-timeline' tile
 * was added (S-253 T-861). Existing users hold a persisted layout array that
 * predates the new tile, and `loadFromStorage` returns that array verbatim — so
 * without a new key the tile would never appear for anyone who has ever opened
 * the dashboard. The old key is silently abandoned rather than migrated: this is
 * a local Tauri app and the only state lost is tile order, which is the
 * lowest-risk migration available.
 */
const STORAGE_KEY = 'depthfusion-dashboard-layout-v2'

const DEFAULT_TILES: TileConfig[] = [
  { id: 'recent-activity', title: 'Recent Activity', width: 2, height: 1 },
  { id: 'search-stats', title: 'Search Stats', width: 1, height: 1 },
  { id: 'storage-usage', title: 'Storage Usage', width: 1, height: 1 },
  { id: 'sync-status', title: 'Sync Status', width: 1, height: 1 },
  { id: 'cognitive-summary', title: 'Cognition', width: 1, height: 1 },
  // Kebab-case id, matching the ids above and the tileContent keys in
  // DashboardPage.tsx. The two must be byte-identical or the tile renders null.
  { id: 'checkpoint-timeline', title: 'Checkpoint Timeline', width: 2, height: 2 },
]

function loadFromStorage(): TileConfig[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_TILES
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length > 0) {
      return parsed as TileConfig[]
    }
  } catch {
    // ignore parse errors
  }
  return DEFAULT_TILES
}

function saveToStorage(tiles: TileConfig[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tiles))
  } catch {
    // ignore storage errors
  }
}

interface UseDashboardReturn {
  tiles: TileConfig[]
  updateTileLayout: (tiles: TileConfig[]) => void
  resetToDefaults: () => void
}

export function useDashboard(): UseDashboardReturn {
  const [tiles, setTiles] = useState<TileConfig[]>(loadFromStorage)

  const updateTileLayout = useCallback((next: TileConfig[]) => {
    setTiles(next)
    saveToStorage(next)
  }, [])

  const resetToDefaults = useCallback(() => {
    setTiles(DEFAULT_TILES)
    saveToStorage(DEFAULT_TILES)
  }, [])

  return { tiles, updateTileLayout, resetToDefaults }
}
