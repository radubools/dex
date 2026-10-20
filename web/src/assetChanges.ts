import { useEffect, useState } from 'react'

/**
 * Which files have changed on disk, as the event stream reports them, and a
 * throttled version number per open file.
 *
 * A task rewriting a model or a preview reports every save, often several a
 * second. Reloading a widget on each one rebuilt its frame faster than it
 * could draw. So a viewer refreshes at most once per window: the first
 * change after a quiet window refreshes at once, changes inside a window are
 * merged, and when the window ends the latest version is loaded and the next
 * window starts.
 */

/** The shortest gap between two refreshes of one open file. */
export const REFRESH_WINDOW_MS = 10_000

type Listener = (path: string) => void
const listeners = new Set<Listener>()

/** Called for every `asset` event; paths are relative to dex's assets folder. */
export function notifyAssetChanged(path: string) {
  for (const listener of listeners) listener(path)
}

/** The same file, however each side spelled its path. */
const samePath = (a: string, b: string) => a === b || a.endsWith(`/${b}`) || b.endsWith(`/${a}`)

/**
 * A number that increases when `path` has changed on disk, at most once per
 * `windowMs`. Use it as a dependency to reload what shows the file. Opening
 * the file counts as the first load, so a change in the first window waits
 * for the window to end.
 */
export function useAssetVersion(path: string, windowMs = REFRESH_WINDOW_MS): number {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let lastLoad = Date.now()
    let timer: ReturnType<typeof setTimeout> | null = null
    const fire = () => {
      timer = null
      lastLoad = Date.now()
      setVersion((v) => v + 1)
    }
    const listener: Listener = (changed) => {
      if (!samePath(changed, path) || timer) return // a refresh is already due; it will pick this up
      const wait = lastLoad + windowMs - Date.now()
      if (wait <= 0) fire()
      else timer = setTimeout(fire, wait)
    }
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
      if (timer) clearTimeout(timer)
    }
  }, [path, windowMs])
  return version
}
