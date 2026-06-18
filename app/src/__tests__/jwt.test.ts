import { describe, it, expect } from 'vitest'
import { decodeJwtPayload, extractRole, type JwtClaims } from '../lib/jwt'

// Build a minimal JWT string: header.payload.signature (no real crypto needed for display-only decode)
function makeJwt(payload: Record<string, unknown>): string {
  const encoded = Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `eyJhbGciOiJSUzI1NiJ9.${encoded}.fakesig`
}

describe('decodeJwtPayload', () => {
  it('decodes a valid JWT payload', () => {
    const jwt = makeJwt({ sub: 'user-123', email: 'test@example.com' })
    const claims = decodeJwtPayload(jwt)
    expect(claims).not.toBeNull()
    expect(claims!.sub).toBe('user-123')
    expect(claims!.email).toBe('test@example.com')
  })

  it('returns null for a string with too few segments', () => {
    expect(decodeJwtPayload('onlyone')).toBeNull()
    expect(decodeJwtPayload('two.parts')).toBeNull()
  })

  it('returns null when the payload is invalid base64', () => {
    expect(decodeJwtPayload('header.!!!invalid!!!.sig')).toBeNull()
  })

  it('returns null for an empty string', () => {
    expect(decodeJwtPayload('')).toBeNull()
  })

  it('handles standard base64 padding variants', () => {
    // Payloads of varying length produce different padding requirements
    const short = makeJwt({ x: 1 })
    const medium = makeJwt({ x: 1, y: 'ab' })
    expect(decodeJwtPayload(short)).not.toBeNull()
    expect(decodeJwtPayload(medium)).not.toBeNull()
  })
})

describe('extractRole', () => {
  it('prefers df:role over everything else', () => {
    const claims: JwtClaims = {
      'df:role': 'admin',
      roles: ['user'],
      realm_access: { roles: ['reader'] },
    }
    expect(extractRole(claims)).toBe('admin')
  })

  it('falls back to first element of roles array', () => {
    const claims: JwtClaims = { roles: ['editor', 'viewer'] }
    expect(extractRole(claims)).toBe('editor')
  })

  it('handles roles as a plain string', () => {
    const claims: JwtClaims = { roles: 'moderator' }
    expect(extractRole(claims)).toBe('moderator')
  })

  it('uses realm_access.roles, skipping default- prefixed entries', () => {
    const claims: JwtClaims = {
      realm_access: { roles: ['default-roles-myrealm', 'depthfusion-writer'] },
    }
    expect(extractRole(claims)).toBe('depthfusion-writer')
  })

  it('returns "user" when only default- realm roles are present', () => {
    const claims: JwtClaims = {
      realm_access: { roles: ['default-roles-myrealm', 'default-offline-access'] },
    }
    expect(extractRole(claims)).toBe('user')
  })

  it('returns "user" when no role claims are present', () => {
    expect(extractRole({})).toBe('user')
  })

  it('returns "user" when roles array is empty', () => {
    expect(extractRole({ roles: [] })).toBe('user')
  })
})
