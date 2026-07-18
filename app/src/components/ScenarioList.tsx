import { type CognitiveScenario } from '../lib/ipc'

interface ScenarioListProps {
  scenarios: CognitiveScenario[]
  loading: boolean
  error: string | null
}

export function ScenarioList({ scenarios, loading, error }: ScenarioListProps) {
  if (loading) {
    return <p className="df-cognitive__empty">Loading scenarios…</p>
  }
  if (error) {
    return <p className="df-cognitive__error">Error: {error}</p>
  }
  if (scenarios.length === 0) {
    return <p className="df-cognitive__empty">No scenarios yet. Scenarios are generated after memories are clustered.</p>
  }

  return (
    <ul className="df-scenario-list">
      {scenarios.map((s, i) => (
        <li key={`${s.project_id}-${i}`} className="df-scenario-list__item">
          <span className="df-scenario-list__title">{s.title}</span>
          {s.project_id && (
            <span className="df-scenario-list__project">{s.project_id}</span>
          )}
          {s.summary && (
            <p className="df-scenario-list__summary">{s.summary}</p>
          )}
        </li>
      ))}
    </ul>
  )
}
