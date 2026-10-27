// Prioritize (design/18): a queued task's ⇡ Prioritize moves it, and every
// task under it, to the front of the queue. The API is answered in the
// browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts
import { expect, test } from '@playwright/test'

const now = 1_760_000_000
const task = {
  id: 'task-a', threadId: 't1', title: 'Build the widget', slug: 'widget', problem: 'Make it.', state: 'queued',
  activity: null, createdAt: now, startedAt: now, finishedAt: now, error: null, costUsd: 0.1, turns: 3,
  outputDir: 'alpha/widget', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'widget', project: 'alpha', model: null, costIsEstimate: false, canResume: false, canRerun: true,
  canRestart: false, canPause: false, canContinue: false, canArchive: true,
  priority: 0, canPrioritize: true,
}
const thread = { id: 't1', title: 'Widgets', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: ['task-a'] }

function answer(p: string): unknown {
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [task] }
  if (p === '/api/tasks/task-a') return { task }
  if (p === '/api/tasks/task-a/messages') return { messages: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 1, tasks: 1 }], default: 'alpha' }
  if (p === '/api/threads') return { threads: [thread] }
  if (p === '/api/threads/t1') return { thread: { ...thread, messages: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [task] }, ts: now }] }, tasks: [task] }
  if (p === '/api/packages') return { packages: [] }
  if (/\/skills$/.test(p)) return { skills: [] }
  return { ok: true }
}

test('Prioritize moves a queued task to the front, and says so', async ({ page }) => {
  const asked: string[] = []
  await page.route('**/api/**', async (route) => {
    const p = new URL(route.request().url()).pathname
    if (p === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    if (p === '/api/tasks/task-a/events') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ taskId: 'task-a', events: [] }) })
    if (p === '/api/tasks/task-a/prioritize') {
      asked.push(route.request().method())
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tasks: [{ ...task, priority: 2, canPrioritize: false }] }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(p)) })
  })
  await page.goto('/?project=alpha&thread=t1')
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await page.getByRole('button', { name: /Prioritize/ }).click()
  expect(asked).toEqual(['POST'])
  await expect(page.locator('.task-panel .badge.prioritized')).toHaveText('⇡ next')
  await expect(page.getByRole('button', { name: /Prioritize/ })).toHaveCount(0)
})
