import { Fragment, useEffect, useRef, useState } from 'react'
import type { PlannedTask, SourceAnchor, Task, TaskState, ThreadMessage } from '../types'
import { actOnTasks } from '../api'
import { ErrorNote } from './ErrorNote'
import { Prose } from './Prose'
import type { TaskView } from '../store'
import { progressOf } from '../store'

/** Task states that mean dex has not finished with it yet. */
/** A slug is kebab-case, so only `-` needs care; escape defensively anyway. */
const escapeForSlug = (slug: string) => slug.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const ACTIVE_STATES = new Set<TaskState>(['queued', 'running', 'awaiting_input', 'paused'])

/** A glance at what a source is, before it is worth opening the pane for. */
function iconFor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'pdf') return '\u{1F4C4}'
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) return '\u{1F4DD}'
  if (['xls', 'xlsx', 'xlsm', 'ods', 'csv', 'tsv'].includes(ext)) return '\u{1F4CA}'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif'].includes(ext)) return '\u{1F5BC}'
  if (['mp4', 'webm', 'mov'].includes(ext)) return '\u{1F3AC}'
  if (['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aac'].includes(ext)) return '\u{1F50A}'
  if (['zip', 'tar', 'gz', '7z'].includes(ext)) return '\u{1F4E6}'
  return '\u{1F4CE}'
}

