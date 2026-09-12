import { Suspense, lazy, useEffect, useState } from 'react'
import { getGuide, putGuide } from '../api'

const Markdown = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))

/**
 * The project's AGENTS.md — the standing brief every task in it reads. Shown
 * rendered, edited as markdown, saved to the file the runner loads per run.
 */
export function GuideEditor({ project, onClose }: { project: string; onClose: () => void }) {
  const [text, setText] = useState('')
  const [saved, setSaved] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [path, setPath] = useState('')

  useEffect(() => {
    let live = true
    getGuide(project)
      .then((g) => {
        if (!live) return
        setText(g.text)
        setSaved(g.text)
        setPath(g.path)
      })
      .catch((err: Error) => live && setError(err.message))
    return () => { live = false }
  }, [project])

  const dirty = text !== saved

  const save = () => {
    setBusy(true)
    putGuide(project, text)
      .then((r) => {
        setSaved(r.text)
        setText(r.text)
        setEditing(false)
        setError(null)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false))
  }

  return (
    <section className="viewer guide" role="dialog" aria-label="Project guide">
      <header className="viewer-bar">
        <button className="back-btn" onClick={onClose} aria-label="Back">‹</button>
        <span className="viewer-path" title={path}>AGENTS.md · {project}</span>
        {dirty && <span className="chip warn">unsaved</span>}
        <button className="ghost-btn small" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Preview' : 'Edit'}
        </button>
        <button className="ghost-btn small accent" disabled={!dirty || busy} onClick={save}>
          Save
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <div className="viewer-body">
        {error && <p className="error">{error}</p>}
        {editing ? (
          <textarea
            className="guide-source"
            value={text}
            spellCheck={false}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <Suspense fallback={<p className="muted">Rendering…</p>}>
            <Markdown source={text} />
          </Suspense>
        )}
      </div>
    </section>
  )
}
