// Notifications (web/src/notifications): the bell, its count, and the panel
// that opens by itself for 5 seconds when something needs the operator. The
// API is answered in the browser from fixtures, and the event stream is a fake
// EventSource the test pushes events into; the page clock is driven by hand so
// the 5 s window is measured exactly.
//
//   npx playwright test --config web/playwright.config.ts notifications
import { expect, test, type Page } from '@playwright/test'
import { Detector } from '../src/notifications/detect'
import { HudController, type HudPhase } from '../src/notifications/hud'
import { applyEffects, markRead } from '../src/notifications/model'
import type { DexEvent, Task } from '../src/types'

const now = 1_760_000_000
const base: Task = {
  id: 'task-a', threadId: 't1', title: 'Build the widget', slug: 'widget', problem: 'Make it.', state: 'running',
  activity: null, createdAt: now, startedAt: now, finishedAt: null, error: null, costUsd: 0.1, turns: 3,
  outputDir: 'alpha/widget', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'widget', project: 'alpha', model: null, costIsEstimate: false, canResume: false, canRerun: true,
  canRestart: false, canPause: false, canContinue: false, canArchive: true,
}
const taskA = base
const taskB: Task = { ...base, id: 'task-b', title: 'Write the README', slug: 'readme', outputSlug: 'readme' }
const threads = [
  { id: 't1', title: 'Widgets', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: ['task-a', 'task-b'] },
  { id: 't2', title: 'Planning ahead', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now - 10, messageCount: 0, taskIds: [] },
]
const started = { id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 2 tasks', data: { tasks: [taskA, taskB] }, ts: now }

function answer(p: string): unknown {
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [taskA, taskB] }
  if (p === '/api/tasks/task-a') return { task: taskA }
  if (p === '/api/tasks/task-b') return { task: taskB }
  if (/^\/api\/tasks\/task-[ab]\/events$/.test(p)) return { events: [] }
  if (/^\/api\/tasks\/task-[ab]\/messages$/.test(p)) return { messages: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 2, tasks: 2 }], default: 'alpha' }
  if (p === '/api/threads') return { threads }
  if (p === '/api/threads/t1') return { thread: { ...threads[0], messages: [started] }, tasks: [taskA, taskB] }
  if (p === '/api/threads/t2') return { thread: { ...threads[1], messages: [] }, tasks: [] }
  if (p === '/api/packages') return { packages: [] }
  if (/\/skills$/.test(p)) return { skills: [] }
  return { ok: true }
}

let seq = 1000
/** Pushes one event down the fake stream, stamped with the page's own clock. */
async function emit(page: Page, event: Record<string, unknown>) {
  seq += 1
  await page.evaluate((e) => {
    const w = window as unknown as { __emit: (e: unknown) => void }
    w.__emit({ ...e, ts: Date.now() / 1000 })
  }, { taskId: null, seq, ...event })
}
const ask = async (page: Page, taskId: string, id: string, question: string) => {
  await emit(page, { type: 'question', taskId, id, question, options: ['Yes', 'No'] })
  await emit(page, { type: 'task_state', taskId, state: 'awaiting_input', activity: 'waiting on a clarifying question' })
}
const approval = async (page: Page, taskId: string, id: string, title: string) => {
  await emit(page, { type: 'approval', taskId, id, tool: 'Bash', title, input: {} })
  await emit(page, { type: 'task_state', taskId, state: 'awaiting_input', activity: 'waiting on approval for Bash' })
}

async function open(page: Page) {
  await page.addInitScript(() => {
    // The app's one EventSource, replaced by one the test can push into.
    class FakeSource {
      onopen: (() => void) | null = null
      onmessage: ((m: { data: string }) => void) | null = null
      onerror: (() => void) | null = null
      constructor() {
        ;(window as unknown as { __source: FakeSource }).__source = this
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
    }
    const w = window as unknown as Record<string, unknown>
    w.EventSource = FakeSource
    w.__emit = (e: unknown) => (w.__source as FakeSource).onmessage?.({ data: JSON.stringify(e) })
  })
  await page.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(p)) })
  })
  await page.clock.install()
  await page.goto('/?project=alpha&thread=t1')
  await expect(page.locator('.task-chip', { hasText: 'Build the widget' }).first()).toBeVisible()
  await page.waitForFunction(() => Boolean((window as unknown as { __source?: unknown }).__source))
  // From here the page's time moves only when the test moves it.
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000))
}

