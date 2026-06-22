/**
 * VpsPrereqScreen — step 1 of the VPS setup flow (E-65 / S-216).
 *
 * Displays a static checklist of prerequisites needed before installing
 * DepthFusion on a self-hosted VPS. The Next button advances unconditionally
 * since these are informational checks only.
 */

import { CheckCircle } from 'lucide-react'

interface VpsPrereqScreenProps {
  onNext: () => void
}

interface PrereqItem {
  title: string
  description: string
}

const PREREQS: PrereqItem[] = [
  {
    title: 'Ubuntu 22.04 LTS or later',
    description: 'The install script targets Ubuntu 22.04+. Debian 12 also works.',
  },
  {
    title: 'SSH access',
    description: 'You must be able to SSH into the server as a user with sudo rights.',
  },
  {
    title: 'Outbound internet access',
    description:
      'The server needs outbound HTTPS (port 443) to pull packages and the DepthFusion image.',
  },
]

export function VpsPrereqScreen({ onNext }: VpsPrereqScreenProps) {
  return (
    <div
      className="df-emerge"
      style={{
        width: '100%',
        maxWidth: 520,
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sp-6)',
      }}
    >
      <div>
        <h2
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 'var(--display-weight)',
            fontSize: 'var(--fs-h2)',
            color: 'var(--text)',
            margin: 0,
          }}
        >
          Before you install
        </h2>
        <p
          style={{
            fontSize: 'var(--fs-body)',
            color: 'var(--muted)',
            marginTop: 'var(--sp-2)',
            marginBottom: 0,
            lineHeight: 1.5,
          }}
        >
          Make sure your server meets these requirements.
        </p>
      </div>

      {/* Checklist */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sp-3)',
        }}
      >
        {PREREQS.map((item) => (
          <div
            key={item.title}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 'var(--sp-3)',
              padding: 'var(--sp-4)',
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--r-lg)',
            }}
          >
            <CheckCircle
              size={18}
              style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 2 }}
            />
            <div style={{ minWidth: 0 }}>
              <p
                style={{
                  fontWeight: 'var(--fw-medium)',
                  fontSize: 'var(--fs-body)',
                  color: 'var(--text)',
                  margin: 0,
                }}
              >
                {item.title}
              </p>
              <p
                style={{
                  fontSize: 'var(--fs-label)',
                  color: 'var(--muted)',
                  margin: 'var(--sp-1) 0 0',
                  lineHeight: 1.4,
                }}
              >
                {item.description}
              </p>
            </div>
          </div>
        ))}
      </div>

      <button
        className="df-btn df-btn--primary"
        onClick={onNext}
        style={{ width: '100%' }}
      >
        My server is ready — continue
      </button>
    </div>
  )
}
