import type {
  AssetResponse, CostTotals, CostsResponse, DexEvent, Health, Plan, PlannedTask,
  AnimationInfo, FeedResponse, GuideResponse, Project, ProjectsResponse, Settings,
  SettingsResponse, Task, TaskMessage, Thread, ThreadSummary, DesignReply,
  PackageEntry, TagCount, UploadBatch, SkillsResponse, SkillVersion, TaskGraph,
} from './types'

/**
 * The server requires no auth by default. When `DEX_TOKEN` is set it arrives as
 * `#t=…`, which is stored and stripped from the address bar.
 */
function readToken(): string {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get('t')
  if (fromHash) {
    sessionStorage.setItem('dex.token', fromHash)
    history.replaceState(null, '', location.pathname + location.search)
    return fromHash
  }
  return sessionStorage.getItem('dex.token') ?? ''
}

export const token = readToken()

/** Raised when the server says who you are is the problem, not the request. */
export class AuthError extends Error {
  constructor(
    // 428 is "your password must be replaced before anything else works". A
    // separate code from 403 because the two need opposite UI: one says ask an
    // admin, the other says fill in this one form.
    readonly status: 401 | 403 | 428,
    message: string,
  ) {
    super(message)
    this.name = 'AuthError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    // Sessions are cookies, so they ride along without being named here. The
    // token header stays for a private install that uses DEX_TOKEN instead.
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-dex-token': token, ...init?.headers },
  })
  // Separated from other failures so the app can show a sign-in page or an
  // unauthorised notice instead of a generic fatal error, which is what every
  // request would have rendered before sign-in existed.
  if (response.status === 401 || response.status === 403 || response.status === 428) {
    const detail = await response.text().catch(() => '')
    throw new AuthError(response.status as 401 | 403 | 428, safeDetail(detail) || String(response.status))
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? `${response.status}: ${safeDetail(detail)}` : `${response.status}`)
  }
  return response.json() as Promise<T>
}

function safeDetail(raw: string): string {
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed.detail === 'string' ? parsed.detail : raw
  } catch {
    return raw.slice(0, 300)
  }
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const getHealth = () => request<Health>('/api/health')

export const listProjects = () => request<ProjectsResponse>('/api/projects')

export const createProject = (name: string, description = '') =>
  post<{ project: Project }>('/api/projects', { name, description }).then((r) => r.project)

export const deleteProject = (slug: string) =>
  request<{ ok: boolean }>(`/api/projects/${slug}`, { method: 'DELETE' })

export const getGuide = (slug: string) => request<GuideResponse>(`/api/projects/${slug}/guide`)

export const putGuide = (slug: string, text: string) =>
  request<{ ok: boolean; text: string }>(`/api/projects/${slug}/guide`, {
    method: 'PUT',
    body: JSON.stringify({ text }),
  })

export const listThreads = (project?: string) =>
  request<{ project: string; threads: ThreadSummary[] }>(
    `/api/threads${project ? `?project=${encodeURIComponent(project)}` : ''}`,
  ).then((r) => r.threads)

export const createThread = (project?: string) =>
  post<{ thread: Thread }>('/api/threads', { project }).then((r) => r.thread)

export const getThread = (id: string) =>
  request<{ thread: Thread; tasks: Task[] }>(`/api/threads/${id}`)

/**
 * Records that the reader opened this project or thread: both lists lead with
 * what they opened last. A thread's view counts for its project too.
 */
export const markProjectViewed = (slug: string) =>
  post<{ ok: boolean }>(`/api/projects/${encodeURIComponent(slug)}/viewed`)

export const markThreadViewed = (id: string) => post<{ ok: boolean }>(`/api/threads/${id}/viewed`)

export const deleteThread = (id: string) =>
  request<{ ok: boolean }>(`/api/threads/${id}`, { method: 'DELETE' })

/**
 * Says something in a thread. A chat thread answers with a plan to confirm; a
 * design thread answers by redrafting the project's guide.
 */
export const sendMessage = (threadId: string, text: string, uploads: string[] = []) =>
  post<{ plan?: Plan; design?: DesignReply }>(`/api/threads/${threadId}/messages`, {
    text,
    uploads,
  })

