import { useEffect, useState } from 'react'
import type { ModelChoice } from '../types'

const PROVIDERS: { id: NonNullable<ModelChoice['provider']>; label: string }[] = [
  { id: 'claude', label: 'Claude' },
  { id: 'antigravity', label: 'Gemini' },
  { id: 'opencode', label: 'OpenCode' },
]

/**
 * Two pickers: whose subscription runs the work (Claude, Gemini through
 * Antigravity, OpenCode Go), then one of that provider's models. Switching
 * provider picks its first model that can run here. A model that cannot run on
 * this server (its CLI not installed or not signed in) is listed, disabled,
 * with what to do.
 *
 * The global picker offers `unset` (the deployment default) under every
 * provider. A project's or a skill's picker passes `inherit` (the model it
 * falls back to) instead: its first provider is "Global", which saves no
 * override, so only a deliberate provider choice ever overrides it.
 */
export function ModelPicker({ label, models, value, onChange, unset, inherit, inheritText, withNote = false }: {
  label: string
  models?: ModelChoice[]
  value: string
  onChange: (model: string) => void
  unset?: string
  inherit?: string
  /** What "Global" falls back to, in words, when it is more than the global model. */
  inheritText?: string
  withNote?: boolean
}) {
  const GLOBAL = 'global'
  const providerOf = (id: string) => (models ?? []).find((m) => m.id === id)?.provider ?? 'claude'
  const offered = PROVIDERS.filter((p) => (models ?? []).some((m) => (m.provider ?? 'claude') === p.id))
  // Unset says nothing about a provider: the one last looked at stays shown.
  const [chosen, setChosen] = useState<string>(() => providerOf(value))
  const provider = value ? providerOf(value) : inherit !== undefined ? GLOBAL : chosen
  useEffect(() => {
    if (value) setChosen(providerOf(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, models])
  const group = (models ?? []).filter((m) => (m.provider ?? 'claude') === provider)
  return (
    <div className="model-picker">
      <select
        aria-label={`${label}: provider`}
        value={provider}
        onChange={(e) => {
          if (e.target.value === GLOBAL) return onChange('')
          setChosen(e.target.value)
          const first = (models ?? []).find(
            (m) => (m.provider ?? 'claude') === e.target.value && m.available !== false,
          )
          if (first) onChange(first.id)
        }}
      >
        {inherit !== undefined && <option value={GLOBAL}>Global</option>}
        {offered.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
      {provider === GLOBAL ? (
        <select aria-label={`${label}: model`} value="" disabled>
          <option value="">{inheritText ?? `Uses the global model${inherit ? ` (${inherit})` : ''}`}</option>
        </select>
      ) : (
        <select aria-label={`${label}: model`} value={value} onChange={(e) => onChange(e.target.value)}>
          {unset !== undefined && <option value="">{unset}</option>}
          {group.map((m) => (
            <option key={m.id} value={m.id} disabled={m.available === false}>
              {m.label}
              {m.available === false ? ` — ${m.unavailable}` : withNote ? ` — ${m.note}` : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
