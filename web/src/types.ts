/** Mirrors the server's event and resource shapes (see `src/dex/models.py`). */

export type TaskState =
  | 'queued' | 'running' | 'awaiting_input'
  | 'succeeded' | 'failed' | 'cancelled'
  /** Stopped mid-run — by dex making room, or by the server dying under it.
      Either way it goes again on its own. */
  | 'paused'
  /** Taken out of every listing, with everything it built left on disk. */
  | 'archived'

export type Task = {
  id: string
  threadId: string | null
  title: string
  slug: string
  problem: string
  state: TaskState
  activity: string | null
  createdAt: number
  startedAt: number | null
  finishedAt: number | null
  error: string | null
  costUsd: number | null
  turns: number | null
  outputDir: string
  artifacts: string[]
  pending: string[]
  attempt: number
  parentId: string | null
  sessionId: string | null
  resumedFrom: string | null
  outputSlug: string
  project: string | null
  model: string | null
  /** True while costUsd is a running estimate, not the agent's own total. */
  costIsEstimate: boolean
  canResume: boolean
  canRerun: boolean
  canRestart: boolean
  canPause: boolean
  /** Stopped short — paused, failed or cancelled — so it can go again. */
  canContinue: boolean
  canArchive: boolean
  /**
   * Where in the operator's own material this task's work came from, when a
   * survey anchored it there. Shown in the Files tab above what the task
   * produced: the input first, then a divider, then the output.
   */
  anchor?: SourceAnchor | null
}

/**
 * Where in an attached source a task's work is, when a survey found it. Kept
 * in the source's own terms — a PDF has pages, a document has lines — because
 * flattening them to one number reads as nothing to the person clicking it.
 */
export type SourceAnchor = {
  source: string
  label: string
  page?: number
  endPage?: number
  line?: number
  endLine?: number
  heading?: string
  sheet?: string
  url?: string
}

export type PlannedTask = {
  title: string
  problem: string
  slug: string
  anchor?: SourceAnchor | null
}

/** What the pre-planning pass found in the attached material. */
export type SurveySegment = {
  title: string
  summary: string
  extent: string
  anchor: SourceAnchor | null
}

export type Survey = {
  segments: SurveySegment[]
  overview: string
  gaps: string
  single: boolean
}

export type Plan = {
  tasks: PlannedTask[]
  notes: string
  needsClarification: string
  /** Concrete answers offered with the question, as buttons. */
  options?: string[]
}

export type ThreadMessage = {
  id: string
  role: 'user' | 'dex'
  kind: 'text' | 'plan' | 'tasks' | 'error'
  text: string
  data: Partial<Plan> & {
    tasks?: (PlannedTask | Task)[]
    guideChanged?: boolean
    project?: string
    /**
     * The run that wrote this message, when one did. A design turn is a task
     * with no chip in the thread, so this is the only way back to its
     * activity once it has finished.
     */
    taskId?: string
    /** Whether that run failed, so the link says so. */
    failed?: boolean
    /** The full failure, when this message is an error; the body is the summary. */
    detail?: string
    /** Which plan in a sequence this is, when one request needed several. */
    batch?: number
    /** The survey this reply reports, when a pre-planning pass produced it. */
    survey?: Survey
    /**
     * Files attached to this message, by name in the project's `datasets/`
     * directory. The transcript keeps the names only — the agent was given the
     * absolute paths, but showing a wall of them to a reader is noise.
     */
    attachments?: string[]
  }
  ts: number
}

/** Files stored in a project's `datasets/` directory, and where they went. */
export type UploadBatch = {
  project: string
  directory: string
  files: { name: string; path: string; bytes: number }[]
}

export type ThreadKind = 'chat' | 'project_design'

export type ThreadSummary = {
  id: string
  title: string
  project: string | null
  kind: ThreadKind
  createdAt: number
  updatedAt: number
  messageCount: number
  taskIds: string[]
  /** Sum of what the tasks this thread started have cost. */
  costUsd?: number
}

export type Thread = ThreadSummary & {
  messages: ThreadMessage[]
  /** A chat call is in flight for this thread, started by whoever. */
  planning?: boolean
}