export const confirmPlan = (threadId: string, tasks: PlannedTask[], uploads: string[] = []) =>
  post<{ tasks: Task[] }>('/api/chat/confirm', {
    tasks,
    thread_id: threadId,
    // The files were attached to the request, so every task the planner split
    // it into gets them.
    uploads,
  }).then((r) => r.tasks)

export const getSettings = () => request<SettingsResponse>('/api/settings')

/** Persisted globally; the server applies changes to runs already in flight. */
export const putSettings = (patch: Record<string, unknown>) =>
  request<{ settings: Settings; costs: CostTotals; released: number }>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(patch),
  })

export const setAutoApprove = (auto_approve: boolean) => putSettings({ auto_approve })

/** Hold or release every generation task. Chats and planning keep running. */
export const setPaused = (paused: boolean) => putSettings({ paused })

/** Override dex's own hold when a Claude limit is nearly spent. */
export const setLimitPaused = (limit_paused: boolean) => putSettings({ limit_paused })

export const getAnimationInfo = (path: string) =>
  request<AnimationInfo>(`/api/assets/animation?path=${encodeURIComponent(path)}`)

/** The animation re-timed, and optionally sliced to one checkpoint. */
export const animationUrl = (path: string, speed?: number, segment?: number) => {
  const params = new URLSearchParams({ path })
  if (speed !== undefined) params.set('speed', String(speed))
  if (segment !== undefined) params.set('segment', String(segment))
  if (token) params.set('t', token)
  return `/api/assets/animation/play?${params}`
}

export const getFeed = (project?: string) =>
  request<FeedResponse>(`/api/feed${project ? `?project=${encodeURIComponent(project)}` : ''}`)

/** Records that a topic was shown and reschedules it. */
export const reviewTopic = (slug: string, rating: 'again' | 'good' | 'easy', project?: string) =>
  post<{ review: unknown }>(
    `/api/feed/${encodeURIComponent(slug)}/reviewed${project ? `?project=${encodeURIComponent(project)}` : ''}`,
    { rating },
  )

/** The project's live task graph: what runs, what waits, and on what (design/18). */
export const getTaskGraph = (project: string) =>
  request<TaskGraph>(`/api/dag?project=${encodeURIComponent(project)}`)

export const getCosts = (period: string, granularity: string, group: string, projects: string[] = [], offset = 0) =>
  request<CostsResponse>(
    `/api/costs?period=${period}&granularity=${granularity}&group=${group}&offset=${offset}` +
      (projects.length ? `&projects=${encodeURIComponent(projects.join(','))}` : ''),
  )

/**
 * One task by id, for opening a run the listing did not carry.
 *
 * `listTasks` returns the newest 200, so anything older is absent from the
 * task map — and a finished design turn is reached by id, from the reply it
 * wrote, long after 200 newer tasks have pushed it out.
 */
export const getTask = (id: string) =>
  request<{ task: Task }>(`/api/tasks/${encodeURIComponent(id)}`).then((r) => r.task)

export const listTasks = () => request<{ tasks: Task[] }>('/api/tasks').then((r) => r.tasks)

export const getTaskMessages = (id: string) =>
  request<{ messages: TaskMessage[] }>(`/api/tasks/${id}/messages`).then((r) => r.messages)

/** Held while the task runs; delivered as the brief for its next attempt. */
export const sendTaskMessage = (id: string, text: string, uploads: string[] = []) =>
  post<{ message: TaskMessage }>(`/api/tasks/${id}/messages`, { text, uploads })

/**
 * Store files in the project's `datasets/` directory, for tasks to read and
 * write. A message then names them, and the server re-checks each name against
 * that directory rather than trusting it. `content-type` is left unset on
 * purpose — the browser has to set the multipart boundary itself.
 */
export const uploadSources = async (
  files: File[],
  project?: string,
): Promise<UploadBatch> => {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  const query = project ? `?project=${encodeURIComponent(project)}` : ''
  const response = await fetch(`/api/uploads${query}`, {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    headers: { 'x-dex-token': token },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? safeDetail(detail) : `upload failed (${response.status})`)
  }
  return response.json() as Promise<UploadBatch>
}

