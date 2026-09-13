import { useEffect, useMemo, useState } from 'react'
import {
  deleteUser,
  listUsers,
  setUserProjects,
  setUserRole,
  type AuthUser,
} from '../api'
import type { Project } from '../types'

/**
 * Who may use this dex, and which projects each of them sees.
 *
 * Unassigned accounts sort first: those are the only rows that need the
 * operator to do something, and on a public deployment they are how you find
 * out somebody signed in at all.
 */
export function UserAdmin({
  projects,
  me,
  onClose,
}: {
  projects: Project[]
  me: AuthUser | null
  onClose: () => void
}) {
  const [users, setUsers] = useState<AuthUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = () =>
    listUsers()
      .then((r) => {
        setUsers(r.users)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))

  useEffect(() => {
    void refresh()
  }, [])

  const waiting = useMemo(() => (users ?? []).filter((u) => !u.role).length, [users])

  /** Runs one change, then reloads: the server is the authority on the result. */
  const apply = async (id: string, change: () => Promise<unknown>) => {
    setBusy(id)
    setError(null)
    try {
      await change()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="viewer" role="dialog" aria-label="Users and access">
      <header className="viewer-bar">
        <span className="viewer-path">
          Users{waiting > 0 ? ` · ${waiting} waiting for a role` : ''}
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </header>

      <div className="viewer-body">
        {error && <p className="error">{error}</p>}
        {users === null && !error && <p className="muted">Loading…</p>}
        {users?.length === 0 && <p className="muted">Nobody has signed in yet.</p>}

        <div className="user-list">
          {(users ?? []).map((user) => {
            const self = me?.id === user.id
            const working = busy === user.id
            return (
              <div className={`user-row ${user.role ? '' : 'pending'}`} key={user.id}>
                <div className="user-who">
                  {user.picture ? (
                    <img className="user-avatar" src={user.picture} alt="" referrerPolicy="no-referrer" />
                  ) : (
                    <span className="user-avatar placeholder" aria-hidden="true" />
                  )}
                  <span>
                    <strong>{user.name || user.email}</strong>
                    <small>{user.email}</small>
                  </span>
                  {self && <span className="chip">you</span>}
                </div>

                <div className="user-role">
                  {/* Three states, one control. "No access" is a role you can
                      choose, not just the absence of one, so taking access away
                      is as explicit as granting it. */}
                  <select
                    value={user.role ?? ''}
                    disabled={working}
                    onChange={(e) =>
                      void apply(user.id, () =>
                        setUserRole(
                          user.id,
                          e.target.value === '' ? null : (e.target.value as 'admin' | 'user'),
                        ),
                      )
                    }
                  >
                    <option value="">No access</option>
                    <option value="user">User</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>

                <div className="user-projects">
                  {user.role === 'admin' ? (
                    <span className="muted small">Every project, including new ones</span>
                  ) : user.role === 'user' ? (
                    projects.length === 0 ? (
                      <span className="muted small">No projects exist yet</span>
                    ) : (
                      projects.map((project) => {
                        const on = user.projects.includes(project.slug)
                        return (
                          <label className="project-check" key={project.slug}>
                            <input
                              type="checkbox"
                              checked={on}
                              disabled={working}
                              onChange={() =>
                                void apply(user.id, () =>
                                  // The whole set every time: the endpoint
                                  // replaces rather than merges, which is what
                                  // makes unticking work.
                                  setUserProjects(
                                    user.id,
                                    on
                                      ? user.projects.filter((p) => p !== project.slug)
                                      : [...user.projects, project.slug],
                                  ),
                                )
                              }
                            />
                            {project.name}
                          </label>
                        )
                      })
                    )
                  ) : (
                    <span className="muted small">Give them a role to choose projects</span>
                  )}
                </div>

                <div className="user-actions">
                  <button
                    className="ghost-btn small danger"
                    disabled={working || self}
                    title={self ? 'You cannot remove your own account' : 'Remove this account'}
                    onClick={() => {
                      if (!confirm(`Remove ${user.email}? They will be signed out.`)) return
                      void apply(user.id, () => deleteUser(user.id))
                    }}
                  >
                    Remove
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        <p className="muted small">
          Removing a role signs that person out immediately. The last admin cannot be demoted or
          removed — promote someone else first.
        </p>
      </div>
    </section>
  )
}