type Base = { seq: number; ts: number; taskId: string | null }

export type DexEvent = Base &
  (
    | { type: 'settings'; settings: Settings }
    | { type: 'cost'; costUsd: number; estimate: boolean }
    | { type: 'task_message'; message: TaskMessage }
    | { type: 'task_message_delivered'; count: number; startedTaskId: string }
    | { type: 'thread_message'; threadId: string; message: ThreadMessage }
    | { type: 'task_created'; task: Task }
    | { type: 'task_state'; state: TaskState; activity: string | null; error?: string }
    | { type: 'text'; text: string }
    | { type: 'text_delta'; id: string; delta: string }
    | { type: 'thinking'; text: string }
    | { type: 'thinking_delta'; id: string; delta: string }
    | { type: 'block_end'; id: string }
    | { type: 'tool'; id: string; name: string; title: string; input: Record<string, string>; activity?: string }
    | { type: 'tool_result'; id: string; ok: boolean; output: string }
    | { type: 'diff'; id: string; tool: string; path: string; patch: string; additions: number; deletions: number }
    | { type: 'asset'; path: string; kind: AssetKind; bytes: number; change: string }
    | { type: 'tags'; project: string; slug: string; tags: string[]; removed: boolean }
    | { type: 'question'; id: string; question: string; options: string[] }
    | { type: 'question_answered'; id: string; answer: string; auto?: boolean }
    | { type: 'approval'; id: string; tool: string; title: string; input: Record<string, string>; auto?: boolean }
    | { type: 'approval_resolved'; id: string; decision: string; auto?: boolean }
    | { type: 'result'; ok: boolean; durationMs: number; turns: number; costUsd: number; summary: string }
    | { type: 'error'; message: string; fatal?: boolean }
    | { type: 'thread_busy'; threadId: string; busy: boolean }
  )

export type AssetKind =
  | 'code' | 'markdown' | 'manifest' | 'animation' | 'image' | 'video'
  /** Narration and its captions, produced alongside an animation. */
  | 'audio' | 'captions'
  | 'file'

export type AssetEntry = { name: string; dir: boolean; kind: AssetKind; bytes: number }

export type AssetResponse =
  | { kind: 'dir'; path: string; entries: AssetEntry[] }
  | { kind: 'file'; path: string; ext: string; content: string }
  /**
   * The server refused to send it as text. Not a failure: a `.mid` or a `.wav`
   * is opened by playing it or by handing it to a widget, both of which fetch
   * the bytes from `/api/assets/raw` instead.
   */
  | { kind: 'binary'; path: string }

export type Health = {
  ok: boolean
  workspace: string
  assetsDir: string
  model: string
  concurrency: number
  manim: boolean
  autoApprove: boolean
  authenticated: boolean
  running: number
  queued: number
}

/** What dex knows about how much of a Claude limit is gone. */
export type LimitStatus = {
  /** Whether dex is holding work because of it. */
  paused: boolean
  /** 0–1 of the nearest window, or null when no run has reported yet. */
  utilization: number | null
  /** Whether the CLI gave a number, or the figure was inferred from a status. */
  measured: boolean
  /** 'allowed' | 'allowed_warning' | 'rejected', as the CLI last reported. */
  status: string | null
  window: string | null
  resetsAt: number | null
  /** The fraction at which dex stops handing out work. */
  pauseAt: number
  /** Whether an operator decision is standing in for the automatic one. */
  overridden: boolean
  /** The last usage probe: when it ran, and what it cost. */
  probe: { at: number; costUsd: number | null; model: string } | null
}

export type Settings = {
  auto_approve: boolean
  /** Generation work is held; chats and planning still run. */
  paused: boolean
  task_concurrency: number
  chat_concurrency: number
  /** Tasks may ask about promoting a helper into a project's utils/. */
  utility_proposals: boolean
  model: string | null
  /** How hard a task thinks before acting; null defers to the deployment. */
  effort: string | null
  animation_speed: number
  [projectModel: string]: unknown
}

export type CostTotals = { day: number; week: number; month: number; all_time: number }

