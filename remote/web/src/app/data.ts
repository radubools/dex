// Everything the application reads and writes. Nothing in the cloud runs code:
// Firestore is written only by local dex (src/dex/remote.py) and is read-only
// here (../../terraform/firestore.rules); requests and answers are published
// straight to the Pub/Sub topic with the person's own Google token;
// attachments and results live in the files bucket (storage.rules).
//
// Firebase itself comes from the shell (`init`), so this bundle imports it
// only for types.
import type { User } from 'firebase/auth'
import type { Timestamp } from 'firebase/firestore'
import type { AppContext, Fb, Profile } from '../firebase'
import { forgetToken } from '../tokens'

export type Project = {
  slug: string
  name: string
  description: string
  /** Machines polling this project's queue, by name, with when they last did. */
  machines: Record<string, { seen?: Timestamp }>
}

export type TaskFile = { path: string; size: number; available: boolean; object: string }

/** A file uploaded to the bucket, named by the SHA-256 of its bytes. */
export type Attachment = { id: string; name: string; size: number }

export const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024

export type Question = { id: string; text: string; options: string[] }

export type Task = {
  id: string
  project: string
  email: string
  title: string
  problem: string
  state: string
  activity?: string
  machine?: string
  createdAt?: Timestamp
  updatedAt?: Timestamp
  summary?: string | null
  error?: string | null
  files: TaskFile[]
  attachments?: Attachment[]
  attempts?: number
  /** The question the task is waiting on, if any. */
  question?: Question | null
}

export type TaskEvent = { id: string; seq: number; kind: string; text: string; ts?: Timestamp }

export type Request = {
  id: string
  title: string
  status: 'accepted' | 'rejected'
  email?: string
  reason?: string
  taskId?: string
  createdAt?: Timestamp
}

let fb: Fb
let ctx: AppContext

export function init(context: AppContext) {
  ctx = context
  fb = context.fb
}

export const signOutNow = () => fb.a.signOut(fb.auth)

const me = (user: User) => (user.email ?? '').toLowerCase()

function project(slug: string, data: Record<string, unknown> | undefined): Project {
  return {
    slug,
    name: (data?.name as string) || slug,
    description: (data?.description as string) || '',
    machines: (data?.machines ?? {}) as Project['machines'],
  }
}

/** The projects this person may use: every served one for an admin, else
 *  those their users/<email> entry names (served or not). */
export async function myProjects(profile: Profile): Promise<Project[]> {
  const { f, db } = fb
  if (profile.role === 'admin') {
    const snap = await f.getDocs(f.collection(db, 'projects'))
    return snap.docs.map((d) => project(d.id, d.data())).sort((a, b) => a.name.localeCompare(b.name))
  }
  const slugs = Object.entries(profile.projects).filter(([, on]) => on).map(([slug]) => slug)
  const found = await Promise.all(
    slugs.map(async (slug) => project(slug, (await f.getDoc(f.doc(db, 'projects', slug))).data())),
  )
  return found.sort((a, b) => a.name.localeCompare(b.name))
}

/** The project's tasks; with `since`, only those changed after it (one read
 *  when nothing did, rather than one per task every 15 seconds). */
export async function tasksOf(slug: string, since?: Timestamp): Promise<Task[]> {
  const { f, db } = fb
  const tasks = f.collection(db, 'projects', slug, 'tasks')
  const snap = await f.getDocs(
    since
      ? f.query(tasks, f.where('updatedAt', '>', since), f.orderBy('updatedAt'), f.limit(100))
      : f.query(tasks, f.orderBy('createdAt', 'desc'), f.limit(100)),
  )
  return snap.docs.map((d) => ({ files: [], ...d.data(), id: d.id }) as unknown as Task)
}

