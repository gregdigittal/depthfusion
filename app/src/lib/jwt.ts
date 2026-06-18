/**
 * Minimal JWT payload decoder (no verification — this is display-only).
 * The token is already cryptographically verified by the OIDC provider
 * before it reaches the vault; here we just extract display claims.
 */

export interface JwtClaims {
  sub?: string
  name?: string
  email?: string
  /** OIDC standard roles claim (string or array) */
  roles?: string | string[]
  /** Keycloak realm_access.roles */
  realm_access?: { roles?: string[] }
  /** Custom DepthFusion role claim */
  'df:role'?: string
}

/**
 * Decode the payload section of a JWT string.
 * Returns `null` for malformed tokens rather than throwing.
 */
export function decodeJwtPayload(token: string): JwtClaims | null {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return null
    const payload = parts[1]
    // Pad to a multiple of 4 for atob
    const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4)
    const json = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
    return JSON.parse(json) as JwtClaims
  } catch {
    return null
  }
}

/** Extract a human-readable role string from the token claims. */
export function extractRole(claims: JwtClaims): string {
  if (claims['df:role']) return claims['df:role']
  if (Array.isArray(claims.roles) && claims.roles.length > 0) return claims.roles[0]
  if (typeof claims.roles === 'string') return claims.roles
  const realmRoles = claims.realm_access?.roles ?? []
  const appRole = realmRoles.find((r) => !r.startsWith('default-'))
  if (appRole) return appRole
  return 'user'
}
