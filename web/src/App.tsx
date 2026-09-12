import { Suspense, lazy, useCallback, useEffect, useReducer, useRef, useState } from 'react'
import {
  confirmPlan, createProject, createThread, deleteThread, getHealth, getSettings,
  getTaskEvents, getThread, listPackages, listProjects, listTasks, listThreads, openStream,
  sendMessage,
  setAutoApprove,
} from './api'
import { initialState, reduce } from './store'
import type { Health, PlannedTask, Project, ThreadSummary } from './types'
import { Chat } from './components/Chat'
import { Composer } from './components/Composer'
import { ProjectPicker } from './components/ProjectPicker'
import { SettingsMenu } from './components/SettingsMenu'
import { TaskPanel } from './components/TaskPanel'
import type { ViewerTarget } from './components/Viewer'

// highlight.js (and, for markdown, mermaid) only load when something is opened.
const Viewer = lazy(() => import('./components/Viewer').then((m) => ({ default: m.Viewer })))
const CostDashboard = lazy(() =>
  import('./components/CostDashboard').then((m) => ({ default: m.CostDashboard })),
)
const Feed = lazy(() => import('./components/Feed').then((m) => ({ default: m.Feed })))
const GuideEditor = lazy(() =>
  import('./components/GuideEditor').then((m) => ({ default: m.GuideEditor })),
)
const PackageLibrary = lazy(() =>
  import('./components/PackageLibrary').then((m) => ({ default: m.PackageLibrary })),
)


/** The canvas views, in the order they appear in the header. */
const VIEWS = [
  { key: 'chat' as const, icon: '💬', label: 'Chat' },
  { key: 'feed' as const, icon: '📺', label: 'Review feed' },
  { key: 'library' as const, icon: '📚', label: 'Library' },
]

