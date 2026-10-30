import type { DexEvent, Task, TaskState, ThreadSummary } from '../types'

/**
 * Why a notification wants the operator: a question (a task's, or the
 * planner's), a plan waiting to be started, an approval nobody granted
 * automatically, or a root task or a thread's run of them finishing.
 */
export type Reason = 'question' | 'plan' | 'approval' | 'finished'

export type Notice = {
  id: string
  /** What it is about. One notice per key: a replayed event cannot raise a second. */
  key: string
  reason: Reason
  /** The task's or the thread's title. */
  title: string
  /** The question, the approval, or what finished. */
  detail: string
  taskId: string | null
  threadId: string | null
  project: string | null
  /** Milliseconds since the epoch. */
  createdAt: number
  /** Set only by clicking the notice. */
  read: boolean
  /** The thing it asked for was handled somewhere else; it stays listed. */
  resolved: boolean
  /** A finished run in which something failed. */
  failed?: boolean
}

export type Effect =
  | { type: 'add'; notice: Notice }
  | { type: 'resolve'; key: string }

type Known = {
  id: string
  title: string
  threadId: string | null
  parentId: string | null
  project: string | null
  state: TaskState
}

const ACTIVE: TaskState[] = ['todo', 'queued', 'running', 'awaiting_input', 'paused', 'blocked']
const TERMINAL: TaskState[] = ['succeeded', 'failed', 'cancelled']
const isActive = (s: TaskState | undefined) => s !== undefined && ACTIVE.includes(s)
const isTerminal = (s: TaskState | undefined) => s !== undefined && TERMINAL.includes(s)

/**
 * Turns dex's event stream into notices.
 *
 * It keeps its own small mirror of every task's state, because what matters is
 * a *transition*: a task parking on a question, or the last running task of a
 * thread stopping. A question is announced when its task parks
 * (`awaiting_input`), not when it is asked, because dex answers some questions
 * itself straight away (the utility question, a reused answer) and those never
 * park. An approval marked `auto` was granted by auto-approve and is never
 * announced.
 *
 * Only root tasks (no `parentId`) finish out loud, and a thread's root tasks
 * finish as one: the notice comes when the last of them stops, and says how
 * many ran and how many failed. A child step finishing says nothing.
 *
 * Events older than `since` (the stream replays its recent history on
 * connect) update the mirror but raise nothing.
 */
export class Detector {
  private tasks = new Map<string, Known>()
  private threads = new Map<string, { title: string; project: string | null }>()
  /** The ask a task is about to park on, by task. */
  private asks = new Map<string, { reason: 'question' | 'approval'; id: string; text: string }>()
  /** The notice a parked task raised, so its answer can resolve it. */
  private parked = new Map<string, string>()
  /** Plans and planner questions still open, by thread: notice keys. */
  private threadOpen = new Map<string, { plans: string[]; questions: string[] }>()
  /** Root tasks of one group that were active during its current run. */
  private batches = new Map<string, Set<string>>()
  private seq = 0
  private seen = new WeakSet<Task>()

  constructor(
    private since: number,
    private now: () => number = Date.now,
  ) {}

  /** Learn tasks from a listing. Never raises: a snapshot is not a transition. */
  seedTasks(tasks: Task[]) {
    for (const task of tasks) {
      // The app hands over the whole map on every change; most of it is the
      // same objects as last time.
      if (this.seen.has(task)) continue
      this.seen.add(task)
      const known = this.tasks.get(task.id)
      // The mirror's state came from the stream and is newer than a listing.
      this.remember(task, known?.state ?? task.state)
    }
  }

  /** Learn thread titles and projects, for notices about a thread. */
  nameThreads(threads: ThreadSummary[]) {
    for (const t of threads) this.threads.set(t.id, { title: t.title, project: t.project })
  }

  observe(event: DexEvent): Effect[] {
    const live = event.ts * 1000 >= this.since
    const effects = this.apply(event)
    return live ? effects : effects.filter((e) => e.type === 'resolve')
  }

  private apply(event: DexEvent): Effect[] {
    switch (event.type) {
      case 'task_created':
        this.remember(event.task, event.task.state)
        return []

      case 'thread_message':
        return this.threadMessage(event.threadId, event.message)

      case 'question':
        if (event.taskId) this.asks.set(event.taskId, { reason: 'question', id: event.id, text: event.question })
        return []

      case 'approval':
        // Auto-approve granted it; nobody needs to hear about it.
        if (event.taskId && !event.auto) {
          this.asks.set(event.taskId, { reason: 'approval', id: event.id, text: event.title })
        }
        return []

      case 'question_answered':
      case 'approval_resolved': {
        if (!event.taskId) return []
        if (this.asks.get(event.taskId)?.id === event.id) this.asks.delete(event.taskId)
        return this.unpark(event.taskId)
      }

      case 'task_state':
        return event.taskId ? this.taskState(event.taskId, event.state, event.activity, event.ts) : []

      default:
        return []
    }
  }

  private remember(task: Task, state: TaskState) {
    const known: Known = {
      id: task.id,
      title: task.title,
      threadId: task.threadId,
      parentId: task.parentId,
      project: task.project,
      state,
    }
    this.tasks.set(task.id, known)
    if (!known.parentId && isActive(state)) this.batch(known).add(task.id)
  }

