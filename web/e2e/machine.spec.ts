// The machine popup, first in a task's header row (web/src/components/MachineButton.tsx):
// a local model's shows every line, a dash where a value is missing; a cloud model's only
// the lines it has; a task with nothing has no button. Hover opens it, a click keeps it
// open until a click outside. The API is answered in the browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts e2e/machine.spec.ts
import { expect, test, type Page } from '@playwright/test'

const now = Math.floor(Date.now() / 1000)
const base = {
  threadId: 't1', problem: 'Make it.', state: 'running', activity: 'Working', createdAt: now - 600,
  startedAt: now - 600, finishedAt: null, error: null, costUsd: 0.1, turns: 3, artifacts: [], pending: [],
  attempt: 1, parentId: null, sessionId: null, resumedFrom: null, project: 'alpha', costIsEstimate: true,
  canResume: false, canRerun: false, canRestart: false, canPause: true, canContinue: false, canArchive: false,
}
const tasks = [
  { ...base, id: 'cloud', title: 'Cloud task', slug: 'cloud', outputDir: 'alpha/cloud', outputSlug: 'cloud', model: 'claude-opus-5-5' },
  { ...base, id: 'local', title: 'Local task', slug: 'local', outputDir: 'alpha/local', outputSlug: 'local', model: 'oc:local/qwen3.8-flash-next' },
  { ...base, id: 'bare', title: 'Bare task', slug: 'bare', outputDir: 'alpha/bare', outputSlug: 'bare', model: 'claude-opus-5-5' },
]
// One task a thread: a thread's running tasks fold into one chip.
const threadOf = (t: (typeof tasks)[number]) => ({ id: 't1', title: 'Widgets', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: [t.id] })
const cost = (taskId: string, seq: number) => ({
  type: 'cost', taskId, seq, ts: now - 5, costUsd: 0.1, estimate: true,
  throughput: { in: 4000, inS: 10, out: 340, outS: 10 },
})
const events: Record<string, unknown[]> = {
  cloud: [cost('cloud', 1)],
  // Power and the GPU only: no DRAM (a Mac without IOReport) and no token rates (no --metrics).
  local: [cost('local', 1), {
    type: 'machine', taskId: 'local', seq: 2, ts: now - 3,
    power: { adapter_w: 86.9, wall_w: 89.5, battery_w: 13.1, fresh_s: 30 },
    gpu: { active: 1, mhz: 1119, ceiling_tflops: 11.4, busy_ceiling_tflops: 11.4 },
    tokens: null,
  }],
  bare: [],
}

function answer(p: string, shown: (typeof tasks)[number]): unknown {
  const thread = threadOf(shown)
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 1, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [shown] }
  const one = /^\/api\/tasks\/([^/]+)$/.exec(p)
  if (one) return { task: tasks.find((t) => t.id === one[1]) }
  if (/^\/api\/tasks\/[^/]+\/messages$/.test(p)) return { messages: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 1, tasks: 1 }], default: 'alpha' }
  if (p === '/api/threads') return { threads: [thread] }
  if (p === '/api/threads/t1') return { thread: { ...thread, messages: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [shown] }, ts: now }] }, tasks: [shown] }
  if (p === '/api/packages') return { packages: [] }
  if (/\/skills$/.test(p)) return { skills: [] }
  return { ok: true }
}

async function open(page: Page, title: string) {
  const shown = tasks.find((t) => t.title === title)!
  await page.route('**/api/**', async (route) => {
    const p = new URL(route.request().url()).pathname
    if (p === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    const ev = /^\/api\/tasks\/([^/]+)\/events$/.exec(p)
    if (ev) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ taskId: ev[1], events: events[ev[1]] ?? [] }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(p, shown)) })
  })
  await page.goto('/?project=alpha&thread=t1')
  await page.locator('.task-chip', { hasText: title }).first().click()
  await expect(page.locator('.task-panel .panel-name')).toHaveText(title)
}

const button = (page: Page) => page.locator('.task-panel .panel-meta .machine-btn')
const pop = (page: Page) => page.locator('.task-panel .machine-pop')
const lines = (page: Page) => pop(page).locator('.machine-line')

test('a cloud model shows only the lines it has, on hover, first in the header row', async ({ page }) => {
  await open(page, 'Cloud task')
  // Left of the other header items: the row's first child.
  await expect(page.locator('.task-panel .panel-meta > :first-child')).toHaveClass(/machine/)
  await button(page).hover()
  await expect(pop(page)).toBeVisible()
  await expect(lines(page)).toHaveCount(2)
  await expect(lines(page).nth(0)).toHaveText(/📥.*in 400 t\/s/)
  await expect(lines(page).nth(1)).toHaveText(/📤.*out 34 t\/s/)
  await expect(pop(page)).not.toContainText('—')
  // Opened by hover: leaving closes it.
  await page.mouse.move(5, 790)
  await expect(pop(page)).toBeHidden()
})

test('a local model shows every line, a dash for each missing one, until a click outside', async ({ page }) => {
  await open(page, 'Local task')
  await button(page).click()
  await expect(pop(page)).toBeVisible()
  const want: [string, RegExp][] = [
    ['power', /⚡.*86\.9 W from the adapter \(~89\.5 W at the wall\)/],
    ['battery', /🔋.*charging 13\.1 W/],
    ['dram', /🧠.*—/],
    ['gpu', /🎮.*GPU 100% busy @ 1119 MHz \(ceiling 11\.4 TFLOPS\)/],
    ['in', /📥.*in 400 t\/s/],
    ['out', /📤.*out 34 t\/s/],
    ['server', /🖥️.*—/],
    ['work', /🧮.*—/],
    ['age', /⏱️.*read \d+ s ago · power 30 s older/],
  ]
  await expect(lines(page)).toHaveCount(want.length)
  for (const [i, [key, text]] of want.entries()) {
    await expect(lines(page).nth(i)).toHaveAttribute('data-key', key)
    await expect(lines(page).nth(i)).toHaveText(text)
  }
  await expect(pop(page).locator('.machine-line.missing')).toHaveCount(3)
  await page.screenshot({ path: process.env.MACHINE_SHOT ?? 'test-results/machine-popup.png' })
  // Opened by a click: moving away keeps it; a click outside closes it.
  await page.mouse.move(5, 790)
  await expect(pop(page)).toBeVisible()
  await page.mouse.click(5, 790)
  await expect(pop(page)).toBeHidden()
  // And a second click on the button closes it too.
  await button(page).click()
  await expect(pop(page)).toBeVisible()
  await button(page).click()
  await expect(pop(page)).toBeHidden()
})

test('a task with nothing to show has no button', async ({ page }) => {
  await open(page, 'Bare task')
  await expect(page.locator('.task-panel .panel-meta .badge').first()).toBeVisible()
  await expect(button(page)).toHaveCount(0)
})

test('on a phone the popup fits the screen', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await open(page, 'Local task')
  await button(page).click()
  const box = await pop(page).boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(375)
  await page.screenshot({ path: process.env.MACHINE_PHONE_SHOT ?? 'test-results/machine-popup-phone.png' })
})