/** What dex said about this person's requests: accepted (with the task) or rejected (with why). */
export async function myRequests(slug: string, user: User): Promise<Request[]> {
  const { f, db } = fb
  const snap = await f.getDocs(
    f.query(f.collection(db, 'projects', slug, 'requests'), f.where('email', '==', me(user)), f.limit(100)),
  )
  return snap.docs
    .map((d) => ({ ...d.data(), id: d.id }) as Request)
    .sort((a, b) => (b.createdAt?.toMillis() ?? Date.now()) - (a.createdAt?.toMillis() ?? Date.now()))
}

export async function eventsOf(slug: string, task: string, after = 0): Promise<TaskEvent[]> {
  const { f, db } = fb
  const snap = await f.getDocs(
    f.query(
      f.collection(db, 'projects', slug, 'tasks', task, 'events'),
      f.where('seq', '>', after), f.orderBy('seq'), f.limit(500),
    ),
  )
  return snap.docs.map((d) => ({ ...d.data(), id: d.id }) as TaskEvent)
}

function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function b64(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let out = ''
  for (const b of bytes) out += String.fromCharCode(b)
  return btoa(out)
}

/** Publish one message to the queue. Pub/Sub cannot say who published, so
 *  the person's Firebase ID token rides along and local dex checks it. */
async function publish(slug: string, body: Record<string, unknown>): Promise<void> {
  const token = await ctx.pubsubToken()
  const idToken = await ctx.user.getIdToken()
  const reply = await fetch(`https://pubsub.googleapis.com/v1/${ctx.config.topic}:publish`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages: [{ data: b64(JSON.stringify({ ...body, idToken })), attributes: { project: slug } }],
    }),
  })
  if (reply.status === 401) {
    forgetToken()
    throw new Error('Your Google sign-in for sending expired; send again to renew it.')
  }
  if (reply.status === 403) {
    throw new Error('You may not send to this queue yet: a dex grants that within a couple of minutes of your being authorised.')
  }
  if (!reply.ok) throw new Error(`Pub/Sub: ${reply.status} ${(await reply.text()).slice(0, 200)}`)
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** Upload one attachment to the person's own area of the project, under its
 *  SHA-256, and return its id. A file already there (sent before) is not sent
 *  again. `progress` gets 0..1. */
export async function uploadAttachment(
  slug: string, file: File, progress: (fraction: number) => void = () => {},
): Promise<Attachment> {
  if (file.size > MAX_ATTACHMENT_BYTES) throw new Error('Larger than 100 MB')
  const { s, storage } = fb
  const id = await sha256(file)
  const at = s.ref(storage, `projects/${slug}/attachments/${ctx.user.uid}/${id}`)
  const attachment = { id, name: file.name, size: file.size }
  try {
    await s.getMetadata(at)
    progress(1)
    return attachment
  } catch (e) {
    if ((e as { code?: string }).code !== 'storage/object-not-found') throw e
  }
  await new Promise<void>((resolve, reject) => {
    const task = s.uploadBytesResumable(at, file, {
      contentType: file.type || undefined, customMetadata: { name: file.name },
    })
    task.on('state_changed', (snap) => progress(snap.totalBytes ? snap.bytesTransferred / snap.totalBytes : 0),
      reject, () => resolve())
  })
  return attachment
}

/** Publish a request naming its attachments by id. Returns the request id. */
export async function submit(slug: string, title: string, problem: string, attachments: Attachment[]): Promise<string> {
  const id = newId()
  await publish(slug, { kind: 'task', requestId: id, title, problem, attachments })
  return id
}

/** Answer the question a task is waiting on; `taskId` is the task as listed. */
export async function answer(slug: string, taskId: string, questionId: string, text: string): Promise<void> {
  await publish(slug, { kind: 'answer', taskId, questionId, answer: text })
}

export async function fetchFile(object: string): Promise<Blob> {
  return fb.s.getBlob(fb.s.ref(fb.storage, object))
}

/** Whether some machine has polled the project in the last three minutes. */
export function served(p: Project): string | null {
  const now = Date.now()
  const live = Object.entries(p.machines)
    .filter(([, m]) => m.seen && now - m.seen.toMillis() < 180_000)
    .map(([name]) => name)
  return live.length ? live.join(', ') : null
}