/** Every skill, with its version and the projects it is enabled for. */
export const listSkills = () => request<SkillsResponse>('/api/skills')

/** Turn one skill on or off for one project; the server materialises. */
export const setSkillEnabled = (
  name: string,
  version: string,
  project: string,
  on: boolean,
) =>
  request<{ project: string; skills: Record<string, string> }>(
    `/api/skills/${encodeURIComponent(name)}/projects/${encodeURIComponent(project)}` +
      `?version=${encodeURIComponent(version)}`,
    { method: on ? 'POST' : 'DELETE', body: '{}' },
  )

/** The skills one project has on, for the design thread's picker. */
export const projectSkills = (slug: string) =>
  request<{ skills: SkillVersion[] }>(`/api/projects/${slug}/skills`)

export const getSkillDoc = (name: string, version: string) =>
  request<{ name: string; version: string; path: string; text: string }>(
    `/api/skills/${encodeURIComponent(name)}/doc?version=${encodeURIComponent(version)}`,
  )

/**
 * Edit a skill's instructions. The server forks and publishes rather than
 * writing in place, so this comes back with a *new* version and the projects
 * it moved.
 */
export const putSkillDoc = (name: string, version: string, text: string) =>
  request<{ ok: boolean; version: string; moved: string[]; text: string }>(
    `/api/skills/${encodeURIComponent(name)}/doc?version=${encodeURIComponent(version)}`,
    { method: 'PUT', body: JSON.stringify({ text }) },
  )

/**
 * Download one version of a skill as a `.tar.gz`.
 *
 * Fetched rather than linked, because the route needs the auth header — and
 * the filename comes from the server's `content-disposition` so the file on
 * disk says which version it is without the browser having to be told twice.
 */
export const exportSkill = async (name: string, version: string): Promise<void> => {
  const response = await fetch(
    `/api/skills/${encodeURIComponent(name)}/export?version=${encodeURIComponent(version)}`,
    { credentials: 'same-origin', headers: { 'x-dex-token': token } },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? safeDetail(detail) : `export failed (${response.status})`)
  }
  const disposition = response.headers.get('content-disposition') ?? ''
  const named = /filename="([^"]+)"/.exec(disposition)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = named ? named[1] : `${name}@${version}.tar.gz`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * Take an exported skill onto this install as a new version.
 *
 * Resolved by the name inside the archive, so it joins the skill it belongs
 * to. It is enabled for nothing: `fresh` is false when this install already
 * had that exact content.
 */
export const importSkill = async (
  file: File,
): Promise<{ name: string; version: string; description: string; fresh: boolean }> => {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/skills/import', {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    headers: { 'x-dex-token': token },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? safeDetail(detail) : `import failed (${response.status})`)
  }
  return response.json()
}

export const cancelTask = (id: string) => post<{ ok: boolean }>(`/api/tasks/${id}/cancel`)

/** Move a task, and every task under it in the graph, to the front of the queue. Returns those moved. */
export const prioritizeTask = (id: string) =>
  post<{ tasks: Task[] }>(`/api/tasks/${id}/prioritize`).then((r) => r.tasks)

/** Continue a task that stopped before finishing, in the same output directory. */
export const resumeTask = (id: string) =>
  post<{ task: Task }>(`/api/tasks/${id}/resume`).then((r) => r.task)

/**
 * Continue a task as itself rather than as a child of itself. For a run that
 * did not really fail — one the server killed mid-flight — so the thread keeps
 * one chip and one running total for one piece of work.
 */
export const resumeTaskInPlace = (id: string) =>
  post<{ task: Task }>(`/api/tasks/${id}/resume?in_place=true`).then((r) => r.task)

/**
 * Pause, restart, or archive tasks. Takes a list because the thread collapses
 * tasks by status: one control can stand for a group of a hundred.
 */
export const actOnTasks = (
  ids: string[],
  action: 'pause' | 'resume' | 'restart' | 'archive',
) =>
  post<{ action: string; tasks: Task[]; skipped: string[] }>('/api/tasks/actions', {
    ids,
    action,
  })

/** Run the same problem again, leaving the earlier package in place. */
export const rerunTask = (id: string) =>
  post<{ task: Task }>(`/api/tasks/${id}/rerun`).then((r) => r.task)