const hud = (page: Page) => page.locator('.notif-hud')
const bell = (page: Page) => page.locator('.notif-trigger')
const items = (page: Page) => page.locator('.notif-item')

test('(a) a notice opens the panel for 5 s, then it fades closed', async ({ page }) => {
  await open(page)
  await expect(hud(page)).toHaveCount(0)
  await expect(page.locator('.notif-badge')).toHaveCount(0)

  await ask(page, 'task-a', 'q1', 'Which colour should the widget be?')
  await expect(hud(page)).toBeVisible()
  await expect(items(page)).toHaveCount(1)
  await expect(items(page).first()).toContainText('Asks a question')
  await expect(items(page).first()).toContainText('Build the widget')
  await expect(items(page).first()).toContainText('Which colour should the widget be?')
  await expect(items(page).first()).toContainText('just now')
  await expect(page.locator('.notif-badge')).toHaveText('1')
  // Seen is not read: the panel being open marks nothing.
  await expect(items(page).first()).toHaveAttribute('data-read', 'false')

  await page.clock.runFor(4900)
  await expect(hud(page)).toBeVisible()
  await expect(hud(page)).not.toHaveClass(/fading/)
  await page.clock.runFor(200)
  // A fade, not a disappearance: still there, going transparent.
  await expect(hud(page)).toHaveClass(/fading/)
  await expect(hud(page)).toHaveCSS('transition-property', 'opacity')
  await page.clock.runFor(500)
  await expect(hud(page)).toHaveCount(0)
  await expect(page.locator('.notif-badge')).toHaveText('1')
})

test('(b) a second notice while it is open restarts the 5 s, newest listed first', async ({ page }) => {
  await open(page)
  await ask(page, 'task-a', 'q1', 'Which colour?')
  await expect(hud(page)).toBeVisible()
  await page.clock.runFor(3000)

  await approval(page, 'task-b', 'a1', 'Install a dependency')
  await expect(items(page)).toHaveCount(2)
  await expect(items(page).first()).toContainText('Needs approval')
  await expect(items(page).nth(1)).toContainText('Asks a question')
  await expect(page.locator('.notif-badge')).toHaveText('2')

  // 5.9 s after the first: the first timer would have closed it by now.
  await page.clock.runFor(2900)
  await expect(hud(page)).toBeVisible()
  await expect(hud(page)).not.toHaveClass(/fading/)
  // 4.9 s after the second.
  await page.clock.runFor(2000)
  await expect(hud(page)).not.toHaveClass(/fading/)
  await page.clock.runFor(200)
  await expect(hud(page)).toHaveClass(/fading/)
  await page.clock.runFor(500)
  await expect(hud(page)).toHaveCount(0)
})

test('(b) a notice during the fade brings it back for a full 5 s', async ({ page }) => {
  await open(page)
  await ask(page, 'task-a', 'q1', 'Which colour?')
  await page.clock.runFor(5100)
  await expect(hud(page)).toHaveClass(/fading/)
  await approval(page, 'task-b', 'a1', 'Install a dependency')
  await expect(hud(page)).not.toHaveClass(/fading/)
  await page.clock.runFor(4900)
  await expect(hud(page)).toBeVisible()
  await page.clock.runFor(700)
  await expect(hud(page)).toHaveCount(0)
})

test('(b) opened by hand it stays, until a notice gives it a 5 s window', async ({ page }) => {
  await open(page)
  await bell(page).click()
  await expect(hud(page)).toBeVisible()
  await expect(hud(page)).toContainText('Nothing needs you right now.')
  await page.clock.runFor(20_000)
  await expect(hud(page)).toBeVisible()
  await ask(page, 'task-a', 'q1', 'Which colour?')
  await page.clock.runFor(4900)
  await expect(hud(page)).toBeVisible()
  await page.clock.runFor(700)
  await expect(hud(page)).toHaveCount(0)
})

