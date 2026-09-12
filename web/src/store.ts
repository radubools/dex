import type { DexEvent, Task, TaskMessage, ThreadMessage } from './types'

/** One entry in a task's activity feed. */
export type FeedItem =
  | { kind: 'text'; id: string; text: string; streaming?: boolean }
  | { kind: 'thinking'; id: string; text: string; streaming?: boolean }
  | { kind: 'tool'; id: string; name: string; title: string; status: 'running' | 'ok' | 'error'; output?: string }
  | { kind: 'diff'; id: string; path: string; patch: string; additions: number; deletions: number }
  | { kind: 'question'; id: string; question: string; options: string[]; answer?: string; auto?: boolean }
  | { kind: 'approval'; id: string; title: string; tool: string; decision?: string; auto?: boolean }
  | { kind: 'result'; id: string; ok: boolean; turns: number; costUsd: number; summary: string }
  | { kind: 'error'; id: string; message: string }
  /** Something the operator typed into the task, and whether it has been sent. */
  | { kind: 'note'; id: string; body: string; delivered: boolean }

export type TaskView = {
  task: Task
  feed: FeedItem[]
  /** Follow-up notes waiting for this task to stop. */
  queued: TaskMessage[]
  /** Assets seen for this task, newest first, as `<slug>/<file>` paths. */
  assets: string[]
}

export type State = {
  connected: boolean
  /** Mirrors the server's global settings; updated over the event stream. */
  autoApprove: boolean
  tasks: Record<string, TaskView>
  /** Thread messages arriving over the stream, keyed by thread. */
  threadMessages: Record<string, ThreadMessage[]>
  /**
   * Package tags as they change on disk: project -> slug -> tags. The Library
   * loads a snapshot and then follows this, so a tag a task writes becomes a
   * filter pill without a reload.
   */
  tags: Record<string, Record<string, string[]>>
}

export const initialState: State = {
  connected: false,
  autoApprove: false,
  tasks: {},
  threadMessages: {},
  tags: {},
}

export type Action =
  | { type: 'event'; event: DexEvent }
  | { type: 'connection'; up: boolean }
  | { type: 'tasks'; tasks: Task[] }
  | { type: 'messages'; threadId: string; messages: ThreadMessage[] }
  | { type: 'autoApprove'; enabled: boolean }
  | { type: 'queued'; taskId: string; messages: TaskMessage[] }
  /** A task's stored history, read when it is opened. */
  | { type: 'taskEvents'; events: DexEvent[] }

export function reduce(state: State, action: Action): State {
  switch (action.type) {
    case 'connection':
      return { ...state, connected: action.up }

    case 'tasks': {
      const tasks = { ...state.tasks }
      for (const task of action.tasks) {
        tasks[task.id] = tasks[task.id]
          ? { ...tasks[task.id], task }
          : { task, feed: [], assets: [], queued: [] }
      }
      return { ...state, tasks }
    }

    case 'messages':
      return {
        ...state,
        threadMessages: { ...state.threadMessages, [action.threadId]: action.messages },
      }

    case 'autoApprove':
      return { ...state, autoApprove: action.enabled }

    case 'queued': {
      const view = state.tasks[action.taskId]
      if (!view) return state
      return {
        ...state,
        tasks: { ...state.tasks, [action.taskId]: { ...view, queued: action.messages } },
      }
    }

    case 'taskEvents':
      // Folded through the same path a live event takes, so history and the
      // stream cannot render differently.
      return action.events.reduce(applyEvent, state)

    case 'event':
      return applyEvent(state, action.event)
  }
}

