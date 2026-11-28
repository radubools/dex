import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { User } from 'firebase/auth'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { Profile } from '../firebase'
import {
  answer, eventsOf, fetchFile, myProjects, myRequests, served, signOutNow, submit, tasksOf, uploadAttachment,
  type Attachment, type Project, type Question, type Request, type Task, type TaskEvent, type TaskFile,
} from './data'

const ACTIVE = new Set(['queued', 'running', 'awaiting_input', 'paused', 'blocked', 'todo'])

const STATE_LABEL: Record<string, string> = {
  queued: 'queued', running: 'running', awaiting_input: 'waiting', paused: 'paused',
  blocked: 'blocked', todo: 'later', succeeded: 'done', failed: 'failed', cancelled: 'cancelled',
  archived: 'archived',
}

/** Run `fn` now and every `seconds` while the tab is visible. */
function usePoll(fn: () => Promise<void> | void, seconds: number, deps: unknown[]) {
  const saved = useRef(fn)
  saved.current = fn
  useEffect(() => {
    let stopped = false
    const run = () => { if (!stopped && document.visibilityState === 'visible') void saved.current() }
    run()
    const timer = setInterval(run, seconds * 1000)
    document.addEventListener('visibilitychange', run)
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', run) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

function ago(ms: number | undefined): string {
  if (!ms) return ''
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return new Date(ms).toLocaleDateString()
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

/** Mounted by the shell once it has checked this person is authorised. */
export function App({ user, profile, pollSeconds }: { user: User; profile: Profile; pollSeconds: number }) {
  return <Signed user={user} profile={profile} pollSeconds={pollSeconds} />
}

function Signed({ user, profile, pollSeconds }: { user: User; profile: Profile; pollSeconds: number }) {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [error, setError] = useState('')
  const [chosen, setChosen] = useState<string>(() => localStorage.getItem('dex-remote-project') ?? '')
  const [taskId, setTaskId] = useState<string | null>(null)

  usePoll(async () => {
    try {
      setProjects(await myProjects(profile))
      setError('')
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }, pollSeconds * 4, [user.uid])

  const project = projects?.find((p) => p.slug === chosen) ?? projects?.[0]
  useEffect(() => {
    if (project) try { localStorage.setItem('dex-remote-project', project.slug) } catch { /* fine */ }
  }, [project?.slug])

  return (
    <div className="shell">
      <header className="top">
        <span className="brand">dex</span>
        {/* Only the projects this person is authorised for (users/<email> in
            Firebase); the list, the tasks and every request follow the choice. */}
        {projects && projects.length > 0 && (
          <label className="project-pick">
            <span className="muted small">Project</span>
            <select value={project?.slug ?? ''} onChange={(e) => { setChosen(e.target.value); setTaskId(null) }}>
              {projects.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
            </select>
          </label>
        )}
        <span className="spacer" />
        <span className="muted small who">{user.email}</span>
        <button className="ghost small" onClick={() => signOutNow()}>Sign out</button>
      </header>
      {error && <p className="error banner">{error}</p>}
      {projects === null && !error && <div className="center muted">Loading your projects…</div>}
      {projects?.length === 0 && (
        <div className="center">
          <div className="card">
            <h2>No projects yet</h2>
            <p className="muted">
              You are authorised as <b>{user.email}</b>, but no project has been given to this account yet,
              or none is served by a dex at the moment. Ask the operator.
            </p>
          </div>
        </div>
      )}
      {project && (
        <ProjectView
          key={project.slug} project={project} user={user} pollSeconds={pollSeconds}
          taskId={taskId} setTaskId={setTaskId}
        />
      )}
    </div>
  )
}

function ProjectView({ project, user, pollSeconds, taskId, setTaskId }: {
  project: Project; user: User; pollSeconds: number
  taskId: string | null; setTaskId: (id: string | null) => void
}) {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [requests, setRequests] = useState<Request[]>([])
  /** Sent from this page and not yet seen by any dex. */
  const [sent, setSent] = useState<{ id: string; title: string; at: number }[]>([])
  const [error, setError] = useState('')

  const newest = useRef<Task['updatedAt']>(undefined)
  const polls = useRef(0)
  const refresh = useCallback(async () => {
    try {
      // Changes only, after the first load; a full reload every 40 polls
      // (10 minutes) picks up anything the incremental reads missed.
      const full = !newest.current || polls.current++ % 40 === 0
      const [t, r] = await Promise.all([
        tasksOf(project.slug, full ? undefined : newest.current),
        sent.length || full ? myRequests(project.slug, user) : Promise.resolve(null),
      ])
      for (const task of t) {
        if (task.updatedAt && (!newest.current || task.updatedAt.toMillis() > newest.current.toMillis())) {
          newest.current = task.updatedAt
        }
      }
      setTasks((old) => {
        if (full || !old) return t
        const changed = new Map(t.map((x) => [x.id, x]))
        const kept = old.map((x) => changed.get(x.id) ?? x)
        const added = t.filter((x) => !old.some((o) => o.id === x.id))
        return [...added, ...kept].sort((a, b) => (b.createdAt?.toMillis() ?? 0) - (a.createdAt?.toMillis() ?? 0))
      })
      if (!r) return
      setRequests(r)
      setSent((s) => s.filter((x) => !r.some((q) => q.id === x.id)))
      setError('')
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }, [project.slug, user, sent.length])
  usePoll(refresh, pollSeconds, [refresh])

  const machines = served(project)
  const task = tasks?.find((t) => t.id === taskId) ?? null
  const rejected = requests.filter((r) => r.status === 'rejected')

  return (
    <div className={`columns ${task ? 'has-detail' : ''}`}>
      <section className="list">
        <div className="served small">
          <span className={`dot ${machines ? 'on' : ''}`} />
          {machines ? <>Served by {machines}</> : <>No dex is polling this project right now; requests wait in the queue for up to 7 days.</>}
        </div>
        {project.description && <p className="muted small">{project.description}</p>}
        <Composer key={project.slug} project={project} user={user} onSent={(id, title) => { setSent((s) => [{ id, title, at: Date.now() }, ...s]); void refresh() }} />
        {error && <p className="error small">{error}</p>}
        <ul className="tasks">
          {sent.map((s) => (
            <li key={s.id} className="task pending">
              <div className="task-title">{s.title || 'Untitled'}</div>
              <div className="task-meta small muted"><span className="chip">sent</span> waiting for a dex to pick it up · {ago(s.at)}</div>
            </li>
          ))}
          {rejected.map((r) => (
            <li key={r.id} className="task rejected">
              <div className="task-title">{r.title || 'Untitled'}</div>
              <div className="task-meta small"><span className="chip failed">refused</span> <span className="muted">{r.reason}</span></div>
            </li>
          ))}
          {tasks?.map((t) => (
            <li key={t.id} className={`task ${t.id === taskId ? 'on' : ''}`}>
              <button className="task-btn" onClick={() => setTaskId(t.id === taskId ? null : t.id)}>
                <div className="task-title">{t.title}</div>
                <div className="task-meta small muted">
                  <span className={`chip ${t.state}`}>{STATE_LABEL[t.state] ?? t.state}</span>
                  {ACTIVE.has(t.state) && t.activity ? <span className="activity">{t.activity}</span> : null}
                  <span>{t.email.split('@')[0]} · {ago(t.createdAt?.toMillis())}</span>
                  {t.files?.length ? <span>{t.files.length} file{t.files.length === 1 ? '' : 's'}</span> : null}
                </div>
              </button>
            </li>
          ))}
          {tasks?.length === 0 && sent.length === 0 && <li className="muted small empty">No tasks yet. Describe one above.</li>}
        </ul>
      </section>
      {task && <TaskView key={task.id} task={task} pollSeconds={pollSeconds} onClose={() => setTaskId(null)} />}
    </div>
  )
}

/** A file picked in the composer: uploaded at once, sent by id. */
type Picked = { key: number; file: File; fraction: number; attachment?: Attachment; error?: string }

let pickedKeys = 0

function Composer({ project, user, onSent }: { project: Project; user: User; onSent: (id: string, title: string) => void }) {
  const [title, setTitle] = useState('')
  const [problem, setProblem] = useState('')
  const [picked, setPicked] = useState<Picked[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)

  const patch = (key: number, change: Partial<Picked>) =>
    setPicked((all) => all.map((p) => (p.key === key ? { ...p, ...change } : p)))

  const upload = (item: Picked) => {
    patch(item.key, { error: undefined, fraction: 0 })
    uploadAttachment(project.slug, item.file, (fraction) => patch(item.key, { fraction }))
      .then((attachment) => patch(item.key, { attachment, fraction: 1 }))
      .catch((e) => patch(item.key, { error: String((e as Error).message ?? e) }))
  }

  const add = (files: File[]) => {
    const room = Math.max(0, 20 - picked.length)
    const items = files.slice(0, room).map((file) => ({ key: ++pickedKeys, file, fraction: 0 }))
    setPicked((all) => [...all, ...items])
    items.forEach(upload)
  }

  const pending = picked.some((p) => !p.attachment)
  const send = async () => {
    if (!problem.trim() || busy || pending) return
    setError('')
    setBusy('Sending')
    try {
      const id = await submit(project.slug, title.trim(), problem.trim(), picked.map((p) => p.attachment!))
      onSent(id, title.trim() || problem.trim().split('\n')[0].slice(0, 80))
      setTitle(''); setProblem(''); setPicked([])
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="composer card"
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault() }}
      onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); add(Array.from(e.dataTransfer.files)) } }}>
      <input className="title-input" placeholder="Title (optional)" value={title} maxLength={200}
        onChange={(e) => setTitle(e.target.value)} />
      <textarea placeholder={`What should dex do in ${project.name}?`} value={problem} maxLength={20000}
        rows={4} onChange={(e) => setProblem(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send() }} />
      {picked.length > 0 && (
        <ul className="attached small">
          {picked.map((p) => (
            <li key={p.key}>
              <span className="name">{p.file.name}</span> <span className="muted">{size(p.file.size)}</span>
              {p.error ? (
                <>
                  <span className="error">{p.error}</span>
                  <button className="ghost small" onClick={() => upload(p)}>Retry</button>
                </>
              ) : p.attachment ? (
                <span className="muted" title={p.attachment.id}>uploaded</span>
              ) : (
                <span className="muted">{p.fraction > 0 ? `uploading ${Math.round(p.fraction * 100)}%` : 'preparing…'}</span>
              )}
              <button className="ghost small" onClick={() => setPicked((all) => all.filter((q) => q.key !== p.key))}
                aria-label={`Remove ${p.file.name}`}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer-row">
        <input ref={input} type="file" multiple hidden onChange={(e) => {
          add(Array.from(e.target.files ?? [])); e.target.value = ''
        }} />
        <button className="ghost" onClick={() => input.current?.click()} disabled={!!busy || picked.length >= 20}>Attach</button>
        <span className="spacer" />
        {busy && <span className="muted small">{busy}…</span>}
        {!busy && pending && <span className="muted small">Waiting for uploads…</span>}
        <button className="primary" onClick={() => void send()} disabled={!problem.trim() || !!busy || pending}>Send</button>
      </div>
      {error && <p className="error small">{error}</p>}
    </div>
  )
}

function TaskView({ task, pollSeconds, onClose }: { task: Task; pollSeconds: number; onClose: () => void }) {
  const [events, setEvents] = useState<TaskEvent[]>([])
  const last = useRef(0)

  usePoll(async () => {
    const fresh = await eventsOf(task.project, task.id, last.current)
    if (fresh.length) {
      last.current = fresh[fresh.length - 1].seq
      setEvents((e) => [...e, ...fresh])
    }
  }, pollSeconds, [task.id])

  const summary = useMemo(
    () => (task.summary ? DOMPurify.sanitize(marked.parse(task.summary, { async: false }) as string) : ''),
    [task.summary],
  )
  const steps = events.filter((e) => e.kind !== 'summary')

  return (
    <section className="detail">
      <header className="detail-bar">
        <span className={`chip ${task.state}`}>{STATE_LABEL[task.state] ?? task.state}</span>
        <span className="detail-title">{task.title}</span>
        <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
      </header>
      <div className="detail-body">
        <p className="muted small">
          {task.email} · {task.createdAt?.toDate().toLocaleString()}{task.machine ? ` · on ${task.machine}` : ''}
          {task.attempts && task.attempts > 1 ? ` · attempt ${task.attempts}` : ''}
        </p>
        <details className="problem">
          <summary className="small muted">Request</summary>
          <pre>{task.problem}</pre>
          {task.attachments?.length ? (
            <p className="small muted">Attached: {task.attachments.map((a) => a.name).join(', ')}</p>
          ) : null}
        </details>
        {task.error && <p className="error">{task.error}</p>}
        {task.question && task.state === 'awaiting_input' && (
          <AnswerBox key={task.question.id} task={task} question={task.question} />
        )}
        {summary && (
          <div className="summary card">
            <div className="small muted label">Summary</div>
            <div className="prose" dangerouslySetInnerHTML={{ __html: summary }} />
          </div>
        )}
        {task.files?.length > 0 && <Files files={task.files} />}
        <div className="small muted label">Steps</div>
        <ol className="steps">
          {steps.map((e) => (
            <li key={e.id} className={`step ${e.kind}`}>
              <span className="when">{e.ts?.toDate().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
              <span className="what">{e.text}</span>
            </li>
          ))}
          {steps.length === 0 && <li className="muted small">Nothing yet. This refreshes every {pollSeconds} s.</li>}
        </ol>
      </div>
    </section>
  )
}

function Files({ files }: { files: TaskFile[] }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const get = async (file: TaskFile, open: boolean) => {
    setBusy(file.path)
    setError('')
    try {
      const blob = await fetchFile(file.object)
      const url = URL.createObjectURL(blob)
      if (open) {
        window.open(url, '_blank', 'noopener')
      } else {
        const a = document.createElement('a')
        a.href = url
        a.download = file.path.split('/').pop() ?? 'file'
        a.click()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="files">
      <div className="small muted label">Files</div>
      <ul>
        {files.map((f) => (
          <li key={f.path} className={f.available ? '' : 'pending'}>
            <span className="path">{f.path}</span>
            <span className="muted small">{size(f.size)}</span>
            {f.available ? (
              <>
                <button className="ghost small" disabled={busy === f.path} onClick={() => void get(f, true)}>Open</button>
                <button className="ghost small" disabled={busy === f.path} onClick={() => void get(f, false)}>Download</button>
              </>
            ) : <span className="muted small">uploading…</span>}
          </li>
        ))}
      </ul>
      {error && <p className="error small">{error}</p>}
    </div>
  )
}

function AnswerBox({ task, question }: { task: Task; question: Question }) {
  const [text, setText] = useState('')
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle')
  const [error, setError] = useState('')
  const send = async (value: string) => {
    if (!value.trim() || state === 'sending') return
    setState('sending')
    setError('')
    try {
      await answer(task.project, task.id, question.id, value.trim())
      setState('sent')
    } catch (e) {
      setError(String((e as Error).message ?? e))
      setState('idle')
    }
  }
  return (
    <div className="question card">
      <div className="small muted label">dex is asking</div>
      <p className="question-text">{question.text}</p>
      {state === 'sent' ? (
        <p className="muted small">Answer sent. The task goes on once its dex picks it up (within about 15 s).</p>
      ) : (
        <>
          {question.options.length > 0 && (
            <div className="options">
              {question.options.map((o) => (
                <button key={o} className="ghost" disabled={state === 'sending'} onClick={() => void send(o)}>{o}</button>
              ))}
            </div>
          )}
          <div className="composer-row">
            <input className="answer-input" placeholder="Or write an answer" value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void send(text) }} />
            <button className="primary" disabled={!text.trim() || state === 'sending'} onClick={() => void send(text)}>Answer</button>
          </div>
        </>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  )
}
