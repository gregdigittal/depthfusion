interface LogoMarkProps {
  size?: number;
  plate?: string;
  mark?: string;
  flat?: boolean;
  animation?: string;
  className?: string;
}

export function LogoMark({
  size = 32,
  plate = 'var(--accent)',
  mark = 'var(--on-accent)',
  flat = false,
  animation = '',
  className = '',
}: LogoMarkProps) {
  const anims = animation
    .split(/\s+/)
    .filter(Boolean)
    .map((a) => `df-logo--${a}`)
    .join(' ');
  const cls = ['df-logo', anims, className].filter(Boolean).join(' ');

  return (
    <span className={cls}>
      <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <g className="df-logo__mark">
          <rect width="64" height="64" rx="14" fill={plate} />
          {!flat && (
            <g stroke={mark} strokeOpacity="0.55" strokeWidth="1.5">
              <line className="df-logo__spoke" x1="32" y1="32" x2="32" y2="10" />
              <line className="df-logo__spoke" x1="32" y1="32" x2="14" y2="50" />
              <line className="df-logo__spoke" x1="32" y1="32" x2="50" y2="50" />
            </g>
          )}
          {!flat && (
            <polygon
              points="32,10 14,50 50,50"
              fill="none"
              stroke={mark}
              strokeOpacity="0.3"
              strokeWidth="1"
            />
          )}
          <circle className="df-logo__node" cx="32" cy="10" r="5" fill={mark} />
          <circle className="df-logo__node" cx="14" cy="50" r="5" fill={mark} />
          <circle className="df-logo__node" cx="50" cy="50" r="5" fill={mark} />
          <circle className="df-logo__hub" cx="32" cy="32" r="7" fill={mark} />
        </g>
      </svg>
    </span>
  );
}

export default LogoMark;
