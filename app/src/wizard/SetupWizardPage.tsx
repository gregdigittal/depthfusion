/**
 * SetupWizardPage — first-run setup wizard state machine (E-65 / S-217).
 *
 * Owns `mode` (solo | vps | connect) and `currentScreen` state, renders the
 * correct screen component, wires onNext / onBack transitions per mode, shows
 * a top progress bar based on step index over total steps per mode, hides the
 * Back button on mode-select and success, and calls setWizardCompleted before
 * invoking the parent onComplete callback.
 *
 * Step counts:
 *   Solo    = 3 (solo-install, solo-api-key, success)
 *   VPS     = 5 (vps-prereq, vps-install, server-url, oidc-signin, success)
 *   Connect = 3 (server-url, oidc-signin, success)
 */

import { useState } from 'react'
import { setWizardCompleted } from '../lib/ipc'
import { LogoMark } from '../components/LogoMark'
import { ModeSelectScreen } from './ModeSelectScreen'
import { SoloInstallScreen } from './SoloInstallScreen'
import { SoloApiKeyScreen } from './SoloApiKeyScreen'
import { VpsPrereqScreen } from './VpsPrereqScreen'
import { VpsInstallScreen } from './VpsInstallScreen'
import { ServerUrlScreen } from './ServerUrlScreen'
import { OidcSignInScreen } from './OidcSignInScreen'
import { ConnectTokenScreen } from './ConnectTokenScreen'
import { SuccessScreen } from './SuccessScreen'
import type { WizardMode } from './ModeSelectScreen'

type Screen =
  | 'mode-select'
  | 'solo-install'
  | 'solo-api-key'
  | 'vps-prereq'
  | 'vps-install'
  | 'server-url'
  | 'oidc-signin'
  | 'connect-token'
  | 'success'

interface SetupWizardPageProps {
  /** Called when the wizard finishes successfully — App.tsx sets wizardNeeded=false. */
  onComplete: () => void
}

// ---------------------------------------------------------------------------
// Screen sequences per mode (excluding mode-select, which is pre-mode)
// ---------------------------------------------------------------------------

const SOLO_SCREENS: Screen[] = ['solo-install', 'solo-api-key', 'success']
const VPS_SCREENS: Screen[] = ['vps-prereq', 'vps-install', 'server-url', 'oidc-signin', 'success']
const CONNECT_SCREENS: Screen[] = ['server-url', 'connect-token', 'success']

function getScreensForMode(mode: WizardMode): Screen[] {
  switch (mode) {
    case 'solo': return SOLO_SCREENS
    case 'vps': return VPS_SCREENS
    case 'connect': return CONNECT_SCREENS
  }
}

/** 0-based index of `screen` within the mode's sequence, or -1 if not found. */
function screenIndex(mode: WizardMode, screen: Screen): number {
  return getScreensForMode(mode).indexOf(screen)
}

/** Next screen in the mode's sequence, or null if already at the last. */
function nextScreen(mode: WizardMode, screen: Screen): Screen | null {
  const seq = getScreensForMode(mode)
  const idx = seq.indexOf(screen)
  if (idx === -1 || idx >= seq.length - 1) return null
  return seq[idx + 1]
}

/** Previous screen in the mode's sequence, or 'mode-select' at the first step. */
function prevScreen(mode: WizardMode, screen: Screen): Screen {
  const seq = getScreensForMode(mode)
  const idx = seq.indexOf(screen)
  if (idx <= 0) return 'mode-select'
  return seq[idx - 1]
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

interface ProgressBarProps {
  /** 0–1 fraction */
  fraction: number
}

function ProgressBar({ fraction }: ProgressBarProps) {
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(fraction * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: 3,
        background: 'var(--border)',
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${fraction * 100}%`,
          background: 'var(--accent)',
          borderRadius: '0 2px 2px 0',
          transition: 'width var(--dur-standard) var(--ease)',
        }}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SetupWizardPage({ onComplete }: SetupWizardPageProps) {
  const [mode, setMode] = useState<WizardMode | null>(null)
  const [currentScreen, setCurrentScreen] = useState<Screen>('mode-select')

  // ── Progress fraction ──────────────────────────────────────────────────────
  // On mode-select, no progress bar is shown. After a mode is chosen, the bar
  // reflects completed-step / total-steps for that mode.

  const progressFraction: number | null = (() => {
    if (mode === null || currentScreen === 'mode-select') return null
    const seq = getScreensForMode(mode)
    const idx = screenIndex(mode, currentScreen)
    if (idx === -1) return null
    // step 0 = just started = 0/N; final step = N-1 = shown as fully filled
    return idx / (seq.length - 1)
  })()

  // ── Navigation helpers ─────────────────────────────────────────────────────

  function handleModeSelect(selectedMode: WizardMode) {
    setMode(selectedMode)
    setCurrentScreen(getScreensForMode(selectedMode)[0])
  }

  function handleNext() {
    if (mode === null) return
    const next = nextScreen(mode, currentScreen)
    if (next !== null) {
      setCurrentScreen(next)
    }
  }

  function handleBack() {
    if (mode === null) return
    const prev = prevScreen(mode, currentScreen)
    setCurrentScreen(prev)
    if (prev === 'mode-select') {
      setMode(null)
    }
  }

  async function handleComplete() {
    try {
      await setWizardCompleted(true)
    } catch {
      // Best-effort — don't block the user if the IPC call fails
    }
    onComplete()
  }

  // ── Back button visibility ─────────────────────────────────────────────────
  // Hidden on mode-select and success screens.
  const showBack =
    currentScreen !== 'mode-select' && currentScreen !== 'success'

  // ── Screen renderer ────────────────────────────────────────────────────────

  function renderScreen() {
    switch (currentScreen) {
      case 'mode-select':
        return <ModeSelectScreen onSelect={handleModeSelect} />

      case 'solo-install':
        return <SoloInstallScreen onNext={handleNext} />

      case 'solo-api-key':
        return <SoloApiKeyScreen onNext={handleNext} />

      case 'vps-prereq':
        return <VpsPrereqScreen onNext={handleNext} />

      case 'vps-install':
        return <VpsInstallScreen onNext={handleNext} />

      case 'server-url':
        return <ServerUrlScreen onNext={handleNext} />

      case 'oidc-signin':
        return <OidcSignInScreen onNext={handleNext} />

      case 'connect-token':
        return <ConnectTokenScreen onNext={handleNext} />

      case 'success':
        return (
          <SuccessScreen
            mode={mode ?? 'solo'}
            onComplete={() => void handleComplete()}
          />
        )
    }
  }

  // ── Layout ─────────────────────────────────────────────────────────────────

  return (
    <div
      className="df df-auth"
      style={{
        position: 'relative',
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--sp-8) var(--sp-4)',
        gap: 'var(--sp-6)',
      }}
    >
      {/* Progress bar — rendered at the very top when a mode is active */}
      {progressFraction !== null && <ProgressBar fraction={progressFraction} />}

      {/* Logo */}
      <LogoMark size={40} animation="breathe pulse" />

      {/* Screen content */}
      <div style={{ width: '100%', maxWidth: 560 }}>
        {renderScreen()}
      </div>

      {/* Back button — hidden on mode-select and success */}
      {showBack && (
        <button
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={handleBack}
          style={{ marginTop: 'var(--sp-2)' }}
        >
          ← Back
        </button>
      )}
    </div>
  )
}
