import { useEffect, useRef, useState } from 'react'
import { machineLines, type MachineLine } from '../machine'
import type { MachineReading } from '../types'

/**
 * The machine behind a task's model, one figure a line (machine.ts): power,
 * battery, DRAM bandwidth, the GPU, tokens per second and the work they take.
 * The first item of the task panel's header row. A hover opens it and leaving
 * closes it; a click (or a tap) opens it until a click outside it, Escape or
 * another click on the button. Not shown when the task has nothing to show.
 */
export function MachineButton({
  model, machine, speed,
}: {
  model: string | null | undefined
  machine: (MachineReading & { ts: number }) | undefined
  speed: { in: number | null; out: number | null }
}) {
  const [open, setOpen] = useState<null | 'hover' | 'click'>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const box = useRef<HTMLSpanElement>(null)

  // While open, the reading's age moves on.
  useEffect(() => {
    if (!open) return
    setNow(Date.now() / 1000)
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(tick)
  }, [open])
  useEffect(() => {
    if (!open) return
    const outside = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(null)
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(null) }
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  const lines: MachineLine[] = machineLines(model, machine, speed, now)
  if (!lines.some((l) => l.text != null)) return null
  return (
    <span
      className="machine"
      ref={box}
      onPointerEnter={(e) => { if (e.pointerType === 'mouse' && !open) setOpen('hover') }}
      onPointerLeave={(e) => { if (e.pointerType === 'mouse' && open === 'hover') setOpen(null) }}
    >
      <button
        type="button"
        className="machine-btn"
        aria-expanded={open != null}
        aria-haspopup="dialog"
        onClick={() => setOpen(open === 'click' ? null : 'click')}
      >
        📊<span className="sr-only">Machine and speed</span>
      </button>
      {open && (
        <div className="machine-pop" role="dialog" aria-label="Machine and speed">
          {lines.map((l) => (
            <div key={l.key} className={`machine-line${l.text == null ? ' missing' : ''}`} data-key={l.key} title={l.label}>
              <span className="machine-emoji" aria-hidden="true">{l.emoji}</span>
              <span className="sr-only">{l.label}: </span>
              <span className="machine-text">{l.text ?? '—'}</span>
            </div>
          ))}
        </div>
      )}
    </span>
  )
}