/** The conversation: what you asked, what dex proposed, what it started. */
export function Chat({
  messages,
  tasks,
  busy,
  threadId,
  onConfirm,
  onOpenTask,
  onOpenSource,
  onActed,
  activeTaskId,
}: {
  messages: ThreadMessage[]
  tasks: Record<string, TaskView>
  busy: boolean
  /** Changing this counts as opening a new conversation. */
  threadId: string | null
  onConfirm: (tasks: PlannedTask[]) => void
  onOpenTask: (taskId: string) => void
  /**
   * Opens an attached source in the preview pane. The anchor is given when
   * the click came from a plan row rather than from the chip on a message,
   * and it is what makes the pane land on the right page.
   */
  onOpenSource?: (name: string, anchor?: SourceAnchor) => void
  /** Called after a pause / restart / archive, so the thread reloads. */
  onActed?: () => void
  activeTaskId?: string
}) {
  const bottom = useRef<HTMLDivElement>(null)


  // Tasks this thread started that have not finished. `paused` counts: dex
  // stopped it to make room and will pick it up again, so the thread is still
  // waiting on something.
  // Chips shown in "Running now" are moved there, not copied: rendering them
  // in both places showed the same task advancing twice.
  const working = Object.values(tasks)
    .filter((v) => v.task.threadId === threadId && ACTIVE_STATES.has(v.task.state))
    .sort((a, b) => {
      // Running first — those are the ones worth looking at — then whatever is
      // waiting, each group oldest first so chips do not jump around.
      const rank = (s: TaskState) => (s === 'running' ? 0 : s === 'awaiting_input' ? 1 : 2)
      return rank(a.task.state) - rank(b.task.state) || a.task.createdAt - b.task.createdAt
    })
  const movedToBottom = new Set(working.map((v) => v.task.id))

  // On open, jump straight to the end without animating through the history;
  // after that, follow new messages smoothly.
  const opened = useRef(false)
  useEffect(() => {
    if (!bottom.current) return
    bottom.current.scrollIntoView({ behavior: opened.current ? 'smooth' : 'auto', block: 'end' })
    opened.current = true
  }, [messages, busy, working.length])

  // A different thread is a fresh open, so jump again rather than animate.
  useEffect(() => {
    opened.current = false
  }, [threadId])

  // Any slug that already has a task is off the table: selecting it again would
  // just create a duplicate package beside the one being built. The task itself
  // is kept, not just its slug, so the plan can tell a finished one from a
  // failed one — a failure is worth offering again, a success is not.
  // A re-run is a new task with a new slug, so a plan row cannot be judged by
  // the one task that happens to carry its slug: the first attempt may have
  // been cancelled while its re-run succeeded. Walk each task back to the
  // attempt that started the family and judge the family as a whole.
  const byId = new Map(Object.values(tasks).map((v) => [v.task.id, v.task]))
  const familyOf = (task: Task): string => {
    let root = task
    for (let hop = 0; hop < 20 && root.parentId; hop += 1) {
      const parent = byId.get(root.parentId)
      if (!parent) break
      root = parent
    }
    return root.outputSlug || root.slug
  }
  const family = new Map<string, Task[]>()
  for (const view of Object.values(tasks)) {
    const key = familyOf(view.task)
    family.set(key, [...(family.get(key) ?? []), view.task])
  }
  const startedSlugs = new Set(family.keys())

  // What each plan row actually produced. A task's slug is not the slug the
  // plan asked for — a collision makes the server append a suffix, so a row for
  // `x-narrate-2` can come back as `x-narrate-2-2` — and looking the row up by
  // its own slug then found nothing and offered to run it all over again.
  // Recorded on the message when the plan was confirmed, so it survives a
  // reload; messages written before that fall back to the slug itself.
  const producedBy = new Map<string, Task[]>()
  for (const message of messages) {
    if (message.kind !== 'tasks') continue
    for (const entry of (message.data.tasks ?? []) as (Task & { plannedSlug?: string })[]) {
      const planned = entry.plannedSlug
      if (!planned) continue
      const live = tasks[entry.id]?.task ?? entry
      producedBy.set(planned, [...(producedBy.get(planned) ?? []), live])
    }
  }

  const bySlug = new Map(Object.values(tasks).map((v) => [v.task.slug, v.task]))

  /** What a planned slug has come to, across every attempt at it. */
  const planState = (slug: string): 'done' | 'running' | 'failed' | 'new' => {
    // The tasks this row started. Three ways to find them, because a task's
    // slug, the directory it writes into, and the slug the plan asked for are
    // three different things whenever a row edits an existing package.
    // All three, unioned rather than tried in turn. A row for
    // `delete-middle-node` finds a failed `delete-middle-node`, but the
    // `delete-middle-node-2` that succeeded is a separate task with no
    // `parentId` linking them — stopping at the first hit read the row as
    // failed and offered to build a package that already exists.
    const suffixed = new RegExp(`^${escapeForSlug(slug)}-\\d+$`)
    const roots = [
      // 1. Recorded when the plan was confirmed: exact, and survives a reload.
      ...(producedBy.get(slug) ?? []),
      // 2. A task carrying this row's slug. The family map is keyed by output
      //    directory, so an editing task — whose directory is the package it
      //    revises, not its own name — is not found under its own slug there.
      ...(bySlug.has(slug) ? [bySlug.get(slug)!] : []),
      // 3. `slugify` resolves a collision by appending `-2`, `-3`: a task whose
      //    slug is this row's plus exactly that suffix is another go at it.
      ...Object.values(tasks)
        .map((v) => v.task)
        .filter((t) => suffixed.test(t.slug)),
    ]

    // Judge the whole family of each: a first attempt may have been cancelled
    // while its re-run succeeded.
    const attempts = new Map<string, Task>()
    for (const task of [...roots, ...(family.get(slug) ?? [])]) {
      for (const member of family.get(task.outputSlug || task.slug) ?? [task]) {
        attempts.set(member.id, member)
      }
    }

    const all = [...attempts.values()]
    if (!all.length) return 'new'
    if (all.some((t) => t.state === 'succeeded')) return 'done'
    const over = ['failed', 'cancelled']
    if (all.every((t) => over.includes(t.state))) return 'failed'
    // Anything still queued, running, or paused counts as submitted: it is in
    // the queue already, and offering it again would duplicate the work.
    return 'running'
  }

  return (
    <div className="chat">
      {messages.length === 0 && (
        <div className="empty">
          <h2>What should dex build?</h2>
          <p className="muted">
            Name one or more interview problems. dex splits them into parallel tasks and asks you
            to confirm before it starts. Each task produces solutions, a test suite it actually
            runs, an explanation with mermaid diagrams, and manim animations.
          </p>
        </div>
      )}

      {messages.map((message) => (
        <Message
          key={message.id}
          message={message}
          onActed={onActed}
          movedToBottom={movedToBottom}
          tasks={tasks}
          startedSlugs={startedSlugs}
          planState={planState}
          onConfirm={onConfirm}
          onOpenTask={onOpenTask}
          onOpenSource={onOpenSource}
          activeTaskId={activeTaskId}
        />
      ))}

      {/* What is happening now, gathered at the end. A task chip also sits up
          in the history where it was started, but in a thread with hundreds of
          them that is nowhere near the bottom of the scroll. */}
      {working.length > 0 && (
        <div className="started now-running">
          <div className="card-kind">
            Running now · {working.length}
          </div>
          <TaskChipGroups
            views={working}
            activeTaskId={activeTaskId}
            onOpenTask={onOpenTask}
            onActed={onActed}
          />
        </div>
      )}

      {(busy || working.length > 0) && (
        <div className="chat-status">
          <span className="pulse" />
          {busy
            ? 'planning…'
            : `${working.length} task${working.length === 1 ? '' : 's'} running…`}
        </div>
      )}
      <div ref={bottom} />
    </div>
  )
}

