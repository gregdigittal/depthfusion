import { useEffect, useState, useCallback } from 'react'
import { getServerUrl, setServerUrl, loadTokens, logoutUser } from './lib/ipc'
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
  const [serverUrl, setServerUrlState] = useState<string>('https://localhost:8000')
  const [inputUrl, setInputUrl] = useState<string>('https://localhost:8000')
  const [profile, setProfile] = useState<ProfileInfo | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string>('')
  const [signingOut, setSigningOut] = useState(false)

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
      // After sign-out, navigate back (the app will re-evaluate auth state)
      onBack()
    } catch (err: unknown) {
      console.error('Sign-out error:', err)
    } finally {
      setSigningOut(false)
    }
  }, [onBack])

  const isDirty = inputUrl.trim() !== serverUrl

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4 flex items-center gap-3">
        <button
          onClick={onBack}
          className="text-gray-400 hover:text-gray-200 transition-colors mr-1"
          aria-label="Back"
        >
          ←
        </button>
        <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-white text-sm">
          DF
        </div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      </header>

      {/* Content */}
      <main className="flex-1 px-6 py-8 max-w-2xl mx-auto w-full">

        {/* Profile section */}
        {profile && (
          <section className="mb-10">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
              Profile
            </h2>
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 flex items-start gap-4">
              <div className="w-12 h-12 rounded-full bg-indigo-600 flex items-center justify-center text-lg font-bold text-white shrink-0">
                {profile.name.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-white truncate">{profile.name}</p>
                <p className="text-sm text-gray-400 truncate">{profile.email}</p>
                <span className="inline-block mt-1 px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                  {profile.role}
                </span>
              </div>
            </div>
          </section>
        )}

        {/* Server URL section */}
        <section className="mb-10">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Server
          </h2>
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <label
              htmlFor="server-url"
              className="block text-sm font-medium text-gray-300 mb-2"
            >
              Server URL
            </label>
            <p className="text-xs text-gray-500 mb-3">
              The base URL of your DepthFusion server. Stored locally and persisted across restarts.
            </p>
            <div className="flex gap-3">
              <input
                id="server-url"
                type="url"
                value={inputUrl}
                onChange={(e) => setInputUrl(e.target.value)}
                placeholder="https://localhost:8000"
                className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isDirty) void handleSave()
                }}
              />
              <button
                onClick={() => void handleSave()}
                disabled={saving || !isDirty}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>

            {/* Save feedback */}
            {saveStatus === 'success' && (
              <p className="mt-2 text-xs text-emerald-400">Server URL saved.</p>
            )}
            {saveStatus === 'error' && (
              <p className="mt-2 text-xs text-red-400">Failed to save: {saveError}</p>
            )}
          </div>
        </section>

        {/* Sign out section */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Account
          </h2>
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
            <p className="text-sm text-gray-400 mb-4">
              Signing out will clear your local session tokens and return you to the login screen.
            </p>
            <button
              onClick={() => void handleSignOut()}
              disabled={signingOut}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-red-600/80 hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {signingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </section>
      </main>
    </div>
  )
}
