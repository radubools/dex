import { useEffect, useMemo, useState } from 'react'
import {
  createUser,
  deleteUser,
  getAuthConfig,
  listUsers,
  resetUserPassword,
  setUserProjects,
  setUserRole,
  type AuthConfig,
  type AuthUser,
  type Role,
} from '../api'
import type { Project } from '../types'

/**
 * Who may use this dex, what each of them may do, and where.
 *
 * A role is the *what* and the project ticks are the *where*; both have to
 * allow an action, which is why every row shows them side by side. An admin
 * needs no ticks — their access is the role — so that column says so rather
 * than rendering boxes that would do nothing.
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
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const refresh = () =>
    listUsers()
      .then((r) => {
        setUsers(r.users)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))

  useEffect(() => {
    void refresh()
    getAuthConfig()
      .then(setConfig)
      .catch(() => setConfig(null))
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

  const roles = config?.roles ?? []

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

        {/* What each role means, from the server rather than duplicated here:
            the person assigning one should not have to read the source. */}
        {roles.length > 0 && (
          <details className="role-key">
            <summary>What the roles mean</summary>
            <dl>
              {roles.map((r) => (
                <div key={r.role}>
                  <dt>
                    <span className={`role-pill ${r.role}`}>{r.role}</span>
                  </dt>
                  <dd>{r.description}</dd>
                </div>
              ))}
              <div>
                <dt>
                  <span className="role-pill none">no role</span>
                </dt>
                <dd>
                  Signed in and not authorised — they see a holding page and nothing else. This
                  is the absence of a role, not a role of its own.
                </dd>
              </div>
            </dl>
          </details>
        )}

        {config?.passwordEnabled && (
          adding ? (
            <NewUser
              projects={projects}
              roles={roles}
              minPasswordLength={config.minPasswordLength}
              onCancel={() => setAdding(false)}
              onCreated={() => {
                setAdding(false)
                void refresh()
              }}
            />
          ) : (
            <button className="ghost-btn small" onClick={() => setAdding(true)}>
              + Add an account
            </button>
          )
        )}

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
                    <strong>{user.name || user.username || user.email}</strong>
                    {/* The identifier they actually sign in with. A local
                        account's email is synthetic, so showing it would be
                        showing an address nobody can write to. */}
                    <small>{user.username ? `@${user.username}` : user.email}</small>
                  </span>
                  {self && <span className="chip">you</span>}
                  {user.mustChangePassword && (
                    <span className="chip warn" title="They must replace it before dex lets them in">
                      temporary password
                    </span>
                  )}
                </div>

                <div className="user-role">
                  {/* Five states, one control. "No access" is chosen as
                      explicitly as any role, so taking access away is as
                      deliberate an act as granting it. */}
                  <select
                    value={user.role ?? ''}
                    disabled={working}
                    onChange={(e) =>
                      void apply(user.id, () =>
                        setUserRole(user.id, e.target.value === '' ? null : (e.target.value as Role)),
                      )
                    }
                  >
                    <option value="">No access</option>
                    {roles.map((r) => (
                      <option key={r.role} value={r.role}>
                        {r.role[0].toUpperCase() + r.role.slice(1)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="user-projects">
                  {user.role === 'admin' ? (
                    <span className="muted small">Every project, including new ones</span>
                  ) : user.role ? (
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
                  {user.username && (
                    <button
                      className="ghost-btn small"
                      disabled={working}
                      title="Set a temporary password they must then replace"
                      onClick={() => {
                        const password = prompt(
                          `A temporary password for ${user.username}. ` +
                            `They will have to replace it before dex lets them in.`,
                        )
                        if (!password) return
                        void apply(user.id, () => resetUserPassword(user.id, password))
                      }}
                    >
                      Reset password
                    </button>
                  )}
                  <button
                    className="ghost-btn small danger"
                    disabled={working || self}
                    title={self ? 'You cannot remove your own account' : 'Remove this account'}
                    onClick={() => {
                      if (!confirm(`Remove ${user.username ?? user.email}? They will be signed out.`)) return
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
          Removing a role signs that person out immediately, and so does resetting their
          password. The last admin cannot be demoted or removed — promote someone else first.
        </p>
      </div>
    </section>
  )
}

/**
 * Create an account that signs in with a username and a password.
 *
 * The password is always temporary. An admin typing somebody else's password
 * means two people know it, so dex marks the account and refuses everything
 * but the change-password form until they have replaced it.
 */
function NewUser({
  projects,
  roles,
  minPasswordLength,
  onCreated,
  onCancel,
}: {
  projects: Project[]
  roles: { role: Role; description: string }[]
  minPasswordLength: number
  onCreated: () => void
  onCancel: () => void
}) {
  const [username, setUsername] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role | ''>('')
  const [granted, setGranted] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const tooShort = password.length > 0 && password.length < minPasswordLength

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await createUser({
        username,
        password,
        role: role === '' ? null : role,
        name: name || undefined,
        // An admin's access is their role, so grants would be dead rows.
        projects: role === 'admin' ? [] : granted,
      })
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="new-user" onSubmit={submit}>
      <h3>New account</h3>
      {error && <p className="error">{error}</p>}
      <div className="new-user-fields">
        <label>
          <span>Username</span>
          <input
            value={username}
            autoFocus
            disabled={busy}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          <span>Display name</span>
          <input
            value={name}
            placeholder="optional"
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          <span>Temporary password</span>
          <input
            type="text"
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          <span>Role</span>
          <select value={role} disabled={busy} onChange={(e) => setRole(e.target.value as Role | '')}>
            <option value="">No access</option>
            {roles.map((r) => (
              <option key={r.role} value={r.role}>
                {r.role[0].toUpperCase() + r.role.slice(1)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {role !== '' && role !== 'admin' && projects.length > 0 && (
        <div className="user-projects">
          {projects.map((project) => (
            <label className="project-check" key={project.slug}>
              <input
                type="checkbox"
                checked={granted.includes(project.slug)}
                disabled={busy}
                onChange={() =>
                  setGranted((current) =>
                    current.includes(project.slug)
                      ? current.filter((p) => p !== project.slug)
                      : [...current, project.slug],
                  )
                }
              />
              {project.name}
            </label>
          ))}
        </div>
      )}

      {tooShort && <p className="muted small">At least {minPasswordLength} characters.</p>}
      <p className="muted small">
        They will be asked to choose their own password the first time they sign in.
      </p>

      <div className="new-user-actions">
        <button className="ghost-btn small" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button className="ghost-btn small" disabled={busy || !username || tooShort || !password}>
          {busy ? 'Creating…' : 'Create'}
        </button>
      </div>
    </form>
  )
}
