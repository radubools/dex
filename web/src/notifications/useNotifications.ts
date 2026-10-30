import { useCallback, useEffect, useRef, useState } from 'react'
import type { DexEvent, Task, ThreadSummary } from '../types'
import { Detector, type Notice } from './detect'
import { HudController, type HudPhase } from './hud'
import { STORAGE, applyEffects, load, markRead as markReadIn, save, unreadCount } from './model'

export type Notifications = {
  /** Newest first. */
  items: Notice[]
  unread: number
  phase: HudPhase
  /** Feed every stream event through this. */
  observe: (event: DexEvent) => void
  /** Task listings, so notices can name the task and tell a root from a child. */
  seedTasks: (tasks: Task[]) => void
  nameThreads: (threads: ThreadSummary[]) => void
  markRead: (id: string) => void
  toggle: () => void
  dismiss: () => void
}

/**
 * The notification system's state: the detector over the stream, the list
 * (kept in localStorage, so read state survives a reload and is shared by
 * this browser's tabs), and the panel's open/fade timer.
 */
export function useNotifications(): Notifications {
  const [items, setItems] = useState<Notice[]>(() => load())
  const current = useRef(items)
  const [phase, setPhase] = useState<HudPhase>('closed')
  const hud = useRef<HudController | null>(null)
  if (!hud.current) hud.current = new HudController(setPhase)
  // Anything stamped before this page opened is the stream's replay.
  const detector = useRef<Detector | null>(null)
  if (!detector.current) detector.current = new Detector(Date.now())

  const commit = useCallback((next: Notice[]) => {
    if (next === current.current) return
    current.current = next
    setItems(next)
    save(next)
  }, [])

  const observe = useCallback(
    (event: DexEvent) => {
      const effects = detector.current!.observe(event)
      if (effects.length === 0) return
      const { items: next, arrived } = applyEffects(current.current, effects)
      commit(next)
      if (arrived > 0) hud.current!.arrive()
    },
    [commit],
  )

  const seedTasks = useCallback((tasks: Task[]) => detector.current!.seedTasks(tasks), [])
  const nameThreads = useCallback((threads: ThreadSummary[]) => detector.current!.nameThreads(threads), [])
  const markRead = useCallback((id: string) => commit(markReadIn(current.current, id)), [commit])
  const toggle = useCallback(() => hud.current!.toggle(), [])
  const dismiss = useCallback(() => hud.current!.dismiss(), [])

  // Another tab read one, or a notice arrived there.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE) return
      current.current = load()
      setItems(current.current)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])
  useEffect(() => () => hud.current?.dispose(), [])

  return { items, unread: unreadCount(items), phase, observe, seedTasks, nameThreads, markRead, toggle, dismiss }
}
