import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { can, getSettings, logout, putSettings, setLimitPaused, setPaused, type Me } from '../api'
import type { SettingsResponse } from '../types'

const money = (n: number) => (n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`)

/** Token counts run to millions; the exact digit has never mattered here. */
const compact = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)}B`
  : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(0)}k`
  : `${n}`

/**
 * The header menu: everything global in one place — auto-approve, the two
 * concurrency limits, the model, and what it has all cost.
 */
export function SettingsMenu({
  autoApprove,
  onAutoApprove,
  onOpenCosts,
  me,
  onOpenUsers,
  onOpenSkills,
  onChangePassword,
}: {
  autoApprove: boolean
  onAutoApprove: (enabled: boolean) => void
  onOpenCosts: () => void
  /** Who is signed in, or null on an install with no sign-in configured. */
  me?: Me | null
  onOpenUsers?: () => void
  /** Which capabilities each project has on. Admin-only. */
  onOpenSkills?: () => void
  /** Offered only to an account that signs in with a password. */
  onChangePassword?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<SettingsResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)

  // Still rendered into document.body via a portal: the header has a
  // backdrop-filter, which creates a stacking context no z-index on a
  // descendant can escape, so an in-place sheet slid under the task panel and
  // the viewer depending on what was open. It no longer needs positioning —
  // it covers the screen — but it does still need to escape that context.

  const refresh = () => getSettings().then(setData).catch(() => {})

  useEffect(() => {
    if (open) refresh()
  }, [open])

  // A full-screen sheet has no outside to click, so Escape and the ✕ are the
  // ways out. Closing on any click would fire on every switch in it.
  useEffect(() => {
    if (!open) return
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [open])

  const patch = (body: Record<string, unknown>) => {
    putSettings(body)
      .then((r) => setData((d) => (d ? { ...d, settings: r.settings, costs: r.costs } : d)))
      .catch(() => {})
  }

  const settings = data?.settings
  const paused = settings?.paused === true
  const parked = data?.paused ?? 0
  const limit = data?.limit ?? {
    paused: false, utilization: null, measured: false, status: null,
    window: null, resetsAt: null, pauseAt: 0.95, overridden: false, probe: null,
  }
  const windowName = limit.window?.replace(/_/g, ' ') ?? 'the limit'
  // Not every plan reports a percentage. Showing an inferred one as though it
  // were measured would be a made-up number; the status is what is actually
  // known, so that is what it says.
  const limitDetail =
    limit.utilization === null
      ? 'Not known yet — dex learns this while a task is running'
      : limit.measured
        ? `${Math.round(limit.utilization * 100)}% of ${windowName} used, holding at ${Math.round(
            limit.pauseAt * 100,
          )}%`
        : limit.status === 'rejected'
          ? `${windowName} is spent`
          : limit.status === 'allowed_warning'
            ? `Claude says you are approaching the ${windowName} limit`
            : `Within the ${windowName} limit · no percentage reported on this plan`
  const projectModelKey = data ? `model:${data.project}` : ''

  const menu = (
    <div className="menu-sheet" role="dialog" aria-modal="true" aria-label="Settings">
      <header className="viewer-bar">
        <span className="viewer-path">Settings</span>
        <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Close settings">
          ✕
        </button>
      </header>
      <div className="menu-sheet-body">
        {/* The rows stay a readable column rather than stretching across a
            wide display; full screen is about room to scroll, not line length. */}
        <div className="menu-sheet-inner">
          {/* First, because it is what you reach for when something is going
              wrong and you want it to stop now. */}
          <div className="menu-row">
            <span>
              {paused ? 'Work is paused' : 'Work is running'}
              <small>
                {paused
                  ? `${parked} task${parked === 1 ? '' : 's'} parked — resume puts them back`
                  : 'Pause parks running tasks and stops new ones starting'}
              </small>
            </span>
            <button
              className={`ghost-btn small ${paused ? 'accent' : ''}`}
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await setPaused(!paused)
                  // Re-read rather than patch: the parked count comes from the
                  // queue, not from the write's response.
                  await refresh()
                } finally {
                  setBusy(false)
                }
              }}
            >
              {busy ? '…' : paused ? 'Resume' : 'Pause'}
            </button>
          </div>

          {/* Directly under the pause it reinforces: two independent reasons
              work can be held, and the operator can see which one is holding
              it. Work runs only when neither is. */}
          <div className="menu-row">
            <span>
              {limit.paused ? 'Held: Claude limit' : 'Claude limit'}
              <small>
                {limitDetail}
                {limit.overridden && ' · overridden by you'}
                {/* Polling costs money, so it is never invisible: a probe is a
                    real call, and this is what the last one came to. */}
                {limit.probe?.costUsd != null &&
                  ` · last check $${limit.probe.costUsd.toFixed(3)}`}
              </small>
            </span>
            <button
              className={`ghost-btn small ${limit.paused ? 'accent' : ''}`}
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await setLimitPaused(!limit.paused)
                  await refresh()
                } finally {
                  setBusy(false)
                }
              }}
            >
              {busy ? '…' : limit.paused ? 'Override' : 'Hold'}
            </button>
          </div>

          <label className="menu-row toggle">
            <span>
              Auto-approve
              <small>Grant every tool approval without asking</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              checked={autoApprove}
              onChange={(e) => onAutoApprove(e.target.checked)}
            />
          </label>

          <label className="menu-row toggle">
            <span>
              Utility proposals
              <small>Let a task ask to share a helper in the project's utils/; runs tasks one at a time</small>
            </span>
            <input
              type="checkbox"
              role="switch"
              // Absent until an operator has ever touched it, and the loop is
              // on by default -- so undefined reads as on, matching the server.
              checked={settings?.utility_proposals ?? true}
              onChange={(e) => patch({ utility_proposals: e.target.checked })}
            />
          </label>

          <div className="menu-row">
            <span>
              Tasks at once
              {/* Turning proposals on drops this to 1, which looks like a bug
                  unless the reason is next to the number. */}
              <small>
                {settings?.utility_proposals === false
                  ? data
                    ? `${data.running} running`
                    : ' '
                  : 'one at a time, so utility proposals land in order'}
              </small>
            </span>
            <Stepper
              value={settings?.task_concurrency ?? 3}
              onChange={(v) => patch({ task_concurrency: v })}
            />
          </div>

          <div className="menu-row">
            <span>
              Chats at once
              <small>{data ? `${data.planning} planning` : ' '}</small>
            </span>
            <Stepper
              value={settings?.chat_concurrency ?? 3}
              onChange={(v) => patch({ chat_concurrency: v })}
            />
          </div>

          <div className="menu-row column">
            <span>
              Model
              <small>Applies to new tasks; running work keeps its model</small>
            </span>
            <select
              value={(settings?.model as string) ?? ''}
              onChange={(e) => patch({ model: e.target.value })}
            >
              <option value="">Default ({data?.defaultModel})</option>
              {data?.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} — {m.note}
                </option>
              ))}
            </select>
          </div>

          <div className="menu-row column">
            <span>
              Thinking effort
              <small>How hard a task thinks before acting; applies to new tasks</small>
            </span>
            <select
              value={(settings?.effort as string) ?? ''}
              onChange={(e) => patch({ effort: e.target.value })}
            >
              <option value="">Default ({data?.defaultEffort})</option>
              {data?.efforts.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.label} — {e.note}
                </option>
              ))}
            </select>
          </div>

          <div className="menu-row">
            <span>
              Animation speed
              <small>Default playback for every animation</small>
            </span>
            <div className="segmented">
              {[0.5, 1, 2, 4].map((s) => (
                <button
                  key={s}
                  className={Number(settings?.animation_speed ?? 1) === s ? 'on' : ''}
                  onClick={() => patch({ animation_speed: s })}
                >
                  {s}×
                </button>
              ))}
            </div>
          </div>

          <div className="menu-row column">
            <span>
              Model for <code>{data?.project}</code>
              <small>Overrides the global choice for this project</small>
            </span>
            <select
              value={(settings?.[projectModelKey] as string) ?? ''}
              onChange={(e) =>
                patch({ project_models: { [data!.project]: e.target.value } })
              }
            >
              <option value="">Use the global model</option>
              {data?.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <div className="menu-costs">
            <div className="menu-row"><span>Spend</span></div>
            <div className="cost-grid">
              {(['day', 'week', 'month', 'all_time'] as const).map((k) => (
                <div key={k} className="cost-cell">
                  <span className="cost-label">{k === 'all_time' ? 'all time' : k}</span>
                  <span className="cost-value">{data ? money(data.costs[k]) : '—'}</span>
                </div>
              ))}
            </div>
            <div className="menu-row"><span>Output tokens</span></div>
            <div className="cost-grid">
              <div className="cost-cell">
                <span className="cost-label">thinking</span>
                <span className="cost-value">{data ? compact(data.tokens.thinking) : '—'}</span>
              </div>
              <div className="cost-cell">
                <span className="cost-label">visible</span>
                <span className="cost-value">{data ? compact(data.tokens.visible) : '—'}</span>
              </div>
              <div className="cost-cell">
                <span className="cost-label">share</span>
                <span className="cost-value">
                  {data && data.tokens.output > 0
                    ? `${Math.round((data.tokens.thinking / data.tokens.output) * 100)}%`
                    : '—'}
                </span>
              </div>
            </div>
            {/* Tokens are recorded from a task's result, so runs that finished
                before that existed have a cost and no counts. Saying so beats a
                share that looks wrong. */}
            {data && data.tokens.counted < data.tokens.with_cost && (
              <div className="muted small">
                from {data.tokens.counted} of {data.tokens.with_cost} priced tasks
              </div>
            )}
            {/* Identity, at the bottom with the other operator-level things.
                Absent entirely when sign-in is not configured, rather than
                showing an empty account row. */}
            {me?.user && (
              <>
                <div className="menu-row">
                  <span>
                    Signed in
                    <small>
                      {/* The identifier they signed in with, and their actual
                          role. This said "admin" or "user" for everyone, which
                          with four roles would have described two of them
                          wrongly. */}
                      {me.user.username ? `@${me.user.username}` : me.user.email} ·{' '}
                      {me.user.role ?? 'no role'}
                    </small>
                  </span>
                  <button
                    className="ghost-btn small"
                    onClick={() => void logout().then(() => location.reload())}
                  >
                    Sign out
                  </button>
                </div>
                {can(me.user, 'manage_users') && onOpenUsers && (
                  <button
                    className="ghost-btn small"
                    onClick={() => {
                      setOpen(false)
                      onOpenUsers()
                    }}
                  >
                    Users and access
                  </button>
                )}
                {can(me.user, 'manage_users') && onOpenSkills && (
                  <button
                    className="ghost-btn small"
                    onClick={() => {
                      setOpen(false)
                      onOpenSkills()
                    }}
                  >
                    Skills
                  </button>
                )}
                {onChangePassword && me.user.username && (
                  <button
                    className="ghost-btn small"
                    onClick={() => {
                      setOpen(false)
                      onChangePassword()
                    }}
                  >
                    Change my password
                  </button>
                )}
              </>
            )}
            <button
              className="ghost-btn small"
              onClick={() => {
                setOpen(false)
                onOpenCosts()
              }}
            >
              Open cost dashboard
            </button>
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <div className="menu-anchor">
      <button
        ref={trigger}
        className={`menu-trigger icon-only ${autoApprove ? 'warn' : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        title={autoApprove ? 'Settings — auto-approve is on' : 'Settings'}
        aria-label={autoApprove ? 'Settings — auto-approve is on' : 'Settings'}
      >
        {/* The dot keeps carrying auto-approve state: with the word gone it is
            the only thing that shows the setting is on. */}
        <span className={`switch-dot ${autoApprove ? 'on' : ''}`} />
        <span aria-hidden="true">⚙</span>
      </button>
      {open && createPortal(menu, document.body)}
    </div>
  )
}

function Stepper({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="stepper">
      <button onClick={() => onChange(Math.max(1, value - 1))} disabled={value <= 1} aria-label="Fewer">
        −
      </button>
      <span>{value}</span>
      <button onClick={() => onChange(Math.min(16, value + 1))} disabled={value >= 16} aria-label="More">
        +
      </button>
    </div>
  )
}
