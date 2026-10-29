// Draggable dividers between panes. A split remembers its size in this
// browser's localStorage (`dex.layout.<key>`), never on the server: each
// person's layout is theirs, per device. A size is always clamped to what the
// window allows now, so a width saved on a big monitor cannot push a pane off
// a laptop screen.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { HTMLAttributes, KeyboardEvent, PointerEvent } from 'react'

const PREFIX = 'dex.layout.'
const STEP = 16
const BIG_STEP = 64

export function readSize(key: string): number | null {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    const n = raw === null ? NaN : Number(raw)
    return Number.isFinite(n) && n > 0 ? n : null
  } catch {
    return null
  }
}

export function writeSize(key: string, size: number | null) {
  try {
    if (size === null) localStorage.removeItem(PREFIX + key)
    else localStorage.setItem(PREFIX + key, String(Math.round(size)))
  } catch {
    // Private mode or storage full: the size lasts until reload instead.
  }
}

export const clamp = (n: number, min: number, max: number) => Math.min(Math.max(n, min), Math.max(min, max))

export interface SplitOptions {
  /** Storage name: `dex.layout.<key>`. */
  key: string
  /** The size before anyone drags, and after a double-click. */
  defaultSize: number
  min: number
  /** Recomputed by the caller each render from the window and the other panes. */
  max: number
  /** The pane sits after the divider (a right-hand column): dragging right shrinks it. */
  after?: boolean
  label: string
}

/**
 * One divider: the pane's current size, and the props for its handle
 * (`role="separator"`, pointer drag with capture so mouse and touch both work,
 * arrow keys, double-click to reset).
 */
export function useSplit({ key, defaultSize, min, max, after = false, label }: SplitOptions) {
  const [stored, setStored] = useState<number | null>(() => readSize(key))
  const size = clamp(stored ?? defaultSize, min, max)
  const drag = useRef<{ x: number; size: number } | null>(null)
  // The latest size, for the pointerup that saves it.
  const live = useRef(size)
  live.current = size
  const sign = after ? -1 : 1

  const set = useCallback(
    (next: number | null, save: boolean) => {
      const value = next === null ? null : clamp(next, min, max)
      setStored(value)
      if (save) writeSize(key, value)
    },
    [key, min, max],
  )

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, size }
    document.body.classList.add('resizing')
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    set(drag.current.size + sign * (e.clientX - drag.current.x), false)
  }
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    drag.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    document.body.classList.remove('resizing')
    writeSize(key, live.current)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? BIG_STEP : STEP
    let next: number | null
    if (e.key === 'ArrowLeft') next = size - sign * step
    else if (e.key === 'ArrowRight') next = size + sign * step
    else if (e.key === 'Home') next = min
    else if (e.key === 'End') next = max
    else if (e.key === 'Enter') next = null
    else return
    e.preventDefault()
    set(next, true)
  }

  // A drag cut short (the window lost focus mid-drag) must not leave the
  // page stuck with the resize cursor and no text selection.
  useEffect(() => () => document.body.classList.remove('resizing'), [])

  const handle: HTMLAttributes<HTMLDivElement> = {
    role: 'separator',
    tabIndex: 0,
    'aria-orientation': 'vertical',
    'aria-label': label,
    'aria-valuenow': Math.round(size),
    'aria-valuemin': Math.round(min),
    'aria-valuemax': Math.round(Math.max(min, max)),
    title: `${label} — drag to resize, double-click to reset`,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel: onPointerUp,
    onLostPointerCapture: onPointerUp,
    onKeyDown,
    onDoubleClick: () => set(null, true),
  }
  return { size, handle }
}

/** The window's width, and whether it is wide enough for side-by-side panes. */
export function useViewport(wideFrom: number) {
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return { width, wide: width >= wideFrom }
}