export type ModelChoice = { id: string; label: string; note: string }

export type SettingsResponse = {
  settings: Settings
  costs: CostTotals
  tokens: TokenTotals
  models: ModelChoice[]
  efforts: ModelChoice[]
  project: string
  defaultModel: string
  defaultEffort: string
  running: number
  planning: number
  /** Tasks currently parked, whether by a pause or by chat pressure. */
  paused: number
  limit: LimitStatus
}

export type CostPoint = { bucket: string; series: string; cost: number; tasks: number }

export type CostsResponse = {
  totals: CostTotals
  series: CostPoint[]
  period: string
  granularity: string
  group: string
}

export type TaskMessage = {
  id: string
  body: string
  createdAt: number
  delivered: boolean
}

export type Topic = {
  slug: string
  title: string
  summary: string | null
  animations: string[]
  solutions: string | null
  tests: string | null
  explanation: string | null
  manifest: { approaches?: { name: string; time: string; space: string }[] } | null
  animation: { frames: number; duration: number; width: number; height: number } | null
  /**
   * Every animation the package has, in order, with how long each runs. A
   * package holds one per approach and the reel plays them in sequence.
   * `duration` is null when it could not be measured.
   */
  clips: { path: string; name: string; duration: number | null }[]
  seenCount: number
  due: boolean
  lastSeenAt: number | null
}

export type FeedResponse = { project: string; projects: string[]; topics: Topic[] }

export type AnimationCheckpoint = {
  index: number
  name: string
  startFrame: number
  endFrame: number
  duration: number
  /** Seconds into the animation; how a video seeks and loops a section. */
  start?: number
  end?: number
}

export type AnimationInfo = {
  /** `video` seeks and loops natively; `gif` is re-encoded server-side. */
  kind?: 'video' | 'gif'
  animation: { frames?: number; duration: number; width?: number; height?: number } | null
  checkpoints: AnimationCheckpoint[]
  /** True when the checkpoints came from the scene's own sections. */
  named: boolean
  speeds: number[]
  defaultSpeed: number
}

export type Project = {
  slug: string
  name: string
  description: string
  createdAt: number
  updatedAt: number
  threads: number
  tasks: number
}

export type ProjectsResponse = { projects: Project[]; default: string }

export type GuideResponse = { project: Project; path: string; text: string }

export type DesignReply = { summary: string; changed: boolean }

/** One generated package in a project, as the library lists it. */
export type PackageEntry = {
  slug: string
  displayName: string
  files: number
  /** The package directory, relative to the assets root. */
  path: string
  /** From the package's manifest. Empty for one written before it had tags. */
  tags: string[]
}

/** A tag and how many packages carry it, for the Library's filter pills. */
export type TagCount = {
  name: string
  count: number
}

/**
 * Tokens across every task, with thinking split out.
 *
 * `thinking` is the part of `output` the model spent thinking, not an extra
 * beside it — `thinking + visible === output`. Adding thinking to output
 * double-counts every thought.
 */
export type TokenTotals = {
  input: number
  cache_read: number
  cache_write: number
  output: number
  thinking: number
  visible: number
  /** Tasks whose tokens were recorded, vs. those with only a cost. */
  counted: number
  with_cost: number
}


/** One version of a skill: what it carries, and when it was published. */
export type SkillVersion = {
  name: string
  description: string
  /** Short content hash; it changes whenever anything in the skill does. */
  version: string
  /** Seconds since the epoch, when that version was published. */
  updated: number
  /** Python packages its utils import, beyond the standard library. */
  requires: string[]
  modules: string[]
  widgets: string[]
}

/**
 * A capability, with every version of it on disk.
 *
 * One entry per *name*: an install that has published a skill ten times has
 * one capability, not ten, and the versions are a dropdown rather than ten
 * rows of clutter.
 */
export type Skill = {
  name: string
  description: string
  /** The newest version — what the dropdown opens on. */
  current: string
  versions: SkillVersion[]
  /** Which version each project is on, keyed by project slug. */
  projects: Record<string, string>
}

export type SkillsResponse = { projects: string[]; skills: Skill[] }
