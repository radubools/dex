/**
 * The dashboard's time axis. Buckets arrive as the server's wall clock, offset
 * and all ("2026-10-30T00:00:00-07:00"), so they are read as that wall clock:
 * a day means the server's day wherever the page is opened. Hours and days
 * nothing was spent in have no row, and the axis has to show them anyway.
 */
export type Granularity = 'hour' | 'day' | 'week' | 'month'

const HOUR = 3_600_000
const DAY = 24 * HOUR
const SPANS: Record<string, number> = { day: DAY, week: 7 * DAY, month: 30 * DAY }
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function offsetOf(iso: string): number {
  const m = iso.match(/([+-])(\d{2}):?(\d{2})?$/)
  return m ? (m[1] === '-' ? -1 : 1) * (Number(m[2]) * HOUR + Number(m[3] ?? 0) * 60_000) : 0
}

/** Wall-clock time as milliseconds, kept in UTC fields so it never shifts. */
const wall = (iso: string) => Date.parse(iso.slice(0, 19) + 'Z')

function floor(ms: number, g: Granularity): number {
  const d = new Date(ms)
  d.setUTCMinutes(0, 0, 0)
  if (g === 'hour') return d.getTime()
  d.setUTCHours(0)
  // Postgres weeks start on Monday.
  if (g === 'week') d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7))
  if (g === 'month') d.setUTCDate(1)
  return d.getTime()
}

function next(ms: number, g: Granularity): number {
  if (g === 'month') {
    const d = new Date(ms)
    d.setUTCMonth(d.getUTCMonth() + 1)
    return d.getTime()
  }
  return ms + (g === 'hour' ? HOUR : g === 'day' ? DAY : 7 * DAY)
}

export const bucketKey = (iso: string, g: Granularity) =>
  new Date(floor(wall(iso), g)).toISOString().slice(0, 13)

/** One page of a period, `offset` pages back: where it starts, where it ends. */
function pageOf(anchor: string, period: string, g: Granularity, offset: number, now: number) {
  const end = next(floor(now + offsetOf(anchor), g), g) - offset * SPANS[period]
  return { start: floor(end - SPANS[period], g), end }
}

/**
 * Every bucket of a period, oldest first, empty ones included. `anchor` is any
 * time from the server, for its offset: a page with no spend still needs one.
 */
export function timeline(
  isos: string[], period: string, g: Granularity, anchor?: string | null, offset = 0, now = Date.now(),
): string[] {
  const from = anchor ?? isos[0]
  if (!from) return []
  const page = period in SPANS
    ? pageOf(from, period, g, offset, now)
    : { start: Math.min(...isos.map((iso) => floor(wall(iso), g)), Infinity), end: next(floor(now + offsetOf(from), g), g) }
  const keys: string[] = []
  for (let at = page.start; at < page.end; at = next(at, g)) keys.push(new Date(at).toISOString().slice(0, 13))
  return keys
}

/** Whether a page further back would hold anything. */
export function hasOlder(earliest: string | null | undefined, period: string, g: Granularity, offset: number): boolean {
  if (!earliest || !(period in SPANS)) return false
  return wall(earliest) < pageOf(earliest, period, g, offset, Date.now()).start
}

export function bucketLabel(key: string, g: Granularity): string {
  const d = new Date(`${key}:00:00Z`)
  if (g === 'hour') return `${String(d.getUTCHours()).padStart(2, '0')}:00`
  if (g === 'month') return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
}

/** The long form, for a bar's tooltip. */
export function bucketTitle(key: string, g: Granularity): string {
  const d = new Date(`${key}:00:00Z`)
  const date = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`
  return g === 'hour' ? `${date}, ${bucketLabel(key, g)}`
    : g === 'week' ? `Week of ${date}`
    : g === 'month' ? bucketLabel(key, g)
    : date
}
