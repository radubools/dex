// Ideation (design/24): the Library's Ideas side, a proposal's Approve and
// Reject, and the project's switch in Settings. The API is answered in the
// browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts ideas
import { expect, test, type Page } from '@playwright/test'

const now = 1_760_000_000
const proposal = {
  id: 'idea-a', threadId: 't1', title: 'Better timbres', slug: 'todo-better-timbres', problem: 'x', state: 'todo',
  activity: null, createdAt: now, startedAt: null, finishedAt: null, error: null, costUsd: 0, turns: 0,
  outputDir: 'x', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'todo-better-timbres', project: 'alpha', model: null, costIsEstimate: false, canResume: false,
  canRerun: false, canRestart: true, canPause: true, canContinue: false, canArchive: true,
  priority: 0, canPrioritize: false, approval: 'pending', canApprove: true, canReject: true,
}
const idea = {
  slug: 'todo-better-timbres', displayName: 'Better timbres', files: 2, path: 'ideas/alpha/todo-better-timbres',
  tags: ['todo:pending-approval'], taskId: 'idea-a', lastActivity: now,
}
const pack = { slug: 'tree', displayName: 'Tree', files: 1, path: 'alpha/tree', tags: ['type:story', 'story', 'algo:stats', 'section:stats', 'chat', 'code-lines:1c', 'asset-lines:0a'],
  earnedTags: ['chat', 'code-lines:1c', 'asset-lines:0a'], taskId: null, lastActivity: null }

const thread = { id: 't1', title: 'Main', project: 'alpha', kind: 'main', intent: 'task', createdAt: now, updatedAt: now, messageCount: 0, taskIds: [] }

async function open(page: Page, available = true, stream = '', extra?: typeof pack[]) {
  const sent: { url: string; body: unknown }[] = []
  let enabled = false
  await page.route('**/api/**', (route) => {
    const request = route.request()
    const p = new URL(request.url()).pathname
    if (p === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: stream })
    if (request.method() !== 'GET') sent.push({ url: p, body: request.postDataJSON() })
    // A doubled header reaches the server as `application/json, application/json`: a 422.
    expect(request.headers()['content-type'] ?? 'application/json').toBe('application/json')
    const status = () => ({ available, enabled, taskId: null, state: null })
    let body: unknown = { ok: true }
    if (p === '/api/auth/me') body = { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
    else if (p === '/api/health') body = { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
    else if (p === '/api/settings') body = { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 }, tokens: { input: 0, cache_read: 0, cache_write: 0, output: 0, thinking: 0, visible: 0, counted: 0, with_cost: 0 }, models: [], efforts: [] }
    else if (p === '/api/tasks') body = { tasks: [] }
    else if (p === '/api/tasks/idea-a') body = { task: proposal }
    else if (p === '/api/tasks/idea-a/approval') body = { tasks: [{ ...proposal, approval: 'rejected', canReject: false }] }
    else if (p === '/api/projects') body = { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 0, tasks: 1 }], default: 'alpha' }
    else if (p === '/api/threads') body = { threads: [thread, { ...thread, id: 't2', title: 'Alpha · design', kind: 'project_design', intent: 'design' }, { ...thread, id: 't3', title: 'Music', kind: 'chat', intent: 'skill' }] }
    else if (p === '/api/threads/t1') body = { thread: { ...thread, messages: [{ id: 'r', role: 'dex', kind: 'routed', ts: now, text: 'Moved to Music.', data: { intent: 'skill', threadId: 't3', threadTitle: 'Music', reason: 'a track' } }] }, tasks: [] }
    else if (p === '/api/threads/t3') body = { thread: { ...thread, id: 't3', title: 'Music', kind: 'chat', intent: 'skill', messages: [] }, tasks: [] }
    else if (p === '/api/packages') body = { packages: extra ? [pack, ...extra] : [pack], tags: [] }
    else if (p === '/api/ideas') body = { log: 'ideas/alpha/IDEAS.md', ideas: [idea, { ...idea, slug: 'todo-fix-catalog', displayName: 'Fix the catalog', tags: ['code-lines:3c', 'todo:approved'], taskId: null }] }
    else if (p.endsWith('/ideation/status')) body = status()
    else if (p.endsWith('/ideation')) { enabled = (request.postDataJSON() as { enabled: boolean }).enabled; body = status() }
    else if (p === '/api/assets' || p.startsWith('/api/assets')) body = { kind: 'dir', path: idea.path, entries: [{ name: `${idea.path}/SPEC.md` }] }
    else if (/\/skills$/.test(p)) body = { skills: [] }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.goto('/?project=alpha&thread=t1&view=library')
  return sent
}