function applyEvent(state: State, event: DexEvent): State {
  if (event.type === 'thread_message') {
    const existing = state.threadMessages[event.threadId] ?? []
    // The sender already has this message from the POST response.
    if (existing.some((m) => m.id === event.message.id)) return state
    return {
      ...state,
      threadMessages: { ...state.threadMessages, [event.threadId]: [...existing, event.message] },
    }
  }

  if (event.type === 'tags') {
    const forProject = { ...(state.tags[event.project] ?? {}) }
    // A package whose manifest is gone stops filtering the library; recording
    // it as an empty tag list would leave it counted as "known, untagged",
    // which is a different thing from absent.
    if (event.removed) delete forProject[event.slug]
    else forProject[event.slug] = event.tags
    return { ...state, tags: { ...state.tags, [event.project]: forProject } }
  }

  if (event.type === 'settings') {
    return { ...state, autoApprove: Boolean(event.settings.auto_approve) }
  }

  if (event.type === 'task_created') {
    return {
      ...state,
      tasks: {
        ...state.tasks,
        [event.task.id]: { task: event.task, feed: [], assets: [], queued: [] },
      },
    }
  }

  const taskId = event.taskId
  if (!taskId) return state
  const view = state.tasks[taskId]
  // An event for a task this client has not loaded: the task list refresh that
  // follows fills it in, so drop it rather than inventing a placeholder.
  if (!view) return state

  const next = (changes: Partial<TaskView>): State => ({
    ...state,
    tasks: { ...state.tasks, [taskId]: { ...view, ...changes } },
  })
  const append = (item: FeedItem) => next({ feed: [...view.feed, item] })

  switch (event.type) {
    case 'task_state':
      return next({
        task: {
          ...view.task,
          state: event.state,
          activity: event.activity,
          error: event.error ?? view.task.error,
        },
      })

    case 'text':
      return append({ kind: 'text', id: `t${event.seq}`, text: event.text })

    case 'thinking':
      return append({ kind: 'thinking', id: `k${event.seq}`, text: event.text })

    // Streamed deltas accumulate into one feed item per content block.
    case 'text_delta':
    case 'thinking_delta': {
      const kind = event.type === 'text_delta' ? 'text' : 'thinking'
      const existing = view.feed.find((i) => i.id === event.id && i.kind === kind)
      if (existing) {
        return next({
          feed: view.feed.map((i) =>
            i === existing ? { ...i, text: (i as { text: string }).text + event.delta } : i,
          ),
        })
      }
      return append({ kind, id: event.id, text: event.delta, streaming: true } as FeedItem)
    }

    case 'block_end':
      return next({
        feed: view.feed.map((i) =>
          i.id === event.id && (i.kind === 'text' || i.kind === 'thinking')
            ? { ...i, streaming: false }
            : i,
        ),
      })

    case 'tool':
      // The tool event carries the activity, so the status line updates when a
      // tool starts rather than only at the next state transition.
      return {
        ...next({ task: { ...view.task, activity: event.activity ?? view.task.activity } }),
        tasks: {
          ...state.tasks,
          [taskId]: {
            ...view,
            task: { ...view.task, activity: event.activity ?? view.task.activity },
            feed: [
              ...view.feed,
              { kind: 'tool', id: event.id, name: event.name, title: event.title, status: 'running' },
            ],
          },
        },
      }

    case 'tool_result':
      return next({
        feed: view.feed.map((item) =>
          item.kind === 'tool' && item.id === event.id
            ? { ...item, status: event.ok ? 'ok' : 'error', output: event.output }
            : item,
        ),
      })

    case 'diff':
      return append({
        kind: 'diff',
        id: `${event.id}-${event.seq}`,
        path: event.path,
        patch: event.patch,
        additions: event.additions,
        deletions: event.deletions,
      })

    case 'question':
      return append({ kind: 'question', id: event.id, question: event.question, options: event.options })

    case 'question_answered':
      return next({
        feed: view.feed.map((item) =>
          item.kind === 'question' && item.id === event.id
            ? { ...item, answer: event.answer, auto: event.auto }
            : item,
        ),
      })

    case 'approval':
      return append({
        kind: 'approval', id: event.id, title: event.title, tool: event.tool,
        // An auto-approved call is shown as a record of what ran, not a prompt.
        decision: event.auto ? 'allow' : undefined,
        auto: event.auto,
      })

    case 'approval_resolved':
      return next({
        feed: view.feed.map((item) =>
          item.kind === 'approval' && item.id === event.id
            ? { ...item, decision: event.decision, auto: item.auto || event.auto }
            : item,
        ),
      })

    case 'asset':
      // A file an agent writes and then removes must leave the list, or the
      // count outlives the file and the tab claims more than the directory has.
      if (event.change === 'deleted') {
        return next({ assets: view.assets.filter((a) => a !== event.path) })
      }
      return next({
        assets: view.assets.includes(event.path) ? view.assets : [event.path, ...view.assets],
      })

    case 'result':
      return append({
        kind: 'result',
        id: `r${event.seq}`,
        ok: event.ok,
        turns: event.turns,
        costUsd: event.costUsd,
        summary: event.summary,
      })

    // Cost ticks up while the task runs; it is not a feed entry.
    case 'cost':
      return next({
        task: { ...view.task, costUsd: event.costUsd, costIsEstimate: event.estimate },
      })

    case 'task_message': {
      if (view.feed.some((i) => i.kind === 'note' && i.id === event.message.id)) return state
      const delivered = Boolean(event.message.delivered)
      // Always a feed entry, so what was typed stays visible after it is sent —
      // otherwise an instantly-delivered note appears and vanishes. A note
      // echoed onto a follow-up arrives already delivered, so it is not queued.
      return next({
        queued: delivered ? view.queued : [...view.queued, event.message],
        feed: [
          ...view.feed,
          { kind: 'note', id: event.message.id, body: event.message.body, delivered },
        ],
      })
    }

    case 'task_message_delivered':
      return next({
        queued: [],
        feed: view.feed.map((i) => (i.kind === 'note' ? { ...i, delivered: true } : i)),
      })

    case 'error':
      return append({ kind: 'error', id: `e${event.seq}`, message: event.message })

    default:
      return state
  }
}

/** Coarse progress for a task, used by the progress bar and task chips. */
export function progressOf(view: TaskView): { pct: number; label: string } {
  const { task } = view
  if (task.state === 'succeeded') return { pct: 100, label: 'done' }
  if (task.state === 'failed' || task.state === 'cancelled') return { pct: 100, label: task.state }
  if (task.state === 'queued') return { pct: 0, label: 'queued' }
  // Paused keeps its progress: it is going to carry on by itself.
  if (task.state === 'paused') return { pct: milestonePct(view), label: 'paused for chat' }
  // Interrupted keeps the partial bar: how far it got is what decides whether
  // resuming is worth it.


  // There is no true percentage to report, so progress is anchored to the
  // artifacts a finished package must contain — that is what is being waited on.
  return { pct: milestonePct(view), label: task.activity ?? task.state }
}

/** Anchored to the artifacts a finished package must contain. */
function milestonePct(view: TaskView): number {
  const milestones = ['solutions.py', 'test_solutions.py', 'explanation.md', '.gif', 'manifest.json']
  const done = milestones.filter((m) => view.assets.some((a) => a.endsWith(m) || a.includes(m))).length
  return Math.min(95, 8 + (done / milestones.length) * 87)
}
