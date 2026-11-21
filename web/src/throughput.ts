/**
 * Input and output tokens per second (the server's throughput.py).
 *
 * Every model call the runner times adds its tokens and the seconds they took
 * to a `cost` event. A rate is always tokens over seconds summed over a span,
 * never an average of rates: one slow call of five tokens says less than a fast
 * one of five thousand.
 */

/** One timed change, as a `cost` event carries it. */
export type SpeedSample = { ts: number; in: number; inS: number; out: number; outS: number }

/** How far back the task panel's figure looks, in seconds. */
export const WINDOW_S = 60

/** Tokens per second, or null with nothing timed. */
export function rate(tokens: number, seconds: number): number | null {
  return seconds > 0 && tokens > 0 ? tokens / seconds : null
}

/** The moving aggregate over the window ending `now` (seconds since the epoch). */
export function windowRates(samples: SpeedSample[] | undefined, now: number, window = WINDOW_S) {
  let tin = 0, sin = 0, tout = 0, sout = 0
  for (const s of samples ?? []) {
    if (s.ts < now - window || s.ts > now + 5) continue
    tin += s.in; sin += s.inS; tout += s.out; sout += s.outS
  }
  return { in: rate(tin, sin), out: rate(tout, sout) }
}

/** Keeps what the window can still use: the newest sample's minute. */
export function trimmed(samples: SpeedSample[], window = WINDOW_S): SpeedSample[] {
  const newest = samples.reduce((a, s) => Math.max(a, s.ts), 0)
  return samples.filter((s) => s.ts >= newest - window)
}

/** '1.2k', '45', '3.4': short enough for a status line. */
export function formatRate(r: number | null): string {
  if (r == null) return '–'
  if (r >= 10_000) return `${Math.round(r / 1000)}k`
  if (r >= 1000) return `${(r / 1000).toFixed(1)}k`
  if (r >= 10) return String(Math.round(r))
  return r.toFixed(1)
}