test('ideas are a side of the Library of their own', async ({ page }) => {
  await open(page)
  await expect(page.locator('.asset-row', { hasText: 'Tree' })).toBeVisible()
  await expect(page.locator('.asset-row', { hasText: 'Better timbres' })).toHaveCount(0)
  await page.getByRole('button', { name: /^Ideas/ }).click()
  await expect(page.locator('.asset-row', { hasText: 'Ideas log' })).toBeVisible()
  await expect(page.locator('.asset-row', { hasText: 'Better timbres' })).toBeVisible()
  await expect(page.locator('.asset-row', { hasText: 'Tree' })).toHaveCount(0)
})

test('a proposal is approved or rejected beside its spec', async ({ page }) => {
  const sent = await open(page)
  await page.getByRole('button', { name: /^Ideas/ }).click()
  await page.locator('.asset-row', { hasText: 'Better timbres' }).click()
  await page.getByRole('button', { name: /Reject/ }).click()
  await expect(page.locator('.todo-decision .badge')).toHaveText('rejected')
  await expect(page.locator('.todo-decision').getByRole('button', { name: /Approve/ })).toBeVisible()
  expect(sent.filter((r) => r.url.includes('approval'))).toEqual([{ url: '/api/tasks/idea-a/approval', body: { decision: 'rejected' } }])
})

test('Settings has the ideation switch only with the Power skill, off at first', async ({ page }) => {
  const sent = await open(page)
  await page.getByRole('button', { name: 'Settings' }).click()
  const row = page.locator('.menu-row', { hasText: 'Ideation' }).getByRole('switch')
  await expect(row).not.toBeChecked()
  await row.click()
  await expect(row).toBeChecked()
  expect(sent.filter((r) => r.url.includes('ideation'))).toEqual([{ url: '/api/projects/alpha/ideation', body: { enabled: true } }])
})

test('no Power skill, no switch', async ({ page }) => {
  await open(page, false)
  await page.getByRole('button', { name: 'Settings' }).click()
  await expect(page.locator('[aria-label="Settings"] .menu-row').first()).toBeVisible()
  await expect(page.locator('.menu-row', { hasText: 'Ideation' })).toHaveCount(0)
})

test('a row says each tag once, and a plain tag gives way to its grouped twin', async ({ page }) => {
  await open(page)
  await expect(page.locator('.asset-row', { hasText: 'Tree' }).locator('.row-tag')).toHaveText(['story', 'stats', 'chat', '1c', '0a'])
  await page.getByRole('button', { name: /Filters/ }).click()
  await expect(page.locator('.tag-pill', { hasText: 'story' })).toHaveCount(1)
  await expect(page.locator('.tag-pill', { hasText: 'stats' })).toHaveCount(2)
})

test('ideas can be searched by name or tag, every word narrowing', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^Ideas/ }).click()
  const search = page.getByRole('textbox', { name: 'Find ideas' })
  await search.fill('catalog')
  await expect(page.locator('.asset-row')).toHaveText([/Fix the catalog/])
  await search.fill('pending')
  await expect(page.locator('.asset-row')).toHaveText([/Better timbres/])
  await search.fill('3c')
  await expect(page.locator('.asset-row')).toHaveText([/Fix the catalog/])
  await search.fill('catalog pending')
  await expect(page.getByText('No ideas match')).toBeVisible()
  await search.fill('')
  await expect(page.locator('.asset-row')).toHaveCount(3)
})

test('size tags group under code and asset lines, and follow the rollup while a task writes', async ({ page }) => {
  const event = { type: 'package_sizes', projectSlug: 'alpha', slug: 'tree', tags: ['code-lines:3c', 'asset-lines:0a'], seq: 1 }
  await open(page, true, `data: ${JSON.stringify(event)}\n\n`)
  const row = page.locator('.asset-row', { hasText: 'Tree' }).locator('.row-tag')
  await expect(row).toHaveText(['story', 'stats', 'chat', '3c', '0a'])
  await page.getByRole('button', { name: /Filters/ }).click()
  await expect(page.locator('.tag-group', { hasText: 'code-lines' }).locator('.tag-pill')).toHaveText([/3c/])
  await expect(page.locator('.tag-group', { hasText: 'asset-lines' }).locator('.tag-pill')).toHaveText([/0a/])
})

const entry = (slug: string, tags: string[]) => ({ ...pack, slug, displayName: slug, tags, earnedTags: [] })
const arranged = [entry('rock', ['type:story', 'level:hard']), entry('leaf', ['type:story', 'level:easy']), entry('bare', ['type:model'])]

