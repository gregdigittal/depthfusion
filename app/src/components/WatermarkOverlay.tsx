interface WatermarkOverlayProps {
  label?: string
  opacity?: number
}

export function WatermarkOverlay({
  label = 'RESTRICTED',
  opacity = 0.08,
}: WatermarkOverlayProps) {
  // Build an SVG data URI with rotated repeating text
  const svgText = encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="220" height="120">
      <text
        x="50%"
        y="50%"
        dominant-baseline="middle"
        text-anchor="middle"
        font-family="sans-serif"
        font-size="22"
        font-weight="bold"
        fill="white"
        transform="rotate(-30 110 60)"
        letter-spacing="4"
      >${label}</text>
    </svg>
  `)

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        opacity,
        pointerEvents: 'none',
        backgroundImage: `url("data:image/svg+xml,${svgText}")`,
        backgroundRepeat: 'repeat',
        backgroundSize: '220px 120px',
        zIndex: 10,
        userSelect: 'none',
      }}
    />
  )
}
