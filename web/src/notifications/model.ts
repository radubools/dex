import type { Effect, Notice } from './detect'

/** How many notices are kept; the oldest go first. */
export const KEEP = 100

/**
 * The list after `effects`, newest first, and whether anything new arrived.
 * A notice whose key is already listed is not added again, so a replayed or
 * repeated event cannot announce the same thing twice.
 */
export function applyEffects(items: Notice[], effects: Effect[]): { items: Notice[]; arrived: number } {
  let next = items
  let arrived = 0
  for (const effect of effects) {
    if (effect.type === 'add') {
      if (next.some((n) => n.key === effect.notice.key)) continue
      next = [effect.notice, ...next]
      arrived += 1
    } else {
      if (!next.some((n) => n.key === effect.key && !n.resolved)) continue
      next = next.map((n) => (n.key === effect.key ? { ...n, resolved: true } : n))
    }
  }
  if (next.length > KEEP) next = newestFirst(next).slice(0, KEEP)
  return { items: next === items ? items : newestFirst(next), arrived }
}

/** Newest first by creation time; the order the panel lists them in. */
export function newestFirst(items: Notice[]): Notice[] {
  return [...items].sort((a, b) => b.createdAt - a.createdAt)
}

export function markRead(items: Notice[], id: string): Notice[] {
  return items.map((n) => (n.id === id && !n.read ? { ...n, read: true } : n))
}

export const unreadCount = (items: Notice[]) => items.filter((n) => !n.read).length

/** The localStorage key the list is kept under. */
export const STORAGE = 'dex.notifications'

/** What this browser kept from before, or nothing. */
export function load(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): Notice[] {
  try {
    const raw = storage?.getItem(STORAGE)
    const parsed = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(parsed) ? newestFirst(parsed.filter(isNotice)) : []
  } catch {
    return []
  }
}

export function save(items: Notice[], storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage) {
  try {
    storage?.setItem(STORAGE, JSON.stringify(items))
  } catch {
    // Full or blocked storage loses the list on reload, nothing more.
  }
}

function isNotice(value: unknown): value is Notice {
  const n = value as Notice
  return Boolean(n) && typeof n.id === 'string' && typeof n.key === 'string' && typeof n.createdAt === 'number'
}

/** "just now", "4 min ago", "3 h ago", then the date. */
export function ago(ms: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ms) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} h ago`
  const d = Math.round(h / 24)
  return d < 7 ? `${d} d ago` : new Date(ms).toLocaleDateString()
}
