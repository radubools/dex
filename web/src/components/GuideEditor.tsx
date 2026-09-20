import { Suspense, lazy, useEffect, useState } from 'react'
import {
  getGuide, getSkillDoc, projectSkills, putGuide, putSkillDoc,
} from '../api'
import type { SkillVersion } from '../types'

const Markdown = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))

/** What is open: the project's own brief, or one skill's instructions. */
type Target = { kind: 'guide' } | { kind: 'skill'; name: string; version: string }

const same = (a: Target, b: Target) =>
  a.kind === b.kind && (a.kind === 'guide' || a.name === (b as { name: string }).name)

/**
 * A project's standing instructions — now more than one document.
 *
 * `AGENTS.md` used to be everything: what the project produces, how the
 * environment works, how viewers are bound, how a helper becomes shared. The
 * shared half moved to a common guide every project reads, and each capability
 * moved into a skill that carries its own `SKILL.md`. So this is a picker: the
 * project's own brief, and the skills it has enabled.
 *
 * Saving a skill's instructions **publishes a new version of that skill**. A
 * published version is what some project is running on and what another
 * install may have copied, and its version is a hash of its contents — editing
 * it in place would leave the name describing something that no longer exists.
 * The server forks, writes, and publishes; every project on that skill moves.
 */
export function GuideEditor({ project, onClose }: { project: string; onClose: () => void }) {
  const [target, setTarget] = useState<Target>({ kind: 'guide' })
  const [skills, setSkills] = useState<SkillVersion[]>([])
  const [text, setText] = useState('')
  const [saved, setSaved] = useState('')
  const [path, setPath] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    projectSkills(project)
      .then((r) => live && setSkills(r.skills))
      // A project with no skills is not an error; the picker just shows one row.
      .catch(() => live && setSkills([]))
    return () => { live = false }
  }, [project])

  useEffect(() => {
    let live = true
    setEditing(false)
    setNote(null)
    const load =
      target.kind === 'guide'
        ? getGuide(project).then((g) => ({ text: g.text, path: g.path }))
        : getSkillDoc(target.name, target.version).then((d) => ({
            text: d.text, path: d.path,
          }))
    load
      .then((got) => {
        if (!live) return
        setText(got.text)
        setSaved(got.text)
        setPath(got.path)
        setError(null)
      })
      .catch((err: Error) => live && setError(err.message))
    return () => { live = false }
  }, [project, target])

  const dirty = text !== saved

  const save = () => {
    setBusy(true)
    setNote(null)
    const write =
      target.kind === 'guide'
        ? putGuide(project, text).then((r) => ({ text: r.text, note: null as string | null }))
        : putSkillDoc(target.name, target.version, text).then((r) => ({
            text: r.text,
            // Saying what happened, because it is more than a save: a new
            // version exists and other projects may have moved onto it.
            note:
              r.version === target.version
                ? 'No change, so no new version.'
                : `Published v${r.version}` +
                  (r.moved.length
                    ? ` — ${r.moved.join(', ')} moved to it.`
                    : ' — no project was on it.'),
          }))
    write
      .then((r) => {
        setSaved(r.text)
        setText(r.text)
        setEditing(false)
        setError(null)
        setNote(r.note)
        // The skill's version changed, so the picker's row is stale.
        if (target.kind === 'skill') {
          void projectSkills(project).then((got) => {
            setSkills(got.skills)
            const now = got.skills.find((s) => s.name === target.name)
            if (now) setTarget({ kind: 'skill', name: now.name, version: now.version })
          })
        }
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false))
  }

  const title =
    target.kind === 'guide' ? `AGENTS.md · ${project}` : `${target.name} · SKILL.md`

  return (
    <section className="viewer guide" role="dialog" aria-label="Project instructions">
      <header className="viewer-bar">
        <button className="back-btn" onClick={onClose} aria-label="Back">‹</button>
        <span className="viewer-path" title={path}>{title}</span>
        {dirty && <span className="chip warn">unsaved</span>}
        <button className="ghost-btn small" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Preview' : 'Edit'}
        </button>
        <button className="ghost-btn small accent" disabled={!dirty || busy} onClick={save}>
          Save
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <nav className="guide-picker">
        <button
          className={`guide-pick ${target.kind === 'guide' ? 'on' : ''}`}
          onClick={() => setTarget({ kind: 'guide' })}
          disabled={dirty}
          title={dirty ? 'Save or discard first' : 'What this project produces'}
        >
          This project
        </button>
        {skills.map((skill) => (
          <button
            key={skill.name}
            className={`guide-pick ${same(target, { kind: 'skill', name: skill.name, version: skill.version }) ? 'on' : ''}`}
            onClick={() => setTarget({ kind: 'skill', name: skill.name, version: skill.version })}
            disabled={dirty}
            title={dirty ? 'Save or discard first' : skill.description}
          >
            {skill.name}
            <span className="guide-pick-version">v{skill.version}</span>
          </button>
        ))}
      </nav>

      {target.kind === 'skill' && (
        <p className="guide-note muted small">
          Saving publishes a new version of <code>{target.name}</code>. Every project
          on it moves; the version you are reading stays on disk.
        </p>
      )}

      <div className="viewer-body">
        {error && <p className="error">{error}</p>}
        {note && <p className="guide-published">{note}</p>}
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