/** The place the URL is pointing at: project, thread, and which tab. */
function urlState(): {
  project: string | null
  thread: string | null
  view: 'chat' | 'feed' | 'library'
} {
  const params = new URLSearchParams(window.location.search)
  const view = params.get('view')
  return {
    project: params.get('project'),
    thread: params.get('thread'),
    view:
      view === 'feed' || view === 'library' ? view : 'chat',
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reduce, initialState)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [threadId, setThreadId] = useState<string | null>(urlState().thread)
  const [health, setHealth] = useState<Health | null>(null)
  const [fatal, setFatal] = useState<string | null>(null)
  const [planning, setPlanning] = useState(false)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [viewing, setViewing] = useState<ViewerTarget | null>(null)
  /** What to return to when the viewer's back button is pressed. */
  const [cameFrom, setCameFrom] = useState<ViewerTarget | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [threadError, setThreadError] = useState<string | null>(null)
  const [showCosts, setShowCosts] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  /** The thread a hide has been asked for but not yet confirmed. */
  const [confirmHide, setConfirmHide] = useState<ThreadSummary | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  // Seeded from the URL so a reload stays where you were rather than falling
  // back to the default project. The URL is the single source of truth for
  // these three, which also makes a view shareable.
  const [project, setProject] = useState<string | null>(urlState().project)
  /** The main column shows either the conversation or the review feed. */
  const [view, setView] = useState<'chat' | 'feed' | 'library'>(urlState().view)
  /** Whether this project has a pose library worth offering a tab for. */
  const [hasPackages, setHasPackages] = useState(false)
  const loaded = useRef<Set<string>>(new Set())

  // One stream for the whole app; tasks and threads are demultiplexed by the store.
  useEffect(() => {
    const close = openStream(
      (event) => {
        dispatch({ type: 'event', event })
        // A note typed into a finished task starts a follow-up; follow it, or
        // the reply would appear on a task the reader is not looking at.
        if (event.type === 'task_message_delivered') {
          setActiveTaskId((current) => (current === event.taskId ? event.startedTaskId : current))
        }
        // Planning is server-side state: another tab, or this one before a
        // reload, may have started it. Follow it rather than only tracking the
        // call this tab made.
        if (event.type === 'thread_busy') {
          setThreadId((current) => {
            if (event.threadId === current) setPlanning(event.busy)
            return current
          })
        }
      },
      (up) => dispatch({ type: 'connection', up }),
    )
    return close
  }, [])

  useEffect(() => {
    getHealth().then(setHealth).catch((err: Error) => setFatal(err.message))
    getSettings()
      .then((s) => dispatch({ type: 'autoApprove', enabled: s.settings.auto_approve }))
      .catch(() => {})
    listTasks().then((tasks) => dispatch({ type: 'tasks', tasks })).catch(() => {})
    listProjects()
      .then((r) => {
        setProjects(r.projects)
        setProject((current) => {
          // A project named in the URL wins, but only if it still exists.
          if (current && r.projects.some((p) => p.slug === current)) return current
          return r.default
        })
      })
      .catch((err: Error) => setFatal(err.message))
  }, [])

  // Mirror the current place into the URL. `replaceState` rather than
  // `pushState`: switching project is not a navigation the back button should
  // have to unwind step by step.
  useEffect(() => {
    if (!project) return
    const params = new URLSearchParams(window.location.search)
    params.set('project', project)
    if (threadId) params.set('thread', threadId)
    else params.delete('thread')
    if (view !== 'chat') params.set('view', view)
    else params.delete('view')
    const next = `${window.location.pathname}?${params}${window.location.hash}`
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(null, '', next)
    }
  }, [project, threadId, view])

  // The Poses tab appears only for a project that has any.
  useEffect(() => {
    if (!project) return
    setHasPackages(false)
    listPackages(project)
      .then(({ packages }) => setHasPackages(packages.length > 0))
      .catch(() => {})
  }, [project])

  // Threads belong to a project, so switching project reloads the list.
  useEffect(() => {
    if (!project) return
    listThreads(project)
      .then(async (existing) => {
        setThreads(existing)
        setThreadId((current) => {
          // Keep the thread the URL asked for, when this project has it.
          if (current && existing.some((t) => t.id === current)) return current
          return existing[0]?.id ?? null
        })
        if (existing.length === 0) setThreadId((await createThread(project)).id)
      })
      .catch((err: Error) => setFatal(err.message))
  }, [project])

  // Read a thread's stored history. Called on open, and again after anything
  // the event stream cannot express as a patch — an archived task leaves the
  // thread altogether, and a restart resets a row rather than changing it.
  const loadThread = useCallback(async (id: string) => {
    setThreadError(null)
    try {
      const { thread, tasks } = await getThread(id)
      dispatch({ type: 'messages', threadId: thread.id, messages: thread.messages })
      // Postgres returns every task of the thread, whichever process ran it,
      // so there is nothing to reconstruct from message snapshots.
      dispatch({ type: 'tasks', tasks })
      // A call may already be in flight, started before this page loaded.
      setPlanning(thread.planning === true)
    } catch (err) {
      // Never fail silently: an empty thread and a thread that failed to load
      // look identical otherwise.
      loaded.current.delete(id)
      setThreadError((err as Error).message)
    }
  }, [])

  // Read a task's own history the first time it is opened. The live stream
  // replays only the most recent events across every task, so a task from an
  // earlier session — or one restarted from one — arrives with nothing to
  // show until this asks for it.
  const historyLoaded = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!activeTaskId || historyLoaded.current.has(activeTaskId)) return
    historyLoaded.current.add(activeTaskId)
    getTaskEvents(activeTaskId)
      .then((events) => dispatch({ type: 'taskEvents', events }))
      .catch(() => {
        // Nothing to show is the state it was already in; let it be retried.
        historyLoaded.current.delete(activeTaskId)
      })
  }, [activeTaskId])

  // Load a thread's stored history the first time it is opened.
  useEffect(() => {
    if (!threadId || loaded.current.has(threadId)) return
    loaded.current.add(threadId)
    void loadThread(threadId)
  }, [threadId, loadThread])

  // Scoped to the current project: an unscoped refetch repopulates the sidebar
  // with every project's threads, which is how threads looked unscoped even
  // though the store filters correctly.
  const refreshThreads = useCallback(() => {
    if (!project) return
    listThreads(project).then(setThreads).catch(() => {})
  }, [project])

  const send = useCallback(
    async (text: string) => {
      if (!threadId) return
      setPlanning(true)
      try {
        await sendMessage(threadId, text)
        refreshThreads()
      } catch (err) {
        // The server already recorded the failure as a thread message; this
        // only covers the case where the request never landed.
        if (!(err instanceof Error && err.message.startsWith('50'))) setFatal(String(err))
      } finally {
        setPlanning(false)
      }
    },
    [threadId, refreshThreads],
  )

  const confirm = useCallback(
    async (tasks: PlannedTask[]) => {
      if (!threadId) return
      const created = await confirmPlan(threadId, tasks)
      dispatch({ type: 'tasks', tasks: created })
      // One task is unambiguous: opening it is what you wanted to look at.
      // Several are not — picking the first would hide the other twenty-nine
      // behind a pane nobody asked for, and the thread is where they all are.
      if (created.length === 1) setActiveTaskId(created[0].id)
      refreshThreads()
    },
    [threadId, refreshThreads],
  )

  const newThread = async () => {
    const thread = await createThread(project ?? undefined)
    setThreads((current) => [thread, ...current])
    setThreadId(thread.id)
    setActiveTaskId(null)
    setViewing(null)
    setDrawerOpen(false)
  }

  // Hides rather than deletes: the row, its messages and its tasks all stay.
  const hideThread = async (id: string) => {
    setConfirmHide(null)
    await deleteThread(id)
    loaded.current.delete(id)
    const remaining = threads.filter((t) => t.id !== id)
    setThreads(remaining)
    if (threadId === id) setThreadId(remaining[0]?.id ?? (await createThread(project ?? undefined)).id)
  }

  if (fatal) {
    return (
      <div className="fatal">
        <h1>dex can't reach the server</h1>
        <p className="error">{fatal}</p>
        <p className="muted">
          Start it with <code>.venv/bin/dex</code> and open the URL it prints.
        </p>
      </div>
    )
  }

  const messages = threadId ? state.threadMessages[threadId] ?? [] : []
  const activeTask = activeTaskId ? state.tasks[activeTaskId] : undefined

  return (
    <div
      className={`app ${activeTask ? 'with-panel' : ''} ${
        viewing || showCosts || showGuide ? 'with-viewer' : ''
      }`}
    >
      <aside className={`threads ${drawerOpen ? 'open' : ''}`}>
        <div className="threads-head">
          <span className="brand">dex</span>
          <button className="ghost-btn small" onClick={() => void newThread()}>New</button>
        </div>
        <div className="thread-list">
          {(() => {
            const design = threads.filter((t) => t.kind === 'project_design')
            const chats = threads.filter((t) => t.kind !== 'project_design')
            const row = (thread: ThreadSummary) => (
              <div key={thread.id} className={`thread-row ${thread.id === threadId ? 'on' : ''}`}>
                <button
                  className="thread-open"
                  onClick={() => {
                    setThreadId(thread.id)
                    setDrawerOpen(false)
                    setActiveTaskId(null)
                    setViewing(null)
                    setView('chat')
                  }}
                >
                  <span className="thread-title">
                    {thread.kind === 'project_design' && (
                      <span className="thread-icon" title="Project design">◈</span>
                    )}
                    {thread.title}
                  </span>
                  <span className="thread-meta">
                    {thread.taskIds.length > 0 && `${thread.taskIds.length} tasks · `}
                    {thread.costUsd ? `$${thread.costUsd.toFixed(2)} · ` : ''}
                    {new Date(thread.updatedAt * 1000).toLocaleDateString()}
                  </span>
                </button>
                {/* The design thread is the project's own conversation; it is
                    not something to delete by accident. */}
                {thread.kind !== 'project_design' && (
                  <button
                    className="icon-btn small"
                    aria-label={`Hide ${thread.title}`}
                    title="Hide this thread"
                    onClick={() => setConfirmHide(thread)}
                  >
                    ✕
                  </button>
                )}
              </div>
            )
            return (
              <>
                {design.map(row)}
                {design.length > 0 && chats.length > 0 && <hr className="thread-divider" />}
                {chats.map(row)}
              </>
            )
          })()}
        </div>

        {health && (
          <footer className="health">
            <span className={`chip ${state.connected ? '' : 'warn'}`}>
              {state.connected ? 'live' : 'reconnecting'}
            </span>
            <span className="chip">{health.model}</span>
            {!health.authenticated && <span className="chip warn">no API key</span>}
            {!health.manim && <span className="chip warn">no manim</span>}
          </footer>
        )}
      </aside>

      {drawerOpen && <div className="scrim" onClick={() => setDrawerOpen(false)} />}

      <main className="main">
        <header className="topbar">
          <button className="icon-btn menu" onClick={() => setDrawerOpen(true)} aria-label="Threads">
            ☰
          </button>
          <ProjectPicker
            projects={projects}
            current={project}
            onSelect={(slug) => {
              setProject(slug)
              setActiveTaskId(null)
              setViewing(null)
              setShowGuide(false)
              loaded.current.clear()
            }}
            onCreate={async (name) => {
              const created = await createProject(name)
              setProjects((all) => [...all, created])
              setProject(created.slug)
            }}
          />

          {/* One toggle for what the canvas shows. Icons rather than words so
              the whole header fits a phone; the name lives in `title` for a
              pointer and `aria-label` for everyone else. */}
          <div className="view-toggle" role="tablist" aria-label="Main view">
            {VIEWS.filter((v) => v.key !== 'library' || hasPackages).map((v) => (
              <button
                key={v.key}
                role="tab"
                aria-selected={view === v.key}
                aria-label={v.label}
                title={v.label}
                className={view === v.key ? 'on' : ''}
                onClick={() => setView(v.key)}
              >
                <span aria-hidden="true">{v.icon}</span>
              </button>
            ))}
          </div>
          <span className="topbar-title">
            {VIEWS.find((v) => v.key === view && v.key !== 'chat')?.label ??
              threads.find((t) => t.id === threadId)?.title ??
              'dex'}
          </span>
          <button
            className="ghost-btn small icon-only"
            onClick={() => setShowGuide(true)}
            // `title` is the hover description on a pointer; `aria-label` is
            // what a screen reader and a touch device get, where hover does
            // not exist. Both are needed — neither covers the other.
            title="Guide — this project's AGENTS.md"
            aria-label="Guide — this project's AGENTS.md"
          >
            <span aria-hidden="true">📖</span>
          </button>
          <SettingsMenu
            autoApprove={state.autoApprove}
            onOpenCosts={() => setShowCosts(true)}
            onAutoApprove={(enabled) => {
              // Optimistic: the server echoes the change back over the stream.
              dispatch({ type: 'autoApprove', enabled })
              setAutoApprove(enabled).catch((err: Error) => {
                dispatch({ type: 'autoApprove', enabled: !enabled })
                setFatal(err.message)
              })
            }}
          />
        </header>

        {view === 'library' && project ? (
          <Suspense fallback={<p className="muted" style={{ padding: 16 }}>Loading library…</p>}>
            <PackageLibrary
              key={project}
              project={project}
              liveTags={state.tags[project]}
              onOpen={setViewing}
              openPath={viewing?.kind === 'asset' ? viewing.path : undefined}
            />
          </Suspense>
        ) : view === 'feed' ? (
          <Suspense fallback={<p className="muted" style={{ padding: 16 }}>Loading feed…</p>}>
            <Feed key={project ?? 'none'} project={project ?? undefined} onOpen={setViewing} />
          </Suspense>
        ) : (
          <>
        {threadError && (
          <div className="load-error">
            Could not load this conversation: {threadError}
            <button className="ghost-btn small" onClick={() => setThreadId((id) => id)}>
              Retry
            </button>
          </div>
        )}

        <Chat
          messages={messages}
          threadId={threadId}
          tasks={state.tasks}
          busy={planning}
          onConfirm={(tasks) => void confirm(tasks)}
          // An archived task leaves the thread entirely and a restart resets
          // its row, neither of which the event stream can express as a patch.
          onActed={() => {
            if (threadId) void loadThread(threadId)
          }}
          onOpenTask={(id) => {
            setActiveTaskId(id)
            setViewing(null)
          }}
          activeTaskId={activeTaskId ?? undefined}
        />
          </>
        )}

        {view === 'chat' && (
          <Composer onSend={(text) => void send(text)} disabled={planning || !threadId} />
        )}
      </main>

      {activeTask && (
        <TaskPanel
          view={activeTask}
          onOpen={setViewing}
          openPath={viewing?.kind === 'asset' ? viewing.path : undefined}
          onBack={() => setActiveTaskId(null)}
          onQueued={(taskId, messages) => dispatch({ type: 'queued', taskId, messages })}
          onStarted={(task) => {
            dispatch({ type: 'tasks', tasks: [task] })
            setActiveTaskId(task.id)
            setViewing(null)
          }}
          onClose={() => {
            setActiveTaskId(null)
            setViewing(null)
          }}
        />
      )}

      {viewing && !showCosts && !showGuide && (
        <Suspense fallback={null}>
          <Viewer
            target={viewing}
            onClose={() => {
              setViewing(null)
              setCameFrom(null)
            }}
            // One level of back is all this needs: package -> asset -> package.
            onBack={() => {
              setViewing(cameFrom)
              setCameFrom(null)
            }}
            onOpen={(target) => {
              setCameFrom(viewing)
              setViewing(target)
            }}
          />
        </Suspense>
      )}

      {confirmHide && (
        <div className="modal-scrim" onClick={() => setConfirmHide(null)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-hide-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="confirm-hide-title">Hide this thread?</h2>
            <p className="muted small">
              <strong>{confirmHide.title}</strong> will be taken out of the list. Nothing
              is deleted — its messages and its {confirmHide.taskIds.length} task
              {confirmHide.taskIds.length === 1 ? '' : 's'} stay exactly where they are.
            </p>
            <div className="modal-actions">
              <button className="ghost-btn" onClick={() => setConfirmHide(null)}>
                Cancel
              </button>
              <button
                className="ghost-btn accent"
                onClick={() => void hideThread(confirmHide.id)}
              >
                Hide
              </button>
            </div>
          </div>
        </div>
      )}

      {showGuide && project && (
        <Suspense fallback={null}>
          <GuideEditor project={project} onClose={() => setShowGuide(false)} />
        </Suspense>
      )}

      {showCosts && (
        <Suspense fallback={null}>
          <CostDashboard onClose={() => setShowCosts(false)} />
        </Suspense>
      )}
    </div>
  )
}