export const answerQuestion = (taskId: string, id: string, answer: string) =>
  post<{ ok: boolean }>(`/api/tasks/${taskId}/answer`, { id, answer })

export const resolveApproval = (taskId: string, id: string, decision: 'allow' | 'deny') =>
  post<{ ok: boolean }>(`/api/tasks/${taskId}/approve`, { id, decision })

/**
 * A 415 from `/api/assets` means the file is binary, which is an answer rather
 * than an error — the viewer then plays it, or hands it to a widget, instead of
 * showing "415: .mid is binary". Anything else still throws.
 */
export const readAsset = (path: string, match?: string): Promise<AssetResponse> =>
  request<AssetResponse>(
    `/api/assets?path=${encodeURIComponent(path)}` +
      (match ? `&match=${encodeURIComponent(match)}` : ''),
  ).catch((error: unknown) => {
    if (error instanceof Error && error.message.startsWith('415')) {
      return { kind: 'binary', path } as AssetResponse
    }
    throw error
  })

/**
 * Write one text file inside the assets root. The server confines the path the
 * way `readAsset` is confined, and the parent directory has to exist already.
 */
export const writeAsset = (path: string, content: string) =>
  request<{ ok: boolean; bytes: number }>('/api/assets', {
    method: 'PUT',
    body: JSON.stringify({ path, content }),
  })

/** Binary assets (the generated GIFs) are loaded by the browser directly. */
/**
 * Every generated package in a project, for the library list.
 *
 * One globbed listing rather than a request per directory: a project with
 * twenty packages would otherwise fire twenty requests every time the tab is
 * opened. Titles come from the tasks that produced them when there is one, so
 * the list reads "Detect A Cycle In A Graph" rather than the slug.
 */
export const listPackages = async (
  project: string,
): Promise<{ packages: PackageEntry[]; tags: TagCount[] }> => {
  // One request: the tags live inside each package's manifest, and reading
  // them in the browser meant a fetch per package.
  try {
    return await request<{ packages: PackageEntry[]; tags: TagCount[] }>(
      `/api/packages?project=${encodeURIComponent(project)}`,
    )
  } catch (err) {
    if (err instanceof Error && err.message.startsWith('404')) return { packages: [], tags: [] }
    throw err
  }
}

/**
 * Everything one task did. The live stream replays only the most recent events
 * across every task, so anything older than that window has to be asked for.
 */
export const getTaskEvents = (id: string) =>
  request<{ taskId: string; events: DexEvent[] }>(`/api/tasks/${id}/events`).then(
    (r) => r.events,
  )

export const assetUrl = (path: string) =>
  `/api/assets/raw?path=${encodeURIComponent(path)}&t=${encodeURIComponent(token)}`

/**
 * A file an operator attached, addressed by project and name rather than by
 * path: `datasets/` is not the assets tree and has no path-shaped identity in
 * the UI. The token rides in the query because an `<iframe>` and an `<img>`
 * send no headers.
 */
export const sourceUrl = (project: string, name: string) =>
  `/api/datasets/raw?project=${encodeURIComponent(project)}&name=${encodeURIComponent(
    name,
  )}&t=${encodeURIComponent(token)}`

/**
 * Subscribes to the server's event stream, reconnecting with the last seen
 * sequence number so nothing is missed across a dropped connection.
 */
export function openStream(onEvent: (e: DexEvent) => void, onStatus: (up: boolean) => void) {
  let source: EventSource | null = null
  let lastSeq = 0
  let retry: ReturnType<typeof setTimeout> | undefined
  let closed = false

  const connect = () => {
    if (closed) return
    source = new EventSource(`/api/events?after=${lastSeq}&t=${encodeURIComponent(token)}`)
    source.onopen = () => onStatus(true)
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as DexEvent
      lastSeq = Math.max(lastSeq, event.seq)
      onEvent(event)
    }
    source.onerror = () => {
      onStatus(false)
      source?.close()
      retry = setTimeout(connect, 1500)
    }
  }

  connect()
  return () => {
    closed = true
    clearTimeout(retry)
    source?.close()
  }
}


