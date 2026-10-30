/** How long an arrival keeps the panel open. */
export const HOLD_MS = 5000
/** How long the closing fade runs (matches `.notif-hud.fading` in notifications.css). */
export const FADE_MS = 400

export type HudPhase = 'closed' | 'open' | 'fading'

type Timers = {
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (handle: unknown) => void
}

/**
 * When the notifications panel is open, apart from any UI.
 *
 * - `arrive()`: a notice came in. Open, or stay open, for a full `hold` from
 *   now, then fade and close. A second arrival restarts the window; one during
 *   the fade brings it back.
 * - `toggle()`: the button. Opened by hand, it stays open until dismissed, or
 *   until an arrival gives it a window of its own.
 * - `dismiss()`: an outside click, Escape, or following a notice. Closes at
 *   once, with no fade.
 */
export class HudController {
  phase: HudPhase = 'closed'
  private hold: unknown = null
  private fade: unknown = null

  constructor(
    private onChange: (phase: HudPhase) => void,
    private holdMs = HOLD_MS,
    private fadeMs = FADE_MS,
    private timers: Timers = {
      setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
      clearTimeout: (h) => globalThis.clearTimeout(h as ReturnType<typeof setTimeout>),
    },
  ) {}

  arrive() {
    this.clear()
    this.set('open')
    this.hold = this.timers.setTimeout(() => {
      this.hold = null
      this.set('fading')
      this.fade = this.timers.setTimeout(() => {
        this.fade = null
        this.set('closed')
      }, this.fadeMs)
    }, this.holdMs)
  }

  toggle() {
    if (this.phase === 'open') return this.dismiss()
    this.clear()
    this.set('open')
  }

  dismiss() {
    this.clear()
    this.set('closed')
  }

  /** Stop the timers; for unmounting. */
  dispose() {
    this.clear()
  }

  private clear() {
    if (this.hold !== null) this.timers.clearTimeout(this.hold)
    if (this.fade !== null) this.timers.clearTimeout(this.fade)
    this.hold = this.fade = null
  }

  private set(phase: HudPhase) {
    if (phase === this.phase) return
    this.phase = phase
    this.onChange(phase)
  }
}