test('Arrange turns a tag group into folders, and All is the plain list', async ({ page }) => {
  await open(page, true, '', arranged)
  const arrange = page.getByLabel('Arrange')
  await expect(arrange.locator('option').first()).toHaveText('All')
  await expect(page.locator('.asset-row')).toHaveCount(4)
  await arrange.selectOption('level')
  await expect(page.locator('.asset-row')).toHaveText([/easy.*1 item/, /hard.*1 item/, /No level.*2 items/])
  await page.locator('.asset-row', { hasText: 'hard' }).click()
  await expect(page.locator('.asset-row')).toHaveText([/level \/ hard/, /rock/])
  await page.locator('.folder-back').click()
  await page.locator('.asset-row', { hasText: 'No level' }).click()
  await expect(page.locator('.asset-row')).toHaveText([/no level/, /Tree/, /bare/])
  await arrange.selectOption('')
  await expect(page.locator('.asset-row')).toHaveCount(4)
})

test('Arrange is kept for each project, and search narrows the folders', async ({ page }) => {
  await open(page, true, '', arranged)
  await page.getByLabel('Arrange').selectOption('level')
  await page.reload()
  await expect(page.getByLabel('Arrange')).toHaveValue('level')
  expect(await page.evaluate(() => localStorage.getItem('dex.library.arrange.alpha'))).toBe('level')
  await page.getByPlaceholder(/Find among/).fill('rock')
  await expect(page.locator('.asset-row')).toHaveText([/hard.*1 item/])
})

test('No group arranges what stays out of the group:value convention', async ({ page }) => {
  await open(page, true, '', [entry('old', ['legacy', 'type:story']), entry('plain', []), entry('tidy', ['type:story'])])
  const arrange = page.getByLabel('Arrange')
  await expect(arrange.locator('option').nth(0)).toHaveText('All')
  await expect(arrange.locator('option').nth(1)).toHaveText('No group')
  await arrange.selectOption({ label: 'No group' })
  await expect(page.locator('.asset-row')).toHaveText([/chat.*1 item/, /legacy.*1 item/, /No tag.*1 item/])
  await page.locator('.asset-row', { hasText: 'legacy' }).click()
  await expect(page.locator('.asset-row')).toHaveText([/No group \/ legacy/, /old/])
  await page.locator('.folder-back').click()
  await page.locator('.asset-row', { hasText: 'No tag' }).click()
  await expect(page.locator('.asset-row')).toHaveText([/No group \/ no tag/, /plain/])
})

test('Arrange works on the Ideas side, kept apart from the assets', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^Ideas/ }).click()
  const arrange = page.getByLabel('Arrange')
  await expect(arrange.locator('option')).toHaveText(['All', 'No group', 'code-lines', 'todo'])
  await arrange.selectOption('todo')
  await expect(page.locator('.asset-row')).toHaveText([/Ideas log/, /approved.*1 item/, /pending-approval.*1 item/])
  await page.locator('.asset-row', { hasText: 'approved' }).first().click()
  await expect(page.locator('.asset-row')).toHaveText([/todo \/ approved/, /Fix the catalog/])
  await page.locator('.folder-back').click()
  await page.getByLabel('Find ideas').fill('timbres')
  await expect(page.locator('.asset-row')).toHaveText([/pending-approval.*1 item/])
  await page.getByLabel('Find ideas').fill('')
  await arrange.selectOption({ label: 'No group' })
  await expect(page.getByText('Every idea here uses the group:value convention.')).toBeVisible()
  await arrange.selectOption('todo')

  await page.reload()
  await expect(page.getByLabel('Arrange')).toHaveValue('')
  await page.getByRole('button', { name: /^Ideas/ }).click()
  await expect(page.getByLabel('Arrange')).toHaveValue('todo')
  expect(await page.evaluate(() => localStorage.getItem('dex.library.arrange.alpha.ideas'))).toBe('todo')
  expect(await page.evaluate(() => localStorage.getItem('dex.library.arrange.alpha'))).toBeNull()
})

test('the filter shows one intent, and Main is always there to post in', async ({ page }) => {
  await open(page)
  await page.goto('/?project=alpha&thread=t1')
  const rows = page.locator('.thread-title')
  await expect(rows).toHaveText([/Main/, /Alpha · design/, /Music/])
  await page.locator('.intent-filter').getByRole('button', { name: 'Skills' }).click()
  await expect(rows).toHaveText([/Main/, /Music/])
  await page.locator('.intent-filter').getByRole('button', { name: 'Design' }).click()
  await expect(rows).toHaveText([/Main/, /Alpha · design/])
  await page.reload()
  await expect(rows).toHaveText([/Main/, /Alpha · design/])
  await page.locator('.intent-filter').getByRole('button', { name: 'All' }).click()
  await page.locator('.routed').click()
  await expect(page).toHaveURL(/thread=t3/)
})

