import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties, RefObject } from 'react'
import { createPortal } from 'react-dom'
import type { Notice, Reason } from './detect'
import { ago } from './model'
import type { Notifications } from './useNotifications'
import './notifications.css'

const REASONS: Record<Reason, { icon: string; label: string }> = {
  question: { icon: '❓', label: 'Asks a question' },
  plan: { icon: '📋', label: 'Plan awaits approval' },
  approval: { icon: '🔐', label: 'Needs approval' },
  finished: { icon: '✅', label: 'Finished' },
}

const reasonOf = (n: Notice) =>
  n.reason === 'finished' && n.failed ? { icon: '⚠️', label: 'Finished with failures' } : REASONS[n.reason]

/**
 * The header's notifications button, and the panel it opens beside itself.
 * Opening a notice marks it read, closes the panel and hands it to `onOpen`,
 * which takes the reader to its task or thread.
 */
export function NotificationsButton({
  notices,
  onOpen,
}: {
  notices: Notifications
  onOpen: (notice: Notice) => void
}) {
  const trigger = useRef<HTMLButtonElement>(null)
  const { unread, phase } = notices
  const label = unread ? `Notifications — ${unread} unread` : 'Notifications'
  return (
    <div className="notif-anchor">
      <button
        ref={trigger}
        className={`menu-trigger icon-only notif-trigger ${unread ? 'has-unread' : ''}`}
        aria-expanded={phase !== 'closed'}
        aria-haspopup="dialog"
        onClick={notices.toggle}
        title={label}
        aria-label={label}
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 && (
          <span className="notif-badge" aria-hidden="true">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {/* Into document.body, as the settings sheet is: the header's
          backdrop-filter is a stacking context nothing inside it escapes. */}
      {phase !== 'closed' &&
        createPortal(<Hud notices={notices} trigger={trigger} onOpen={onOpen} />, document.body)}
    </div>
  )
}

function Hud({
  notices,
  trigger,
  onOpen,
}: {
  notices: Notifications
  trigger: RefObject<HTMLButtonElement | null>
  onOpen: (notice: Notice) => void
}) {
  const panel = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<CSSProperties>({ visibility: 'hidden' })
  const [now, setNow] = useState(Date.now())
  const { dismiss } = notices

  // Hung under the button, right edges aligned, and kept there on resize.
  useLayoutEffect(() => {
    const measure = () => {
      const rect = trigger.current?.getBoundingClientRect()
      if (!rect) return
      setPlace({ top: rect.bottom + 6, right: Math.max(8, window.innerWidth - rect.right) })
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [trigger])

  // A click anywhere else closes it at once. The button is not "elsewhere":
  // it toggles, and closing here first would have it reopen straight away.
  useEffect(() => {
    const outside = (e: PointerEvent) => {
      const target = e.target as Node
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return
      dismiss()
    }
    const key = (e: KeyboardEvent) => e.key === 'Escape' && dismiss()
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      document.removeEventListener('keydown', key)
    }
  }, [dismiss, trigger])

  // Relative times stay true while it is open.
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(tick)
  }, [])

  return (
    <div
      ref={panel}
      className={`notif-hud ${notices.phase === 'fading' ? 'fading' : ''}`}
      style={place}
      role="dialog"
      aria-label="Notifications"
    >
      <header className="notif-head">
        <span>Notifications</span>
        <span className="muted small">{notices.unread ? `${notices.unread} unread` : 'all read'}</span>
      </header>
      {notices.items.length === 0 ? (
        <p className="notif-empty muted small">Nothing needs you right now.</p>
      ) : (
        <ul className="notif-list">
          {notices.items.map((n) => {
            const reason = reasonOf(n)
            return (
              <li key={n.id}>
                <button
                  className={`notif-item ${n.read ? 'read' : 'unread'} ${n.failed ? 'failed' : ''}`}
                  data-notice={n.id}
                  data-read={n.read ? 'true' : 'false'}
                  onClick={() => {
                    // Read now, at the click, and not when it was merely seen.
                    notices.markRead(n.id)
                    dismiss()
                    onOpen(n)
                  }}
                >
                  <span className="notif-icon" aria-hidden="true">{reason.icon}</span>
                  <span className="notif-body">
                    <span className="notif-line">
                      <span className="notif-reason">{reason.label}</span>
                      <span className="notif-time">{ago(n.createdAt, now)}</span>
                    </span>
                    <span className="notif-title">{n.title}</span>
                    <span className="notif-detail">{n.detail}</span>
                    {n.resolved && n.reason !== 'finished' && <span className="notif-handled">handled</span>}
                  </span>
                  {!n.read && <span className="notif-dot" aria-label="unread" />}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