function Message({
  message,
  onActed,
  tasks,
  movedToBottom,
  startedSlugs,
  planState,
  onConfirm,
  onOpenTask,
  onOpenSource,
  activeTaskId,
}: {
  message: ThreadMessage
  onActed?: () => void
  tasks: Record<string, TaskView>
  /** Task ids gathered into "Running now"; history must not repeat them. */
  movedToBottom: Set<string>
  startedSlugs: Set<string>
  planState: (slug: string) => 'done' | 'running' | 'failed' | 'new'
  onConfirm: (tasks: PlannedTask[]) => void
  onOpenTask: (taskId: string) => void
  onOpenSource?: (name: string, anchor?: SourceAnchor) => void
  activeTaskId?: string
}) {
  if (message.role === 'user') {
    // Files attached to what was typed. Shown under the bubble rather than
    // inside it: the text is what was said, and the files are what came with
    // it — and until this existed an upload left no trace in the thread at all.
    const attached = message.data?.attachments ?? []
    return (
      <div className="bubble-group">
        <div className="bubble user">{message.text}</div>
        {attached.length > 0 && (
          <div className="bubble-sources">
            {attached.map((name) => (
              <button
                key={name}
                className="source-chip"
                title={`Preview ${name}`}
                onClick={() => onOpenSource?.(name)}
              >
                <span className="source-chip-icon" aria-hidden="true">{iconFor(name)}</span>
                <span className="source-chip-name">{name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    )
  }
  if (message.kind === 'error') {
    return <ErrorNote text={message.text} detail={message.data?.detail as string | undefined} />
  }

  // A design thread replies in prose; so does a chat thread when it has a
  // question rather than a plan. Both are markdown.
  if (message.kind === 'text') {
    // A design turn runs as a task, but posts no chip — so once it stops being
    // live there was nothing left to click and its activity became
    // unreachable. The reply carries the id of the run that wrote it, which is
    // the way back in.
    const from = message.data?.taskId as string | undefined
    const failed = message.data?.failed === true
    // A survey reply is the reasoning the plan beneath it was built on, not an
    // answer in its own right; it is set apart so it reads that way.
    const surveyed = message.data?.survey != null
    return (
      <div
        className={`prose ${message.data?.guideChanged ? 'guide-changed' : ''} ${
          failed ? 'turn-failed' : ''
        } ${surveyed ? 'from-survey' : ''}`}
      >
        {surveyed && <span className="card-kind">Surveyed the attached sources</span>}
        {message.data?.guideChanged === true && (
          <span className="card-kind">AGENTS.md updated</span>
        )}
        <Prose text={message.text} />
        {from && (
          <button
            className={`turn-activity ${activeTaskId === from ? 'on' : ''}`}
            onClick={() => onOpenTask(from)}
          >
            {failed ? 'What went wrong' : 'Activity'}
          </button>
        )}
      </div>
    )
  }

  if (message.kind === 'plan') {
    return (
      <PlanCard
        text={message.text}
        tasks={(message.data.tasks ?? []) as PlannedTask[]}
        startedSlugs={startedSlugs}
        planState={planState}
        onConfirm={onConfirm}
        onOpenAnchor={(anchor) => onOpenSource?.(anchor.source, anchor)}
      />
    )
  }

  if (message.kind === 'tasks') {
    const started = ((message.data.tasks ?? []) as Task[]).filter(
      (task) => !movedToBottom.has(task.id),
    )
    // Everything this batch started is live and gathered at the end of the
    // thread. Keep the sentence — it is still what happened here — but say
    // where the chips went rather than leaving an empty gap.
    const allMoved = started.length === 0 && (message.data.tasks ?? []).length > 0
    return (
      <div className="started">
        <div className="muted small">
          {message.text}
          {allMoved && ' Still running — see below.'}
        </div>
        <TaskChipGroups
          // Prefer live state from the stream; fall back to the snapshot
          // stored in the thread, which is all that survives a restart.
          views={started.map((task) => tasks[task.id] ?? { task, feed: [], assets: [] })}
          activeTaskId={activeTaskId}
          onOpenTask={onOpenTask}
          onActed={onActed}
        />
      </div>
    )
  }

  return <Prose text={message.text} />
}

function PlanCard({
  text,
  tasks,
  startedSlugs,
  planState,
  onConfirm,
  onOpenAnchor,
}: {
  text: string
  tasks: PlannedTask[]
  /** Slugs already queued or finished; they cannot be started twice. */
  startedSlugs: Set<string>
  /** What each planned slug has come to across every attempt at it. */
  planState: (slug: string) => 'done' | 'running' | 'failed' | 'new'
  onConfirm: (tasks: PlannedTask[]) => void
  /** Opens the source at a task's anchor, in the preview pane. */
  onOpenAnchor?: (anchor: SourceAnchor) => void
}) {
  const [dropped, setDropped] = useState<Set<string>>(new Set())
  const [sent, setSent] = useState(false)
  const [showDone, setShowDone] = useState(false)
  const [showAll, setShowAll] = useState(false)
  // Rows still open to a decision. A done or running one is locked, so it is
  // neither counted nor touched by the select-all toggle.
  const selectable = tasks.filter((t) => ['new', 'failed'].includes(planState(t.slug)))
  const chosen = selectable.filter((t) => !dropped.has(t.slug))
  const noneChosen = chosen.length === 0

  // Finished work is history; it folds away. What stays is what still needs a
  // decision: never run, or run and failed — a failure is worth another go.
  const done = tasks.filter((t) => planState(t.slug) === 'done')
  const open = tasks.filter((t) => planState(t.slug) !== 'done')
  const collapsed = done.length > 0 && !showDone

  const toggle = (slug: string) =>
    setDropped((current) => {
      const next = new Set(current)
      next.has(slug) ? next.delete(slug) : next.add(slug)
      return next
    })

  return (
    <div className="plan-card">
      <div className="card-kind">
        Plan · {tasks.length} task{tasks.length === 1 ? '' : 's'}
        {tasks.length > 1 && ' in parallel'}
      </div>
      {/* The reasoning matters while there is still something to decide; once
          the work is done it is just noise above the result. */}
      {text && !collapsed && <p className="muted small">{text}</p>}

      {done.length > 0 && (
        <button className="plan-done-toggle" onClick={() => setShowDone((v) => !v)}>
          {showDone ? 'Hide' : 'Show'} {done.length} task{done.length === 1 ? '' : 's'} done
        </button>
      )}

      {(() => {
        const rows = showDone ? tasks : open
        // A thirty-row plan is most of a screen on its own, and a thread holds
        // many of them. One row stands for the plan; the rest are a click away.
        const listed = showAll ? rows : rows.slice(0, 1)
        return (
          <>
            {listed.map((task, index) => {
              // A failed attempt can be chosen again; a finished one cannot.
              const state = planState(task.slug)
              const locked = state === 'done' || state === 'running'
              return (
                <Fragment key={task.slug}>
                  <PlanRow
                    task={task}
                    disabled={sent || locked}
                    started={locked}
                    checked={!dropped.has(task.slug)}
                    onToggle={() => toggle(task.slug)}
                    onOpenAnchor={onOpenAnchor}
                  />
                  {/* A thirty-row plan has the same problem as a long chip
                      group: the way back is otherwise only at the bottom. */}
                  {showAll && index < listed.length - 1 && (
                    <button className="more-toggle inline" onClick={() => setShowAll(false)}>
                      Show less
                    </button>
                  )}
                </Fragment>
              )
            })}
            {rows.length > 1 && (
              <button className="more-toggle" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show less' : `+${rows.length - 1} more`}
              </button>
            )}
          </>
        )
      })()}
      {/* Keyed off `selectable`, not `chosen`: deselecting everything used to
          take the whole row away with it, and with it the only way back. */}
      {!sent && selectable.length > 0 && (
        <div className="actions">
          <button
            className="primary"
            disabled={noneChosen}
            onClick={() => {
              setSent(true)
              onConfirm(chosen)
            }}
          >
            Run {chosen.length} task{chosen.length === 1 ? '' : 's'}
          </button>
          {/* Everything starts selected, so this reads "Deselect all" first and
              flips once there is nothing left to run. */}
          {selectable.length > 1 && (
            <button
              className="plan-select-toggle"
              onClick={() =>
                setDropped(noneChosen ? new Set() : new Set(selectable.map((t) => t.slug)))
              }
            >
              {noneChosen ? 'Select all' : 'Deselect all'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Chips grouped by what they are doing, one visible per group.
 *
 * A thread that started three hundred tasks rendered three hundred chips, and
 * the scroll became unusable. The name of one task in a group says more than
 * a wall of them, and the count says the rest; clicking opens the group.
 */
const STATUS_RANK: Record<string, number> = {
  running: 0, awaiting_input: 1, queued: 2, paused: 3,
  failed: 4, cancelled: 5, succeeded: 6,
}

const STATUS_LABEL: Record<string, string> = {
  running: 'running',
  awaiting_input: 'waiting on you',
  queued: 'queued',
  paused: 'paused',
  failed: 'failed',
  cancelled: 'cancelled',
  succeeded: 'done',
}

function TaskChipGroups({
  views,
  activeTaskId,
  onOpenTask,
  onActed,
}: {
  views: TaskView[]
  activeTaskId?: string
  onOpenTask: (id: string) => void
  /** Called after an action lands, so the thread reflects it at once. */
  onActed?: () => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const groups = new Map<string, TaskView[]>()
  for (const view of views) {
    const state = view.task.state as string
    groups.set(state, [...(groups.get(state) ?? []), view])
  }
  const ordered = [...groups.entries()].sort(
    ([a], [b]) => (STATUS_RANK[a] ?? 9) - (STATUS_RANK[b] ?? 9),
  )

  const toggle = (state: string) =>
    setExpanded((current) => {
      const next = new Set(current)
      next.has(state) ? next.delete(state) : next.add(state)
      return next
    })

  return (
    <div className="task-groups">
      {ordered.map(([state, members]) => {
        // A group of one is already as short as it can be; a toggle on it
        // would be chrome around a single chip.
        const open = expanded.has(state) || members.length === 1
        const shown = open ? members : members.slice(0, 1)
        const label = STATUS_LABEL[state] ?? state
        return (
          <div className="task-group" key={state}>
            <div className="task-chips">
              {shown.map((view, index) => (
                <Fragment key={view.task.id}>
                  <TaskChip
                    view={view}
                    active={view.task.id === activeTaskId}
                    onClick={() => onOpenTask(view.task.id)}
                    // Collapsed, one chip stands for the group and its
                    // controls act on all of it. Expanded, each chip is only
                    // itself — the same button must not quietly mean more
                    // depending on a fold.
                    actsOn={open ? [view.task] : members.map((m) => m.task)}
                    onActed={onActed}
                  />
                  {/* Between every chip, not only under the last. Twenty-eight
                      expanded chips run past the bottom of the screen, and a
                      single closing control means scrolling to the end to undo
                      a click made at the start. */}
                  {open && index < shown.length - 1 && (
                    <button className="more-toggle inline" onClick={() => toggle(state)}>
                      Show less
                    </button>
                  )}
                </Fragment>
              ))}
            </div>
            {members.length > 1 && (
              <button className="more-toggle" onClick={() => toggle(state)}>
                {open
                  ? `Show less · ${members.length} ${label}`
                  : `+${members.length - 1} more ${label}`}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Drawn rather than typed: an emoji is the font's idea of the glyph, which
 * varies by platform and never matches the weight of the text beside it.
 * These inherit `currentColor` and the button's size.
 */
function Icon({ name }: { name: ActionName }) {
  const common = {
    width: 14, height: 14, viewBox: '0 0 16 16', 'aria-hidden': true,
    fill: 'none', stroke: 'currentColor', strokeWidth: 1.6,
    strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  }
  if (name === 'pause') {
    return (
      <svg {...common}>
        <path d="M6 3v10M10 3v10" />
      </svg>
    )
  }
  if (name === 'resume') {
    return (
      <svg {...common} fill="currentColor" stroke="none">
        <path d="M5 3.2 12.5 8 5 12.8Z" />
      </svg>
    )
  }
  if (name === 'restart') {
    // An arc that stops short of a full circle, with the head on the open end:
    // a closed ring reads as "loading", not "go again".
    return (
      <svg {...common}>
        <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.9" />
        <path d="M13.4 2.4v2.9h-2.9" />
      </svg>
    )
  }
  // A lid across the top and a body below, with the pull-slot that tells an
  // archive box from a plain rectangle.
  return (
    <svg {...common}>
      <rect x="2" y="2.75" width="12" height="3" rx="0.9" />
      <path d="M3.2 5.75v6.4a1.1 1.1 0 0 0 1.1 1.1h7.4a1.1 1.1 0 0 0 1.1-1.1v-6.4" />
      <path d="M6.5 8.5h3" />
    </svg>
  )
}

/** What each control does, in the words the confirmation uses. */
const ACTIONS = {
  pause: { label: 'Pause', confirm: null as string | null },
  resume: { label: 'Resume', confirm: null as string | null },
  restart: {
    label: 'Restart',
    confirm:
      'Restart discards what the agent worked out and runs the same brief from ' +
      'the beginning, re-reading the project guide as it is now. Anything the ' +
      'task already wrote stays on disk.',
  },
  archive: {
    label: 'Archive',
    confirm:
      'Archiving takes these out of the thread and out of every listing. ' +
      'Nothing they built is deleted — the packages stay in the Library.',
  },
} as const

type ActionName = keyof typeof ACTIONS

/**
 * Pause, restart and archive, for one task or for a whole collapsed group.
 *
 * `tasks` is the set the action applies to: the single chip when the group is
 * open, every member when one chip is standing in for many. Doing otherwise
 * would make the same button mean different things depending on a fold.
 */
function TaskActions({
  tasks,
  onDone,
}: {
  tasks: Task[]
  onDone: () => void
}) {
  const [pending, setPending] = useState<ActionName | null>(null)
  const [busy, setBusy] = useState(false)

  // Pause and Resume are the same slot: a task is either on its way or waiting
  // to go again, never both. In a mixed group whichever is the more common
  // wins, so the button does something for most of what it covers.
  const canPause = tasks.filter((t) => t.canPause).length
  const canResume = tasks.filter((t) => t.canContinue).length
  const goStop: ActionName[] =
    canPause === 0 && canResume === 0
      ? []
      : canResume > canPause
        ? ['resume']
        : ['pause']
  const available: ActionName[] = [
    ...goStop,
    // Not offered for an archived task: archiving is a decision to stop, and
    // the server refuses a restart on one, so the button would do nothing.
    // Re-running it is still possible from the task's own ↻.
    ...(tasks.some((t) => t.canRestart) ? (['restart'] as const) : []),
    ...(tasks.some((t) => t.canArchive) ? (['archive'] as const) : []),
  ]
  if (!tasks.length) return null

  const run = async (action: ActionName) => {
    setBusy(true)
    try {
      await actOnTasks(tasks.map((t) => t.id), action)
      onDone()
    } finally {
      setBusy(false)
      setPending(null)
    }
  }

  return (
    <>
      <span className="chip-actions">
        {available.map((name) => (
          <button
            key={name}
            className="chip-action"
            title={`${ACTIONS[name].label}${tasks.length > 1 ? ` all ${tasks.length}` : ''}`}
            aria-label={`${ACTIONS[name].label}${tasks.length > 1 ? ` all ${tasks.length} tasks` : ''}`}
            disabled={busy}
            onClick={(e) => {
              // The chip itself opens the task; these must not.
              e.stopPropagation()
              if (ACTIONS[name].confirm) setPending(name)
              else void run(name)
            }}
          >
            <Icon name={name} />
          </button>
        ))}
      </span>
      {pending && (
        <ConfirmAction
          action={pending}
          count={tasks.length}
          busy={busy}
          onCancel={() => setPending(null)}
          onConfirm={() => void run(pending)}
        />
      )}
    </>
  )
}

function ConfirmAction({
  action,
  count,
  busy,
  onCancel,
  onConfirm,
}: {
  action: ActionName
  count: number
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const { label, confirm } = ACTIONS[action]
  return (
    <div className="modal-scrim" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>
          {label} {count === 1 ? 'this task' : `all ${count} tasks`}?
        </h2>
        <p className="muted small">{confirm}</p>
        <div className="modal-actions">
          <button className="ghost-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="primary" onClick={onConfirm} disabled={busy}>
            {busy ? '…' : label}
          </button>
        </div>
      </div>
    </div>
  )
}

function TaskChip({
  view,
  active,
  onClick,
  actsOn,
  onActed,
}: {
  view: TaskView
  active: boolean
  onClick: () => void
  /** Every task the controls apply to: this one, or the group it stands for. */
  actsOn?: Task[]
  onActed?: () => void
}) {
  const { pct } = progressOf(view)
  // The chip and its controls are siblings rather than nested: a button inside
  // a button is invalid, and clicking a control must not also open the task.
  return (
    <div className={`chip-row ${active ? 'active' : ''}`}>
      <button className={`task-chip ${view.task.state}`} onClick={onClick}>
        <span className={`state-dot ${view.task.state}`} />
        <span className="chip-title">{view.task.title}</span>
        <span className="chip-state">
          {view.task.costUsd ? `$${view.task.costUsd.toFixed(2)} · ` : ''}
          {view.task.activity ?? view.task.state.replace('_', ' ')}
        </span>
        <span className="chip-progress" style={{ width: `${pct}%` }} />
      </button>
      {actsOn && actsOn.length > 0 && <TaskActions tasks={actsOn} onDone={onActed ?? (() => {})} />}
    </div>
  )
}

function PlanRow({
  task,
  checked,
  disabled,
  started,
  onToggle,
  onOpenAnchor,
}: {
  task: PlannedTask
  checked: boolean
  disabled: boolean
  started: boolean
  onToggle: () => void
  onOpenAnchor?: (anchor: SourceAnchor) => void
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className={`plan-row ${!checked ? 'off' : ''} ${started ? 'started' : ''}`}>
      <label className="plan-pick">
        <input type="checkbox" checked={checked && !started} disabled={disabled} onChange={onToggle} />
      </label>
      <div className="plan-body">
        <span className="plan-title">
          {task.title}
          {started && <span className="plan-badge">already started</span>}
        </span>
        <span className="plan-slug">{task.slug}</span>
        {/* Where in the material this task's work is. It is a button rather
            than a label because the whole reason the survey records an anchor
            is so the operator can check it before choosing the task. */}
        {task.anchor && (
          <button
            className="plan-anchor"
            title={`Open ${task.anchor.source} at ${task.anchor.label}`}
            onClick={() => task.anchor && onOpenAnchor?.(task.anchor)}
          >
            <span aria-hidden="true">📍</span> {task.anchor.label}
          </button>
        )}
        <p className={`plan-problem ${expanded ? 'open' : ''}`}>{task.problem}</p>
        <button className="plan-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show less' : 'Show full problem'}
        </button>
      </div>
    </div>
  )
}