test('home is a prompt with attachments, the menu stays, and sending goes to Main', async ({ page }) => {
  const sent = await open(page)
  await page.goto('/?project=alpha')
  const card = page.locator('.home-card')
  await expect(card.locator('textarea')).toBeVisible()
  await expect(card.getByRole('button', { name: /attach/i })).toBeVisible()
  const box = (await card.locator('textarea').boundingBox())!
  await expect(card.locator('.attach-btn .attach-icon')).toBeVisible()
  const attach = (await card.getByRole('button', { name: /attach/i }).boundingBox())!
  const send = (await card.getByRole('button', { name: 'Send' }).boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(160)
  expect(attach.y).toBeGreaterThan(box.y + box.height)
  expect(send.y).toBeGreaterThan(box.y + box.height)
  expect(attach.x).toBe(box.x)
  expect(send.x + send.width).toBe(box.x + box.width)
  await expect(page.locator('.threads .thread-title').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Library', exact: true })).toBeVisible()
  await card.locator('textarea').fill('make a track')
  await card.locator('textarea').press('Enter')
  await expect(page).toHaveURL(/thread=t1/)
  expect(sent.some((s) => s.url === '/api/threads/t1/messages')).toBe(true)
  await page.getByRole('button', { name: 'Home', exact: true }).click()
  await expect(page.locator('.home-card')).toBeVisible()
})

test('the side menu labels its items, and Chat folds the threads under it', async ({ page }) => {
  await open(page)
  const chat = page.locator('.menu-item', { hasText: 'Chat' })
  await expect(page.locator('.side-menu .menu-label')).toHaveText(['Home', 'Library', 'Task graph', 'Notifications', 'Guide', 'Chat', 'Review feed', 'Cost dashboard'])
  await chat.click()
  await expect(page).not.toHaveURL(/view=library/)
  await expect(page.locator('.thread-title').first()).toBeVisible()
  await chat.click()
  await expect(chat).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.thread-title')).toHaveCount(0)
  await chat.click()
  await expect(page.locator('.thread-title').first()).toBeVisible()
  await expect(page.locator('.threads-head .project-trigger')).toBeVisible()
  await expect(page.locator('.brand')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '＋ New thread' })).toBeVisible()
  await expect(page.locator('.side-menu').getByRole('button', { name: 'Settings' })).toHaveCount(0)
  await expect(page.locator('.side-menu').getByRole('button', { name: 'Notifications' })).toBeVisible()
  await expect(page.locator('.topbar button:not(.back-btn)')).toHaveCount(0)
  await expect(page.locator('.side-foot').getByRole('button', { name: 'Settings' })).toBeVisible()
  await page.getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByRole('dialog', { name: 'Settings' }).getByText('Open cost dashboard')).toHaveCount(0)
})

test('Settings stays pinned while the menu scrolls, and a thread is one short line', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 360 })
  await open(page)
  await expect(page.locator('.side-foot').getByRole('button', { name: 'Settings' })).toBeInViewport()
  const menu = page.locator('.side-menu')
  expect(await menu.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
  const row = await page.locator('.thread-open').first().boundingBox()
  expect(row!.height).toBeLessThan(40)
})

test('the project picker opens fully on screen from the sidebar', async ({ page }) => {
  await open(page)
  await page.locator('.project-trigger').click()
  await expect(page.locator('.project-panel')).toBeInViewport({ ratio: 1 })
})

const centred = async (page: Page) => {
  const middle = (selector: string) => page.locator(selector).evaluate((el) => {
    const { left, right } = el.getBoundingClientRect()
    return (left + right) / 2
  })
  expect(await page.locator('.topbar-title').evaluate((el) => getComputedStyle(el).textAlign)).toBe('center')
  expect(await middle('.topbar-title')).toBe(await middle('.main'))
}

const dividers = async (page: Page) => {
  const bottom = (selector: string) => page.locator(selector).evaluate((el) => el.getBoundingClientRect().bottom)
  await expect(page.locator('.topbar-title')).toBeVisible()
  expect(await bottom('.threads-head')).toBe(await bottom('.topbar'))
}

test('the top bar and the sidebar header end on one line', async ({ page }) => {
  await open(page)
  await dividers(page)
  await centred(page)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })

  test('the top bar and the sidebar header end on one line', async ({ page }) => {
    await open(page)
    await page.getByRole('button', { name: 'Menu', exact: true }).click()
    await dividers(page)
    await centred(page)
  })

  test('the menu button stays on screen and opens and closes the menu', async ({ page }) => {
    await open(page)
    const button = page.getByRole('button', { name: 'Menu', exact: true })
    await expect(page.locator('.threads')).not.toBeInViewport()
    await button.click()
    await expect(page.locator('.side-menu .menu-item').first()).toBeInViewport()
    await button.click()
    await expect(page.locator('.threads')).not.toBeInViewport()
    await button.click()
    await page.getByRole('button', { name: 'Task graph', exact: true }).click()
    await expect(page.locator('.threads')).not.toBeInViewport()
    await expect(button).toBeInViewport()
  })
})
