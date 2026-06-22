/**
 * ModeSelectScreen — first screen of the setup wizard (E-65 / S-214).
 *
 * Presents three mode cards: Solo, VPS, and Connect. Selecting a card
 * immediately advances to the first step of that mode's flow by calling
 * `onSelect` with the chosen mode string.
 */

import { Monitor, Server, Plug } from 'lucide-react'

export type WizardMode = 'solo' | 'vps' | 'connect'

interface ModeSelectScreenProps {
  onSelect: (mode: WizardMode) => void
}

interface ModeCard {
  mode: WizardMode
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number }>
  title: string
  subtitle: string
}

const CARDS: ModeCard[] = [
  {
    mode: 'solo',
    Icon: Monitor,
    title: 'Solo',
    subtitle: 'Run entirely on your Mac. Uses MLX for AI and your own Anthropic API key.',
  },
  {
    mode: 'vps',
    Icon: Server,
    title: 'Self-hosted VPS',
    subtitle: 'Install on a Linux server you control. CPU or GPU — auto-detected.',
  },
  {
    mode: 'connect',
    Icon: Plug,
    title: 'Connect to server',
    subtitle: 'Your server is already running. Enter the URL and sign in.',
  },
]

export function ModeSelectScreen({ onSelect }: ModeSelectScreenProps) {
  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 480,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sp-4)',
      }}
    >
      <div style={{ textAlign: 'center', marginBottom: 'var(--sp-2)' }}>
        <h2
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 'var(--display-weight)',
            fontSize: 'var(--fs-h2)',
            color: 'var(--text)',
            margin: 0,
          }}
        >
          How will you use DepthFusion?
        </h2>
        <p
          style={{
            fontSize: 'var(--fs-body)',
            color: 'var(--muted)',
            marginTop: 'var(--sp-2)',
            marginBottom: 0,
          }}
        >
          Choose a setup mode to get started.
        </p>
      </div>

      {CARDS.map(({ mode, Icon, title, subtitle }) => (
        <button
          key={mode}
          onClick={() => onSelect(mode)}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 'var(--sp-4)',
            padding: 'var(--sp-5)',
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--r-xl)',
            cursor: 'pointer',
            textAlign: 'left',
            width: '100%',
            transition:
              'background var(--dur-quick) var(--ease), border-color var(--dur-quick) var(--ease), box-shadow var(--dur-quick) var(--ease)',
          }}
          onMouseEnter={(e) => {
            const el = e.currentTarget
            el.style.background = 'var(--surface-3)'
            el.style.borderColor = 'var(--accent-soft)'
            el.style.boxShadow = '0 0 0 1px var(--accent-soft)'
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget
            el.style.background = 'var(--surface-2)'
            el.style.borderColor = 'var(--border)'
            el.style.boxShadow = 'none'
          }}
        >
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 40,
              height: 40,
              borderRadius: 'var(--r-lg)',
              background: 'var(--accent-wash)',
              color: 'var(--accent)',
              flexShrink: 0,
              marginTop: 2,
            }}
          >
            <Icon size={20} strokeWidth={1.5} />
          </span>

          <div style={{ minWidth: 0 }}>
            <p
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 'var(--fw-medium)',
                fontSize: 'var(--fs-title)',
                color: 'var(--text)',
                margin: 0,
              }}
            >
              {title}
            </p>
            <p
              style={{
                fontSize: 'var(--fs-body)',
                color: 'var(--muted)',
                margin: 'var(--sp-1) 0 0',
                lineHeight: 1.5,
              }}
            >
              {subtitle}
            </p>
          </div>
        </button>
      ))}
    </div>
  )
}
