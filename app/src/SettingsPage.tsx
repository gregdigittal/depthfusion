import { useEffect, useState, useCallback } from 'react'
import { getServerUrl, setServerUrl, loadTokens, logoutUser, setWizardCompleted } from './lib/ipc'
import { decodeJwtPayload, extractRole } from './lib/jwt'

interface ProfileInfo {
  name: string
  email: string
  role: string
}

interface SettingsPageProps {
  onBack: () => void
}

/** Settings page: server URL configuration + user profile + sign-out (T-634). */
export function SettingsPage({ onBack }: SettingsPageProps) {
  const [serverUrl, setServerUrlState] = useState<string>('https://mcp.tonracein.com')
  const [inputUrl, setInputUrl] = useState<string>('https://mcp.tonracein.com')
  const [profile, setProfile] = useState<ProfileInfo | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string>('')
  const [signingOut, setSigningOut] = useState(false)
  const [rerunning, setRerunning] = useState(false)

  // Load current settings and profile on mount
  useEffect(() => {
    getServerUrl()
      .then((url) => {
        setServerUrlState(url)
        setInputUrl(url)
      })
      .catch(console.error)

    loadTokens()
      .then((tokens) => {
        if (!tokens?.id_token) return
        const claims = decodeJwtPayload(tokens.id_token)
        if (!claims) return
        setProfile({
          name: claims.name ?? claims.email ?? claims.sub ?? 'Unknown',
          email: claims.email ?? '—',
          role: extractRole(claims),
        })
      })
      .catch(console.error)
  }, [])

  const handleSave = useCallback(async () => {
    const trimmed = inputUrl.trim()
    if (!trimmed) return

    setSaving(true)
    setSaveStatus('idle')
    setSaveError('')

    try {
      await setServerUrl(trimmed)
      setServerUrlState(trimmed)
      setSaveStatus('success')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setSaveError(msg)
      setSaveStatus('error')
    } finally {
      setSaving(false)
    }
  }, [inputUrl])

  const handleSignOut = useCallback(async () => {
    setSigningOut(true)
    try {
      await logoutUser()
      onBack()
    } catch (err: unknown) {
      console.error('Sign-out error:', err)
    } finally {
      setSigningOut(false)
    }
  }, [onBack])

  const handleRerunWizard = useCallback(async () => {
    setRerunning(true)
    try {
      await setWizardCompleted(false)
      window.location.reload()
    } catch (err: unknown) {
      console.error('Failed to re-trigger setup wizard:', err)
      setRerunning(false)
    }
  }, [])

  const isDirty = inputUrl.trim() !== serverUrl

  return (
    <div className="df-page overflow-y-auto">
      <div className="df-settings">

        {/* Profile card */}
        {profile && (
          <div className="df-card">
            <div className="df-card__head">
              <span className="df-tile__label">Profile</span>
            </div>
            <div className="df-card__body">
              <div className="df-card__row">
                <div
                  className="df-avatar"
                  style={{ width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--fs-h3)', fontWeight: 600, color: 'var(--on-accent)' }}
                >
                  {profile.name.charAt(0).toUpperCase()}
                </div>
                <div className="df-card__stack">
                  <p className="df-card__name">{profile.name}</p>
                  <p className="df-card__email">{profile.email}</p>
                  <span className="df-rolebadge">{profile.role}</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Server URL card */}
        <div className="df-card">
          <div className="df-card__head">
            <span className="df-tile__label">Server</span>
          </div>
          <div className="df-card__body">
            <label htmlFor="server-url" className="df-card__field">
              <span style={{ fontSize: 'var(--fs-body)', fontWeight: 500, color: 'var(--text)' }}>
                Server URL
              </span>
              <span style={{ fontSize: 'var(--fs-small)', color: 'var(--muted)', marginTop: 'var(--sp-1)' }}>
                The base URL of your DepthFusion server. Stored locally and persisted across restarts.
              </span>
            </label>
            <input
              id="server-url"
              type="url"
              value={inputUrl}
              onChange={(e) => setInputUrl(e.target.value)}
              placeholder="https://mcp.tonracein.com"
              className="df-input"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isDirty) void handleSave()
              }}
            />
            <div className="df-card__actions">
              <button
                onClick={() => void handleSave()}
                disabled={saving || !isDirty}
                className="df-btn df-btn--primary"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              {saveStatus === 'success' && (
                <p className="df-card__saved">Server URL saved.</p>
              )}
              {saveStatus === 'error' && (
                <p className="df-card__saved" style={{ color: 'var(--danger)' }}>
                  Failed to save: {saveError}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Account / sign-out card */}
        <div className="df-card">
          <div className="df-card__head">
            <span className="df-tile__label">Account</span>
          </div>
          <div className="df-card__body">
            <p className="df-card__desc">
              Signing out will clear your local session tokens and return you to the login screen.
            </p>
            <button
              onClick={() => void handleSignOut()}
              disabled={signingOut}
              className="df-btn df-btn--danger"
            >
              {signingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </div>

        {/* Setup / wizard card */}
        <div className="df-card">
          <div className="df-card__head">
            <span className="df-tile__label">Setup</span>
          </div>
          <div className="df-card__body">
            <p className="df-card__desc">
              Re-run the first-time setup wizard to change your deployment mode or reconfigure your server.
            </p>
            <button
              onClick={() => void handleRerunWizard()}
              disabled={rerunning}
              className="df-btn"
            >
              {rerunning ? 'Resetting…' : 'Re-run setup wizard'}
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}
