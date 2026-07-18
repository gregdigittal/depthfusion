import { useState, useCallback } from 'react'

export interface TileConfig {
  id: string
  title: string
  width: 1 | 2
  height: 1 | 2
}

const STORAGE_KEY = 'depthfusion-dashboard-layout'

const DEFAULT_TILES: TileConfig[] = [
  { id: 'recent-activity', title: 'Recent Activity', width: 2, height: 1 },
  { id: 'search-stats', title: 'Search Stats', width: 1, height: 1 },
  { id: 'storage-usage', title: 'Storage Usage', width: 1, height: 1 },
  { id: 'sync-status', title: 'Sync Status', width: 1, height: 1 },
  { id: 'cognitive-summary', title: 'Cognition', width: 1, height: 1 },
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