  private group(task: Known) {
    return task.threadId ? `thread:${task.threadId}` : `task:${task.id}`
  }

  private batch(task: Known) {
    const key = this.group(task)
    let batch = this.batches.get(key)
    if (!batch) this.batches.set(key, (batch = new Set()))
    return batch
  }

  private notice(fields: Omit<Notice, 'id' | 'createdAt' | 'read' | 'resolved'>): Effect {
    this.seq += 1
    const createdAt = this.now()
    return {
      type: 'add',
      notice: { ...fields, id: `n${createdAt.toString(36)}-${this.seq}`, createdAt, read: false, resolved: false },
    }
  }

  private unpark(taskId: string): Effect[] {
    const key = this.parked.get(taskId)
    if (!key) return []
    this.parked.delete(taskId)
    return [{ type: 'resolve', key }]
  }

  private taskState(taskId: string, state: TaskState, activity: string | null, ts: number): Effect[] {
    const known: Known = this.tasks.get(taskId) ?? {
      // An event for a task no listing has carried: it is still worth telling.
      id: taskId, title: 'A task', threadId: null, parentId: null, project: null, state: 'running',
    }
    const previous = known.state
    known.state = state
    this.tasks.set(taskId, known)
    const effects: Effect[] = []

    if (previous === 'awaiting_input' && state !== 'awaiting_input') effects.push(...this.unpark(taskId))

    if (state === 'awaiting_input' && previous !== 'awaiting_input') {
      const ask = this.asks.get(taskId)
      // Parked without the ask in view (it was before this page loaded): the
      // activity line says which kind it is.
      const reason = ask?.reason ?? (activity?.includes('approval') ? 'approval' : 'question')
      const key = `ask:${taskId}:${ask?.id ?? ts}`
      this.parked.set(taskId, key)
      effects.push(
        this.notice({
          key, reason, title: known.title,
          detail: ask?.text ?? activity ?? 'Waiting on you',
          taskId, threadId: known.threadId, project: known.project,
        }),
      )
    }

    if (known.parentId) return effects

    if (isActive(state)) this.batch(known).add(taskId)

    if (isTerminal(state) && !isTerminal(previous)) {
      const batch = this.batch(known)
      batch.add(taskId)
      const group = this.group(known)
      const stillGoing = [...this.tasks.values()].some(
        (t) => !t.parentId && t.id !== taskId && this.group(t) === group && isActive(t.state),
      )
      if (stillGoing) return effects
      this.batches.delete(group)
      const members = [...batch].map((id) => this.tasks.get(id)).filter((t): t is Known => Boolean(t))
      const failed = members.filter((t) => t.state === 'failed').length
      const cancelled = members.filter((t) => t.state === 'cancelled').length
      // Stopping everything yourself is not news.
      if (cancelled === members.length) return effects
      const thread = known.threadId ? this.threads.get(known.threadId) : undefined
      const many = members.length > 1
      effects.push(
        this.notice({
          key: `done:${group}:${taskId}:${ts}`,
          reason: 'finished',
          title: many ? (thread?.title ?? known.title) : known.title,
          detail: summary(members.length, failed, cancelled),
          // One task: open it. Several: the thread is where they all are.
          taskId: many ? null : taskId,
          threadId: known.threadId,
          project: known.project ?? thread?.project ?? null,
          failed: failed > 0,
        }),
      )
    }
    return effects
  }

  private threadMessage(
    threadId: string,
    message: Extract<DexEvent, { type: 'thread_message' }>['message'],
  ): Effect[] {
    const open = this.threadOpen.get(threadId) ?? { plans: [], questions: [] }
    this.threadOpen.set(threadId, open)
    const thread = this.threads.get(threadId)
    const base = { taskId: null, threadId, project: thread?.project ?? message.data?.project ?? null }

    // The operator replied: the planner's question is answered.
    if (message.role === 'user') {
      const effects = open.questions.map((key): Effect => ({ type: 'resolve', key }))
      open.questions = []
      return effects
    }
    // A plan was started: every plan on this thread has had its decision.
    if (message.kind === 'tasks') {
      const effects = open.plans.map((key): Effect => ({ type: 'resolve', key }))
      open.plans = []
      return effects
    }
    if (message.kind === 'plan' && (message.data?.tasks?.length ?? 0) > 0) {
      const key = `plan:${message.id}`
      open.plans.push(key)
      const n = message.data.tasks!.length
      return [this.notice({
        ...base, key, reason: 'plan',
        title: thread?.title ?? 'A plan',
        detail: `${n} task${n === 1 ? '' : 's'} planned. Review and start them.`,
      })]
    }
    if (message.kind === 'text' && message.data?.needsClarification) {
      const key = `ask:${message.id}`
      open.questions.push(key)
      return [this.notice({
        ...base, key, reason: 'question',
        title: thread?.title ?? 'The planner',
        detail: message.data.needsClarification,
      })]
    }
    return []
  }
}

function summary(total: number, failed: number, cancelled: number): string {
  const head = total === 1 ? 'Finished' : `All ${total} tasks finished`
  const parts = [failed && `${failed} failed`, cancelled && `${cancelled} cancelled`].filter(Boolean)
  return parts.length ? `${head}: ${parts.join(', ')}` : total === 1 ? 'Finished successfully' : head
}
