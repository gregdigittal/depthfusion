import { useState } from 'react'
import { useCognitiveStatus } from '../hooks/useCognitiveStatus'
import { useCognitiveScenarios } from '../hooks/useCognitiveScenarios'
import { ScenarioList } from '../components/ScenarioList'
import { RefsBrowser } from '../components/RefsBrowser'

type CognitiveTab = 'persona' | 'scenarios' | 'refs'

const TABS: Array<{ id: CognitiveTab; label: string }> = [
  { id: 'persona', label: 'Persona' },
  { id: 'scenarios', label: 'Scenarios' },
  { id: 'refs', label: 'Refs' },
]

export function CognitivePage() {
  const [activeTab, setActiveTab] = useState<CognitiveTab>('persona')
  const { data: status, loading: statusLoading, error: statusError } = useCognitiveStatus()
  const { data: scenarios, loading: scenariosLoading, error: scenariosError } = useCognitiveScenarios()

  return (
    <div className="df-cognitive">
      <header className="df-cognitive__header">
        <h2 className="df-cognitive__title">Memory &amp; Cognition</h2>
      </header>

      <nav className="df-cognitive__tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`df-cognitive__tab${activeTab === tab.id ? ' df-cognitive__tab--active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="df-cognitive__panel" role="tabpanel">
        {activeTab === 'persona' && (
          <PersonaTab loading={statusLoading} error={statusError} data={status} />
        )}
        {activeTab === 'scenarios' && (
          <ScenarioList
            scenarios={scenarios?.scenarios ?? []}
            loading={scenariosLoading}
            error={scenariosError}
          />
        )}
        {activeTab === 'refs' && (
          <RefsBrowser refsCount={status?.offload.refs_count ?? 0} />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Persona sub-panel
// ---------------------------------------------------------------------------

interface PersonaTabProps {
  loading: boolean
  error: string | null
  data: ReturnType<typeof useCognitiveStatus>['data']
}

function PersonaTab({ loading, error, data }: PersonaTabProps) {
  if (loading) return <p className="df-cognitive__empty">Loading status…</p>
  if (error) return <p className="df-cognitive__error">Error: {error}</p>
  if (!data) return <p className="df-cognitive__empty">No status available.</p>

  const { persona, offload, distillation } = data

  return (
    <dl className="df-cognitive__dl">
      <dt>Last persona update</dt>
      <dd>{persona.persona_last_updated ?? '—'}</dd>

      <dt>Memories at generation</dt>
      <dd>{persona.memory_count_at_last_generation ?? '—'}</dd>

      <dt>Distillation backend</dt>
      <dd>{distillation.resolved_backend}</dd>

      <dt>Offloaded refs</dt>
      <dd>{offload.refs_count}</dd>

      <dt>Offloading</dt>
      <dd>{offload.offload_enabled ? 'enabled' : 'disabled'}</dd>
    </dl>
  )
}
