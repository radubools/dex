import { useEffect, useState } from 'react'
import { listSkills, setSkillEnabled } from '../api'
import type { Project, Skill } from '../types'

/**
 * Which capabilities this install has, and which projects use them.
 *
 * One row per skill, not per version. An install that has published a skill
 * ten times has one capability; showing ten rows would bury the thing an
 * operator actually acts on — the ticks — under its own history.
 *
 * The version is a dropdown, and it opens on the current one. When a task or a
 * design turn publishes a new version the list reloads and the dropdown moves
 * with it, so the default is always "the version you would want", and an older
 * one is a deliberate choice rather than a thing you can drift into.
 */
export function SkillAdmin({
  projects,
  onClose,
}: {
  projects: Project[]
  onClose: () => void
}) {
  const [skills, setSkills] = useState<Skill[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  /** Only where an operator has deliberately looked at an older version. */
  const [picked, setPicked] = useState<Record<string, string>>({})

  const load = () =>
    listSkills()
      .then((r) => {
        setSkills(r.skills)
        // Drop a pick that no longer exists — the version was removed, or this
        // is a different install's list. Anything still present is kept, so a
        // reload does not yank an operator off the version they were reading.
        setPicked((was) => {
          const alive: Record<string, string> = {}
          for (const skill of r.skills) {
            const chosen = was[skill.name]
            if (chosen && skill.versions.some((v) => v.version === chosen)) {
              alive[skill.name] = chosen
            }
          }
          return alive
        })
      })
      .catch((err: Error) => setError(err.message))

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggle = async (skill: Skill, version: string, project: string, on: boolean) => {
    setBusy(`${skill.name}:${project}`)
    setError(null)
    try {
      await setSkillEnabled(skill.name, version, project, on)
      await load()
    } catch (err) {
      // A collision names both skills and the thing they both claim; it is the
      // one failure worth reading in full, so it is not truncated.
      setError((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="viewer" role="dialog" aria-label="Skills">
      <header className="viewer-bar">
        <span className="viewer-path">Skills{skills ? ` · ${skills.length}` : ''}</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <div className="viewer-body">
        {error && <p className="error">{error}</p>}
        {skills === null && !error && <p className="muted">Loading…</p>}
        {skills?.length === 0 && (
          <p className="muted">
            No skills yet. A skill is <code>skills/&lt;name&gt;/&lt;version&gt;/</code> with
            a <code>skill.json</code>; its <code>utils/</code> are materialised into
            each project that has it on.
          </p>
        )}

        {skills?.map((skill) => {
          const showing = picked[skill.name] ?? skill.current
          const detail =
            skill.versions.find((v) => v.version === showing) ?? skill.versions[0]
          return (
            <div className="skill-row" key={skill.name}>
              <div className="skill-head">
                <span className="skill-name">{skill.name}</span>
                <select
                  className="skill-version-pick"
                  value={showing}
                  onChange={(e) =>
                    setPicked((was) => ({ ...was, [skill.name]: e.target.value }))
                  }
                  aria-label={`Version of ${skill.name}`}
                >
                  {skill.versions.map((v, i) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}{i === 0 ? ' · current' : ''} · {whenever(v.updated)}
                    </option>
                  ))}
                </select>
                {showing !== skill.current && (
                  <span className="skill-older" title="Not the newest version">older</span>
                )}
              </div>
              {detail?.description && <p className="muted small">{detail.description}</p>}

              <div className="skill-carries">
                {detail?.modules.map((m) => <span className="skill-chip" key={m}>{m}</span>)}
                {detail?.widgets.map((w) => (
                  <span className="skill-chip widget" key={w}>{w}</span>
                ))}
                {detail?.requires.map((r) => (
                  <span className="skill-chip needs" key={r} title="Python package it imports">
                    {r}
                  </span>
                ))}
              </div>

              <div className="skill-projects">
                {projects.map((project) => {
                  const at = skill.projects[project.slug]
                  const on = at === showing
                  // On this skill but a different version: ticking moves it
                  // here, and saying which version it is moving from beats a
                  // tick that appears to do nothing.
                  const elsewhere = at && at !== showing ? at : null
                  return (
                    <label
                      className={`skill-tick ${elsewhere ? 'elsewhere' : ''}`}
                      key={project.slug}
                      title={elsewhere ? `On v${elsewhere}; ticking moves it here` : undefined}
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={busy !== null}
                        onChange={() => void toggle(skill, showing, project.slug, !on)}
                      />
                      {project.name}
                      {elsewhere && <span className="skill-elsewhere">v{elsewhere}</span>}
                      {busy === `${skill.name}:${project.slug}` && <span className="pulse" />}
                    </label>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/** "3 days ago", roughly. Precision here would be noise. */
function whenever(seconds: number): string {
  if (!seconds) return ''
  const ago = Date.now() / 1000 - seconds
  if (ago < 90) return 'just now'

  // Largest scale the age clears, so 200 seconds reads as minutes and 200,000
  // as days.
  const scales: [number, string][] = [
    [60, 'minute'], [3600, 'hour'], [86400, 'day'], [604800, 'week'],
  ]
  let chosen = scales[0]
  for (const scale of scales) if (ago >= scale[0]) chosen = scale

  const n = Math.round(ago / chosen[0])
  return `${n} ${chosen[1]}${n === 1 ? '' : 's'} ago`
}
