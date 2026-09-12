import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Project } from '../types'

/**
 * Switches which project the whole app is looking at — its threads, its feed,
 * its guide. Portalled for the same reason the settings menu is: the header
 * has a backdrop-filter, and a descendant cannot escape that stacking context.
 */
export function ProjectPicker({
  projects,
  current,
  onSelect,
  onCreate,
}: {
  projects: Project[]
  current: string | null
  onSelect: (slug: string) => void
  onCreate: (name: string) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [at, setAt] = useState<{ top: number; left: number } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const place = () => {
      const box = trigger.current?.getBoundingClientRect()
      if (box) setAt({ top: box.bottom + 8, left: box.left })
    }
    place()
    const away = (e: MouseEvent) => {
      const node = e.target as Node
      if (!panel.current?.contains(node) && !trigger.current?.contains(node)) setOpen(false)
    }
    const key = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('resize', place)
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('resize', place)
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  const selected = projects.find((p) => p.slug === current)

  const create = async () => {
    const trimmed = name.trim()
    if (!trimmed || busy) return
    setBusy(true)
    try {
      await onCreate(trimmed)
      setName('')
      setOpen(false)
    } finally {
      setBusy(false)
    }
  }

  const menu = (
    <div
      className="menu-panel project-panel"
      role="dialog"
      aria-label="Projects"
      ref={panel}
      style={at ? { top: at.top, left: at.left, right: 'auto' } : undefined}
    >
      <div className="menu-row"><span className="card-kind">Projects</span></div>
      <div className="project-list">
        {projects.map((project) => (
          <button
            key={project.slug}
            className={`project-row ${project.slug === current ? 'on' : ''}`}
            onClick={() => {
              onSelect(project.slug)
              setOpen(false)
            }}
          >
            <span className="project-name">{project.name}</span>
            <span className="project-meta">
              {project.threads} threads · {project.tasks} tasks
            </span>
          </button>
        ))}
      </div>
      <div className="freeform new-project">
        <input
          value={name}
          placeholder="New project…"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void create()}
        />
        <button disabled={!name.trim() || busy} onClick={() => void create()}>Add</button>
      </div>
    </div>
  )

  return (
    <div className="menu-anchor">
      <button
        ref={trigger}
        className="menu-trigger project-trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        title="Switch project"
      >
        <span className="project-current">{selected?.name ?? '…'}</span>
        <span aria-hidden="true">▾</span>
      </button>
      {open && createPortal(menu, document.body)}
    </div>
  )
}
