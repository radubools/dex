import { expect, test, type Page } from '@playwright/test'

const todos = [
  { id: 'a', slug: 'todo-a', title: 'Tidy the lab', state: 'succeeded', costUsd: 1.25, spawnedBy: null },
  { id: 'b', slug: 'todo-b', title: 'Rebuild the bench', state: 'blocked', activity: 'waiting on the tasks it started', costUsd: 0.5, spawnedBy: null },
  { id: 'c', slug: 'bench-model', title: 'Bench model', state: 'running', activity: 'Write bench.py', costUsd: 2, spawnedBy: 'b' },
  { id: 'd', slug: 'bench-sound', title: 'Bench sound', state: 'todo', costUsd: 0, spawnedBy: 'c' },
  { id: 'e', slug: 'todo-e', title: 'Check the lessons', state: 'todo', activity: 'waiting for dex to be idle', costUsd: 0, spawnedBy: null },
]

async function mount(page: Page, doc: unknown, theme = 'dark') {
  await page.goto('/widgets/harness.html?widget=power-todos')
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(
    ([text, mode]) => (window as any).__mount({ path: 'ml/todos-x/todos.json', text, theme: mode }),
    [JSON.stringify(doc), theme],
  )
}

test('shows each todo, what it spawned, and how far the list is', async ({ page }) => {
  await mount(page, { request: 'Tidy things', todos })
  await expect(page.locator('#error')).toBeHidden()
  await expect(page.locator('.pt-title')).toHaveText('Tidy things')
  await expect(page.locator('.pt-count')).toHaveText('1 of 5 done')
  await expect(page.locator('.pt-total')).toHaveText('$3.75')
  await expect(page.locator('.pt-list > li')).toHaveCount(3)

  // Spawned work sits under what started it, at any depth.
  const nested = page.locator('.pt-list > li[data-state="blocked"] ul ul .pt-name')
  await expect(nested).toHaveText('Bench sound')

  await expect(page.locator('.pt-chip.running')).toHaveText('Running')
  await expect(page.locator('.pt-chip.todo').first()).toHaveText('Todo')
  await expect(page.locator('.pt-activity').first()).toHaveText('waiting on the tasks it started')
})

test('a todo whose parent is not on the list still shows', async ({ page }) => {
  await mount(page, { todos: [{ id: 'x', slug: 'x', title: 'Orphan', state: 'todo', spawnedBy: 'gone' }] }, 'light')
  await expect(page.locator('.pt-list > li')).toHaveCount(1)
  await expect(page.locator('.pt-title')).toHaveText('Todos')
})

test('an empty list and a file that is not a list say so', async ({ page }) => {
  await mount(page, { request: 'Later', todos: [] })
  await expect(page.locator('.pt-empty')).toBeVisible()
  await page.evaluate(() => (window as any).__mount({ path: 'x/todos.json', text: '{"a":1}' }).catch(() => {}))
  await expect(page.locator('#error')).toContainText('not a todo list')
})
