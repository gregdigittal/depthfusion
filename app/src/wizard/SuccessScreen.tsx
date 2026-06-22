/**
 * SuccessScreen — final screen of the setup wizard (E-65 / S-217).
 *
 * Displays a checkmark, a mode-specific summary line, and a
 * "Go to Dashboard" button that calls `onComplete` to exit the wizard.
 */

import { CheckCircle2 } from 'lucide-react'
import type { WizardMode } from './ModeSelectScreen'

interface SuccessScreenProps {
  mode: WizardMode
  /** Called when the user clicks "Go to Dashboard". */
  onComplete: () => void
}

const MODE_SUMMARY: Record<WizardMode, string> = {
  solo: 'DepthFusion is running locally on your Mac with your Anthropic API key.',
  vps: 'DepthFusion is installed on your VPS and you are signed in.',
  connect: 'DepthFusion is connected to your server and you are signed in.',
}

export function SuccessScreen({ mode, onComplete }: SuccessScreenProps) {
  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 440,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 'var(--sp-6)',
        textAlign: 'center',
      }}
    >
      {/* Checkmark icon */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 72,
          height: 72,
          borderRadius: '50%',
          background: 'var(--accent-wash)',
        }}
      >
        <CheckCircle2 size={40} style={{ color: 'var(--accent)' }} strokeWidth={1.5} />
      </div>

      {/* Heading + summary */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
        <h2
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 'var(--display-weight)',
            fontSize: 'var(--fs-h2)',
            color: 'var(--text)',
            margin: 0,
          }}
        >
          You're all set!
        </h2>
        <p
          style={{
            fontSize: 'var(--fs-body)',
            color: 'var(--muted)',
            margin: 0,
            lineHeight: 1.6,
          }}
        >
          {MODE_SUMMARY[mode]}
        </p>
      </div>

      <button
        className="df-btn df-btn--primary"
        onClick={onComplete}
        style={{ width: '100%' }}
      >
        Go to Dashboard
      </button>
    </div>
  )
}