test('(c) clicking a notice marks it read at the click and opens its task', async ({ page }) => {
  await open(page)
  await ask(page, 'task-a', 'q1', 'Which colour?')
  await approval(page, 'task-b', 'a1', 'Install a dependency')
  await expect(page.locator('.notif-badge')).toHaveText('2')
  await expect(page.locator('.task-panel')).toHaveCount(0)

  await items(page).filter({ hasText: 'Which colour?' }).click()
  // It closes, and its task opens.
  await expect(hud(page)).toHaveCount(0)
  await expect(page.locator('.task-panel')).toBeVisible()
  await expect(page.locator('.task-panel')).toContainText('Build the widget')
  await expect(page.locator('.notif-badge')).toHaveText('1')

  await bell(page).click()
  await expect(items(page).filter({ hasText: 'Which colour?' })).toHaveAttribute('data-read', 'true')
  await expect(items(page).filter({ hasText: 'Which colour?' })).toHaveClass(/read/)
  await expect(items(page).filter({ hasText: 'Install a dependency' })).toHaveAttribute('data-read', 'false')
  await expect(items(page).filter({ hasText: 'Install a dependency' })).toHaveClass(/unread/)
  // Read state is kept across a reload.
  await page.clock.resume()
  await page.reload()
  await bell(page).click()
  await expect(items(page).filter({ hasText: 'Which colour?' })).toHaveAttribute('data-read', 'true')
  await expect(page.locator('.notif-badge')).toHaveText('1')
})

test('(c) a plan waiting on approval opens its thread', async ({ page }) => {
  await open(page)
  await emit(page, {
    type: 'thread_message', threadId: 't2',
    message: { id: 'p1', role: 'dex', kind: 'plan', text: 'Two tasks.', data: { tasks: [{ title: 'One', problem: '', slug: 'one' }, { title: 'Two', problem: '', slug: 'two' }] }, ts: now },
  })
  await expect(items(page).first()).toContainText('Plan awaits approval')
  await expect(items(page).first()).toContainText('Planning ahead')
  await expect(items(page).first()).toContainText('2 tasks planned')
  await items(page).first().click()
  await expect(page).toHaveURL(/thread=t2/)
  await expect(page.locator('.topbar-title')).toHaveText('Planning ahead')
})

test('(d) a click outside closes it at once', async ({ page }) => {
  await open(page)
  await ask(page, 'task-a', 'q1', 'Which colour?')
  await expect(hud(page)).toBeVisible()
  await page.locator('.topbar-title').click()
  // No time passes: closed by the click, not by the timer.
  await expect(hud(page)).toHaveCount(0)
  await expect(items(page)).toHaveCount(0)
  // A click inside does not close it, and the bell toggles.
  await bell(page).click()
  await page.locator('.notif-head').click()
  await expect(hud(page)).toBeVisible()
  await bell(page).click()
  await expect(hud(page)).toHaveCount(0)
})

test('nothing for an auto-approval, a question dex answered itself, or a replayed event', async ({ page }) => {
  await open(page)
  await emit(page, { type: 'approval', taskId: 'task-a', id: 'a9', tool: 'Bash', title: 'ls', input: {}, auto: true })
  await emit(page, { type: 'approval_resolved', taskId: 'task-a', id: 'a9', decision: 'allow', auto: true })
  await emit(page, { type: 'question', taskId: 'task-a', id: 'q9', question: 'Share it?', options: ['Put it', 'Keep it'] })
  await emit(page, { type: 'question_answered', taskId: 'task-a', id: 'q9', answer: 'Put it', auto: true })
  // From the stream's replay: stamped before the page opened.
  await page.evaluate(() =>
    (window as unknown as { __emit: (e: unknown) => void }).__emit({
      type: 'task_state', taskId: 'task-b', seq: 5, ts: Date.now() / 1000 - 3600, state: 'awaiting_input', activity: 'waiting on a clarifying question',
    }),
  )
  await page.clock.runFor(100)
  await expect(hud(page)).toHaveCount(0)
  await expect(page.locator('.notif-badge')).toHaveCount(0)
})

