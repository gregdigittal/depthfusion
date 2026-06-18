/**
 * The DepthFusion mark: a memory-graph (3 outer nodes + hub + spokes) on a
 * rounded amber plate. Doubles as the brand's animation artifact.
 */
export interface LogoMarkProps {
  /** Pixel size (square). Default 32. Outer nodes vanish below ~16px — use `flat`. */
  size?: number;
  /** Plate (background square) fill. Default the amber accent token. */
  plate?: string;
  /** Mark (nodes/spokes/hub) fill. Default --on-accent (dark ember). */
  mark?: string;
  /** Simplified variant: hub + nodes only, no spokes/triangle. For small sizes. */
  flat?: boolean;
  /**
   * Space-separated animation tokens. Any of:
   *  - "breathe" : patient amber glow loop (ambient)
   *  - "develop" : resolves from blurred/dim → sharp, once on mount
   *  - "pulse"   : nodes & hub breathe on a stagger
   *  - "draw"    : spokes draw in on mount
   * e.g. animation="develop pulse"
   */
  animation?: string;
  className?: string;
}

export function LogoMark(props: LogoMarkProps): JSX.Element;