// --- identity ---------------------------------------------------------------

export type AuthState =
  | 'open'
  | 'service'
  | 'anonymous'
  | 'unauthorised'
  /** Signed in, has a role, and cannot use it until the password is replaced. */
  | 'password_change'
  | 'authorised'

/** The four roles. No role is `null` — the absence of one, never a value. */
export type Role = 'admin' | 'author' | 'operator' | 'viewer'

/**
 * What a route needs. The server sends the caller's set, and the UI hides what
 * is not in it — a button that only ever answers 403 is worse than no button.
 */
export type Capability = 'view' | 'run_tasks' | 'design' | 'manage_projects' | 'manage_users'

export type Me = {
  state: AuthState
  user: AuthUser | null
  googleEnabled: boolean
  passwordEnabled: boolean
}

export type AuthUser = {
  id: string
  email: string
  name: string | null
  picture: string | null
  role: Role | null
  projects: string[]
  username: string | null
  isAdmin: boolean
  authorised: boolean
  capabilities: Capability[]
  mustChangePassword: boolean
  createdAt: number
  lastSeen: number | null
}

export type AuthConfig = {
  googleEnabled: boolean
  passwordEnabled: boolean
  open: boolean
  roles: { role: Role; description: string }[]
  minPasswordLength: number
}

/** Whether this person may do `capability`. Absent user means no. */
export const can = (user: AuthUser | null | undefined, capability: Capability): boolean =>
  !!user?.capabilities?.includes(capability)

/** Never throws on 401/403: the answer to "who am I" includes "nobody". */
export const getMe = () => request<Me>('/api/auth/me')

export const signInUrl = (next = '/') =>
  `/api/auth/google?next=${encodeURIComponent(next)}`

export const logout = () => request<void>('/api/auth/logout', { method: 'POST' })

export const listUsers = () => request<{ users: AuthUser[] }>('/api/auth/users')

export const getAuthConfig = () => request<AuthConfig>('/api/auth/config')

export const login = (username: string, password: string) =>
  request<{ user: AuthUser }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })

/** Change my own password. The one thing a `password_change` session may do. */
export const changePassword = (current: string, next: string) =>
  request<{ user: AuthUser }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ current, new: next }),
  })

export const createUser = (body: {
  username: string
  password: string
  role: Role | null
  name?: string
  email?: string
  projects?: string[]
}) =>
  request<{ user: AuthUser }>('/api/auth/users', {
    method: 'POST',
    body: JSON.stringify(body),
  })

/** Always temporary: the holder must replace it before dex lets them in. */
export const resetUserPassword = (id: string, password: string) =>
  request<{ user: AuthUser }>(`/api/auth/users/${id}/password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  })

export const setUserRole = (id: string, role: Role | null) =>
  request<{ user: AuthUser }>(`/api/auth/users/${id}/role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  })

export const setUserProjects = (id: string, projects: string[]) =>
  request<{ user: AuthUser }>(`/api/auth/users/${id}/projects`, {
    method: 'PUT',
    body: JSON.stringify({ projects }),
  })

export const deleteUser = (id: string) =>
  request<void>(`/api/auth/users/${id}`, { method: 'DELETE' })


// --- widgets ----------------------------------------------------------------

export type WidgetInfo = {
  name: string
  title: string
  description: string
  version: string
  built: boolean
  /** Versioned, because import()/fetch cache by URL for the life of the page. */
  url: string
}

export type WidgetRule = { widget: string; extensions: string[]; filenames: string[] }

export const listWidgets = (project: string) =>
  request<{ project: string; widgets: WidgetInfo[]; rules: WidgetRule[] }>(
    `/api/widgets?project=${encodeURIComponent(project)}`,
  )

/** Which widget opens this file, or null to use a built-in viewer. */
export const resolveWidget = (path: string) =>
  request<{ widget: WidgetInfo | null }>(
    `/api/widgets/resolve?path=${encodeURIComponent(path)}`,
  )

/** Which of `paths` a widget opens: {path: widget}, only those that have one. */
export const resolveWidgets = (paths: string[]) =>
  post<{ widgets: Record<string, WidgetInfo> }>('/api/widgets/resolve', { paths })