test('a thread’s tasks finish as one notice; a child step finishing says nothing', async ({ page }) => {
  await open(page)
  await emit(page, { type: 'task_created', taskId: 'child', task: { ...taskA, id: 'child', title: 'A child step', parentId: 'task-a' } })
  await emit(page, { type: 'task_state', taskId: 'child', state: 'succeeded', activity: null })
  await emit(page, { type: 'task_state', taskId: 'task-a', state: 'succeeded', activity: null })
  await page.clock.runFor(100)
  await expect(hud(page)).toHaveCount(0)
  await emit(page, { type: 'task_state', taskId: 'task-b', state: 'failed', activity: null })
  await expect(items(page)).toHaveCount(1)
  await expect(items(page).first()).toContainText('Finished with failures')
  await expect(items(page).first()).toContainText('Widgets')
  await expect(items(page).first()).toContainText('All 2 tasks finished: 1 failed')
})

// The parts without a page, where every case is cheap to state.

const ev = (e: Record<string, unknown>, ts = 2_000_000_000) => ({ taskId: null, seq: 1, ts, ...e }) as DexEvent

test('detector: an answered question resolves its notice; a re-sent event is not a second one', () => {
  const d = new Detector(0, () => 5)
  d.seedTasks([taskA])
  const raised = [
    ...d.observe(ev({ type: 'question', taskId: 'task-a', id: 'q', question: 'Why?', options: [] })),
    ...d.observe(ev({ type: 'task_state', taskId: 'task-a', state: 'awaiting_input', activity: null })),
  ]
  expect(raised).toHaveLength(1)
  let { items: list, arrived } = applyEffects([], raised)
  expect(arrived).toBe(1)
  // The same notice again (a replay after reconnecting) adds nothing.
  ;({ items: list, arrived } = applyEffects(list, raised))
  expect([list.length, arrived]).toEqual([1, 0])
  const answered = d.observe(ev({ type: 'question_answered', taskId: 'task-a', id: 'q', answer: 'Because' }))
  expect(answered).toEqual([{ type: 'resolve', key: 'ask:task-a:q' }])
  list = applyEffects(list, answered).items
  expect(list[0]).toMatchObject({ resolved: true, read: false })
  expect(markRead(list, list[0].id)[0].read).toBe(true)
})

test('hud: arrive opens for 5 s, a second arrival restarts it, dismiss is immediate', () => {
  let clock = 0
  const pending: { at: number; fn: () => void; id: number }[] = []
  let ids = 0
  const timers = {
    setTimeout: (fn: () => void, ms: number) => (pending.push({ at: clock + ms, fn, id: ++ids }), ids),
    clearTimeout: (h: unknown) => {
      const at = pending.findIndex((p) => p.id === h)
      if (at >= 0) pending.splice(at, 1)
    },
  }
  const run = (ms: number) => {
    const until = clock + ms
    for (;;) {
      pending.sort((a, b) => a.at - b.at)
      const next = pending[0]
      if (!next || next.at > until) break
      pending.shift()
      clock = next.at
      next.fn()
    }
    clock = until
  }
  const phases: HudPhase[] = []
  const hudc = new HudController((p) => phases.push(p), 5000, 400, timers)
  hudc.arrive()
  run(3000)
  hudc.arrive()
  run(4999)
  expect(hudc.phase).toBe('open')
  run(1)
  expect(hudc.phase).toBe('fading')
  run(400)
  expect(hudc.phase).toBe('closed')
  hudc.arrive()
  hudc.dismiss()
  expect(hudc.phase).toBe('closed')
  expect(pending).toHaveLength(0)
  expect(phases).toEqual(['open', 'fading', 'closed', 'open', 'closed'])
})
