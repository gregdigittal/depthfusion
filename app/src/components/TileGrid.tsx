import type { TileConfig } from '../hooks/useDashboard'

interface TileGridProps {
  tiles: TileConfig[]
  children: React.ReactNode
}

export function TileGrid({ tiles, children }: TileGridProps) {
  const childArray = Array.isArray(children) ? children : [children]

  return (
    <div
      className="grid gap-4 p-6"
      style={{
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
      }}
    >
      {tiles.map((tile, i) => {
        const child = childArray[i]
        return (
          <div
            key={tile.id}
            className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden"
            style={{
              gridColumn: tile.width > 1 ? `span ${tile.width}` : undefined,
              gridRow: tile.height > 1 ? `span ${tile.height}` : undefined,
            }}
          >
            <div className="px-4 py-3 border-b border-gray-800">
              <h3 className="text-sm font-semibold text-gray-300">{tile.title}</h3>
            </div>
            <div className="p-4">{child}</div>
          </div>
        )
      })}
    </div>
  )
}
