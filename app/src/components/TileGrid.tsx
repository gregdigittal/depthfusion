import type { TileConfig } from '../hooks/useDashboard'

interface TileGridProps {
  tiles: TileConfig[]
  children: React.ReactNode
}

export function TileGrid({ tiles, children }: TileGridProps) {
  const childArray = Array.isArray(children) ? children : [children]

  return (
    <div className="df-tiles">
      {tiles.map((tile, i) => {
        const cls = ['df-tile', tile.width > 1 ? 'df-tile--wide' : ''].filter(Boolean).join(' ')
        return (
          <div key={tile.id} className={cls}>
            <div className="df-tile__head">
              <span className="df-tile__label">{tile.title}</span>
            </div>
            <div className="df-tile__body">{childArray[i]}</div>
          </div>
        )
      })}
    </div>
  )
}
