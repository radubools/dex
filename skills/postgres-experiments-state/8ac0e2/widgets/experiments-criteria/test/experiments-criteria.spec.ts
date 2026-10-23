import { readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

/**
 * Both halves of this widget, against real rows.
 *
 * The fixtures came out of the live `experiments` schema: `doc-5-r4.criteria.json`
 * is `dashboard.export` of the task `change-order-risk-criteria-doc-5/r4` (28
 * criteria, every one with a recorded `hits`), and `chunks.json` is the very
 * file that package chunked its document into.
 *
 * -   The **offline** tests open the self-contained export. No network.
 * -   The **live** tests open a three-line pointer and make the widget query
 *     `/api/sql` for everything, with `serve_standalone.py` on 4399 standing in
 *     for dex's own server. That is the path that matters — it is the one a
 *     precomputed file was replaced by — and it is tested against the real
 *     database, not a stub, because the queries are most of the widget.
 */

const REPO = fileURLToPath(new URL('../../../../../../', import.meta.url))

/**
 * This widget's own bundle, repo-relative, passed to the harness as `?src=`.
 *
 * Never `?widget=experiments-criteria` alone: that resolves through
 * `widgets/index.json`, which maps one name to one path — and `build.mjs`
 * regenerates it from whatever it built last, so with several published versions
 * of this skill on disk the winner is alphabetical accident. It pointed at a
 * *published* bundle while these tests were "passing" against a rewritten
 * source, which is the most expensive kind of green.
 */
const SRC = relative(REPO, fileURLToPath(new URL('../dist/index.js', import.meta.url)))
const HARNESS = `/widgets/harness.html?widget=experiments-criteria&src=${encodeURIComponent(SRC)}`

/** The three choosers, by their `data-role`, and the criterion options. */
const MODEL = '#root .cd-select[data-role=model]'
const ROUND = '#root .cd-select[data-role=round]'
const CRITERION = '#root .cd-select[data-role=criterion]'
const OPTIONS = `${CRITERION} option`
const FILTER = '#root .cd-filter'
const FIXTURES = fileURLToPath(new URL('./fixtures/', import.meta.url))
const API_PORT = 4399

const read = (name: string) => readFileSync(join(FIXTURES, name), 'utf8')

async function mount(page: Page, file: string, theme = 'dark') {
  const text = read(file)
  await page.goto(HARNESS)
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(
    ([path, body, mode, chunks]) =>
      (window as any).__mount({ path, text: body, theme: mode, assets: { 'chunks.json': chunks } }),
    [
      `construction/change-order-risk-criteria-doc-5/${file}`,
      text,
      theme,
      read('chunks.json'),
    ],
  )
  return JSON.parse(text)
}

// ---------------------------------------------------------------------------
// Offline: the self-contained export
// ---------------------------------------------------------------------------

test('lists every criterion by recorded hits, descending', async ({ page }) => {
  const doc = await mount(page, 'doc-5-r4.criteria.json')
  await expect(page.locator('#error')).toBeHidden()

  const items = page.locator(OPTIONS)
  await expect(items).toHaveCount(doc.criteria.length)

  // Each option reads "<name>  <hits>", which is both what the picker shows and
  // the only ordering signal on a phone where there is no list to eyeball.
  const drawn = await page.$$eval(OPTIONS, (nodes) =>
    nodes.map((n) => {
      const text = (n as HTMLOptionElement).textContent ?? ''
      const at = text.lastIndexOf('  ')
      return { name: text.slice(0, at), hits: text.slice(at + 2).trim() }
    }),
  )
  // Ordering is the request: hits descending. Assert it on what is drawn rather
  // than trusting the file's order, which the widget is meant to impose itself.
  const numbers = drawn.map((d) => (d.hits === '—' ? -1 : Number(d.hits)))
  for (let i = 1; i < numbers.length; i += 1) {
    expect(numbers[i]).toBeLessThanOrEqual(numbers[i - 1])
  }
  // Every name is snake_case, which is what the picker is meant to show.
  for (const { name } of drawn) expect(name).toMatch(/^[a-z0-9_.:]+$/)

  // The most-hit criterion is selected on open, not an empty pane.
  // `toHaveValue`, not `toHaveText`: the text of a <select> is every option
  // concatenated, so a text assertion here passes or fails for the wrong reason.
  await expect(page.locator(CRITERION)).toHaveValue('co_risk.phased_construction')
})

test('cards read the run’s stored metrics and never recompute them', async ({ page }) => {
  const doc = await mount(page, 'doc-5-r4.criteria.json')
  const first = doc.criteria[0]

  // `hits` on the card must equal `metrics.hits` exactly. A recomputed count at
  // the default threshold would differ, which is the whole point of the choice.
  await expect(page.locator('#root .cd-card[data-metric=hits] .cd-card-v')).toHaveText(
    String(first.metrics.hits),
  )
  const lift = page.locator('#root .cd-card[data-metric=lift] .cd-card-v')
  await expect(lift).toHaveText(String(first.metrics.lift.toFixed(4)))
  // A metric this run never recorded reads as a dash, not as zero.
  await expect(page.locator('#root .cd-card[data-metric=J] .cd-card-v')).toHaveText('—')
  // Nothing is hidden: the metrics without a card are still printed.
  await expect(page.locator('#root .cd-rest')).toContainText('mean=')
})

test('lists the chunks above the threshold, with their text, and re-filters', async ({ page }) => {
  const doc = await mount(page, 'doc-5-r4.criteria.json')
  const answers = doc.criteria[0].answers as { chunk: string; verdict: number }[]

  const expected = (t: number) => answers.filter((a) => a.verdict >= t).length
  await expect(page.locator('#root .cd-count').last()).toContainText(
    `${expected(0.3)} of ${answers.length} chunks`,
  )
  await expect(page.locator('#root .cd-chunk')).toHaveCount(expected(0.3))

  // The filter is on the verdict, not on confidence. These answers are yes/no,
  // so a chunk at P(true) 0.03 carries confidence 0.97 -- if the widget filtered
  // on confidence it would show *more* rows at a high threshold, not fewer.
  const low = answers.filter((a) => a.verdict < 0.3)
  expect(low.length).toBeGreaterThan(0)
  for (const a of low) {
    await expect(page.locator(`#root .cd-chunk[data-chunk="${a.chunk}"]`)).toHaveCount(0)
  }

  // Real chunk text, joined from the sibling by id.
  const top = answers.filter((a) => a.verdict >= 0.3).sort((a, b) => b.verdict - a.verdict)[0]
  const row = page.locator(`#root .cd-chunk[data-chunk="${top.chunk}"]`)
  await expect(row).toHaveCount(1)
  await expect(row.locator('.cd-score')).toHaveText(`${(top.verdict * 100).toFixed(0)}%`)
  const text = (doc.chunks[top.chunk].text as string).trim().slice(0, 40)
  await expect(row.locator('.cd-text')).toContainText(text)

  // Dragging the threshold changes the listing and nothing else.
  await page.locator('#root .cd-bar input[type=range]').fill('90')
  await expect(page.locator('#root .cd-chunk')).toHaveCount(expected(0.9))
  await expect(page.locator('#root .cd-card[data-metric=hits] .cd-card-v')).toHaveText(
    String(doc.criteria[0].metrics.hits),
  )
  await page.locator('#root .cd-bar input[type=range]').fill('0')
  await expect(page.locator('#root .cd-chunk')).toHaveCount(answers.length)
})

test('selecting another criterion swaps the cards and the chunks', async ({ page }) => {
  const doc = await mount(page, 'doc-5-r4.criteria.json', 'light')
  const second = doc.criteria[1]
  await page.locator(CRITERION).selectOption(second.name)
  await expect(page.locator(CRITERION)).toHaveValue(second.name)
  await expect(page.locator('#root .cd-q')).toHaveText(second.question.instructions)
  await expect(page.locator('#root .cd-card[data-metric=hits] .cd-card-v')).toHaveText(
    String(second.metrics.hits),
  )
})

test('filters the picker by name', async ({ page }) => {
  await mount(page, 'doc-5-r4.criteria.json')
  await page.locator(FILTER).fill('contingency')
  const remaining = page.locator(OPTIONS)
  await expect(remaining).not.toHaveCount(0)
  for (const text of await remaining.allTextContents()) {
    expect(text).toContain('contingency')
  }
  // ...and the placeholder says how much of the set is left, since the count is
  // no longer visible as a list.
  await expect(page.locator(FILTER)).toHaveAttribute('placeholder', /of \d+ names/)
})

test('says so rather than drawing nothing when the file is not a pointer', async ({ page }) => {
  await page.goto(HARNESS)
  await page.waitForFunction(() => (window as any).__ready)
  for (const [text, expected] of [
    ['not json', /not a criteria file/i],
    ['[]', /top level is not an object/i],
    ['{"format": "experiments.criteria"}', /needs a "task"/i],
  ] as const) {
    const failed = await page
      .evaluate((body) => (window as any).__mount({ path: 'x.criteria.json', text: body }), text)
      .catch((error) => String(error))
    expect(String(failed)).toMatch(expected)
    await expect(page.locator('#error')).toContainText(expected)
  }
})

// ---------------------------------------------------------------------------
// Live: everything through /api/sql
// ---------------------------------------------------------------------------

test.describe('against /api/sql', () => {
  test('reads the criteria and the chunks out of Postgres, not the file', async ({ page }) => {
    // The pointer is three keys. Everything on screen came over HTTP.
    const pointer = JSON.parse(read('live.criteria.json'))
    expect(pointer.criteria).toBeUndefined()

    await mount(page, 'live.criteria.json')
    await expect(page.locator('#error')).toBeHidden()
    await expect(page.locator('#root .cd-head')).toContainText('live')

    // The same 28 criteria the export holds, in the same hits order -- which is
    // the real assertion that the SQL and the exporter agree.
    const offline = JSON.parse(read('doc-5-r4.criteria.json'))
    await expect(page.locator(OPTIONS)).toHaveCount(offline.criteria.length)
    const names = await page.locator(OPTIONS).evaluateAll((nodes) =>
      nodes.map((n) => ((n as HTMLOptionElement).textContent ?? '').split('  ')[0]),
    )
    expect(names).toEqual(offline.criteria.map((c: { name: string }) => c.name))

    // Chunks arrive from the ANSWERS query and are joined to the sibling text.
    const answers = offline.criteria[0].answers as { verdict: number }[]
    await expect(page.locator('#root .cd-chunk')).toHaveCount(
      answers.filter((a) => a.verdict >= 0.3).length,
    )
    await expect(page.locator('#root .cd-chunk').first().locator('.cd-text')).not.toHaveText(
      /^\(no text/,
    )
  })

  test('selecting a criterion fetches that criterion’s chunks', async ({ page }) => {
    const offline = JSON.parse(read('doc-5-r4.criteria.json'))
    const second = offline.criteria[1]
    await mount(page, 'live.criteria.json')
    await page.locator(CRITERION).selectOption(second.name)
    await expect(page.locator('#root .cd-q')).toHaveText(second.question.instructions)
    const answers = second.answers as { verdict: number }[]
    await expect(page.locator('#root .cd-chunk')).toHaveCount(
      answers.filter((a) => a.verdict >= 0.3).length,
    )
  })

  test('says which endpoint it could not reach when the API is down', async ({ page }) => {
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    const failed = await page
      .evaluate(
        (body) => (window as any).__mount({ path: 'x.criteria.json', text: body }),
        JSON.stringify({ task: 'whatever', api: 'http://127.0.0.1:4098' }),
      )
      .catch((error) => String(error))
    // A CORS refusal, a blocked cross-origin request and a dead server are one
    // indistinguishable TypeError with no status, so the message has to name
    // every base it tried and say what else to check. An explicit `api` is used
    // alone, so this names exactly one.
    expect(String(failed)).toMatch(/cannot reach \/api\/sql/)
    expect(String(failed)).toMatch(/Tried: http:\/\/127\.0\.0\.1:4098/)
    expect(String(failed)).toMatch(/router installed/)
    // ...and it must not silently have fallen back to a base nobody named.
    expect(String(failed)).not.toMatch(/4317/)
  })

  test('apiBases: explicit wins alone, otherwise the parent origin then the fallback', async ({
    page,
  }) => {
    // The bug behind the operator's "cannot reach http://127.0.0.1:4317/api/sql
    // (Load failed)" was that the widget knew exactly one host. These are the
    // rules that replaced it.
    //
    // The harness is the *top* window, so there is no parent to derive: no
    // `ancestorOrigins`, no `referrer`. One base — the fallback — is the correct
    // answer here, and the derived case is covered in
    // `widgets/experiments-criteria/test/sandbox.spec.ts`, which is the only place
    // a parent origin exists at all.
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    const seen = await page.evaluate(async (src: string) => {
      const mod = await import(`/${src.replace(/^\/+/, '')}`)
      return {
        unframed: mod.apiBases(),
        explicit: mod.apiBases('http://example.test/'),
        parent: mod.parentOrigin(),
        fallback: mod.FALLBACK_API,
      }
    }, SRC)
    expect(seen.parent).toBe('')
    expect(seen.unframed).toEqual([seen.fallback])
    // An explicit base suppresses the lot, trailing slash trimmed: calling a host
    // nobody named is worse than failing on the one they did.
    expect(seen.explicit).toEqual(['http://example.test'])
  })

  test('a task with no rows says so instead of drawing an empty dashboard', async ({ page }) => {
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    const failed = await page
      .evaluate(
        (body) => (window as any).__mount({ path: 'x.criteria.json', text: body }),
        JSON.stringify({ task: 'no-such-task-exists', api: `http://127.0.0.1:${API_PORT}` }),
      )
      .catch((error) => String(error))
    expect(String(failed)).toMatch(/no criterion .* scored by task "no-such-task-exists"/)
  })
  // -------------------------------------------------------------------------
  // Opening `chunks.json` itself: the file every package already has.
  // -------------------------------------------------------------------------

  test('opening chunks.json derives the task from the directory', async ({ page }) => {
    // No pointer, no `task` key, no api override -- only the store and the path.
    const store = JSON.parse(read('chunks.json'))
    expect(store.task).toBeUndefined()
    expect(store.criteria).toBeUndefined()

    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    await page.evaluate(
      ([path, text, api]) =>
        (window as any).__mount({ path, text: JSON.stringify({ ...JSON.parse(text), api }) }),
      [
        'construction/change-order-risk-criteria-doc-5/chunks.json',
        read('chunks.json'),
        `http://127.0.0.1:${API_PORT}`,
      ],
    )
    await expect(page.locator('#error')).toBeHidden()

    // doc-5 has several tasks, so the picker appears and the most-scored one is
    // chosen. r4 is that one, with the same 28 criteria the export holds.
    await expect(page.locator('#root .cd-task')).toHaveText(/change-order-risk-criteria-doc-5/)
    // The count is asserted against the picker's own label rather than a literal:
    // which of doc-5's tasks has the most criteria is a fact about the database
    // today, and a hard-coded 28 fails the next time a round is published.
    const label = await page.locator(ROUND).inputValue()
    const option = await page.locator(`${ROUND} option[value="${label}"]`).textContent()
    const expected = Number(option!.match(/\((\d+)\)$/)![1])
    expect(expected).toBeGreaterThan(0)
    await expect(page.locator(OPTIONS)).toHaveCount(expected)
    await expect(page.locator('#root .cd-task')).toHaveText(label)
    // The chunk text came out of the opened file, not a sibling fetch: the
    // harness was given no `assets`, so a fetchAsset would have rejected.
    await expect(page.locator('#root .cd-note')).toHaveCount(0)
    await expect(page.locator('#root .cd-chunk').first().locator('.cd-text')).not.toHaveText(
      /^\(no text/,
    )
  })

  test('a package with several tasks offers a picker that switches between them', async ({
    page,
  }) => {
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    await page.evaluate(
      ([path, text, api]) =>
        (window as any).__mount({ path, text: JSON.stringify({ ...JSON.parse(text), api }) }),
      [
        'construction/change-order-risk-criteria-doc-5/chunks.json',
        read('chunks.json'),
        `http://127.0.0.1:${API_PORT}`,
      ],
    )
    const picker = page.locator(ROUND)
    await expect(picker).toHaveCount(1)
    const options = await picker.locator('option').allTextContents()
    expect(options.length).toBeGreaterThan(1)
    // Ordered by criteria count, descending, and each option says the count.
    const counts = options.map((o) => Number(o.match(/\((\d+)\)$/)?.[1] ?? -1))
    for (let i = 1; i < counts.length; i += 1) expect(counts[i]).toBeLessThanOrEqual(counts[i - 1])

    // Switching re-renders against the other task.
    const other = (await picker.locator('option').nth(1).getAttribute('value'))!
    await picker.selectOption(other)
    await expect(page.locator('#root .cd-task')).toHaveText(other)
    await expect(page.locator(OPTIONS)).not.toHaveCount(0)
    // The chunk map was carried across, not re-fetched -- again, no assets were
    // given to the harness, so a refetch would surface as the ids-only note.
    await expect(page.locator('#root .cd-note')).toHaveCount(0)
  })

  test('a package with nothing scored says so, naming the package', async ({ page }) => {
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    const failed = await page
      .evaluate(
        ([path, text, api]) =>
          (window as any).__mount({ path, text: JSON.stringify({ ...JSON.parse(text), api }) }),
        [
          'construction/no-such-package-anywhere/chunks.json',
          read('chunks.json'),
          `http://127.0.0.1:${API_PORT}`,
        ],
      )
      .catch((error) => String(error))
    expect(String(failed)).toMatch(/names the package "no-such-package-anywhere"/)
    expect(String(failed)).toMatch(/Nothing has been scored here yet/)
  })

  test('the package regex does not let doc-1 match doc-13', async ({ page }) => {
    await page.goto(HARNESS)
    await page.waitForFunction(() => (window as any).__ready)
    const tasks = await page.evaluate(
      async ([api, src]) => {
        const mod = await import(`/${src.replace(/^\/+/, '')}`)
        const rows = await mod.ask(api, mod.TASKS_SQL, [
          mod.taskPattern('change-order-risk-criteria-doc-1'),
        ])
        return rows.map((r: { task: string }) => r.task)
      },
      [`http://127.0.0.1:${API_PORT}`, SRC],
    )
    expect(tasks.length).toBeGreaterThan(0)
    for (const t of tasks) {
      expect(t).toMatch(/(^|\/)change-order-risk-criteria-doc-1($|[/#])/)
      expect(t).not.toMatch(/doc-1[0-9]/)
    }
  })
})
