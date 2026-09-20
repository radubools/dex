import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

/**
 * The widget is opened on a `*.bitext.json` and draws every numbered chunk
 * twice: the source paragraph, then the translation under it. So these tests
 * ask the two questions that matter — are all the chunks there, and are they
 * paired with the right translation?
 *
 * They run against the first real document in `assets/traduceri` as soon as the
 * project has one, and against the fixtures beside this file until then. Both
 * fixtures are real: the source side of `bucharest` came out of a live crawl
 * and the source side of `rsmeans` out of a scanned PDF, both through
 * `fixtures/extract.py`.
 */

/** Resolved from this file: a task runs the suite from its own package directory. */
const REPO = fileURLToPath(new URL('../../../', import.meta.url))
const FIXTURES = join(REPO, 'widgets/bitext/test/fixtures')

/** The first real bitext in the project, or the fixture standing in for it. */
function document_(): { dir: string; name: string } {
  const root = join(REPO, 'assets/traduceri')
  if (existsSync(root)) {
    for (const entry of readdirSync(root).sort()) {
      const dir = join(root, entry)
      if (!statSync(dir).isDirectory()) continue
      const found = readdirSync(dir)
        .sort()
        .find((name) => name.endsWith('.bitext.json'))
      if (found) return { dir, name: found }
    }
  }
  return { dir: FIXTURES, name: 'bucharest.bitext.json' }
}

async function mount(page: Page, dir: string, name: string, theme = 'dark') {
  const text = readFileSync(join(dir, name), 'utf8')
  await page.goto('/widgets/harness.html?widget=bitext')
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(
    ([path, body, mode]) => (window as any).__mount({ path, text: body, theme: mode }),
    [`traduceri/doc/${name}`, text, theme],
  )
  return JSON.parse(text) as { segments: { n: number; source: string; target: string }[] }
}

test('draws every numbered chunk with its translation beneath it', async ({ page }) => {
  const { dir, name } = document_()
  const doc = await mount(page, dir, name)
  await expect(page.locator('#error')).toBeHidden()

  const rows = page.locator('#root .bx-seg')
  await expect(rows).toHaveCount(doc.segments.length)

  // Pairing, not just presence: an off-by-one in the alignment still renders a
  // full, plausible-looking document, and that is the failure worth catching.
  const drawn = await page.$$eval('#root .bx-seg', (nodes) =>
    nodes.map((node) => ({
      n: Number((node as HTMLElement).dataset.n),
      source: node.querySelector('.bx-src')?.textContent ?? '',
      target: node.querySelector('.bx-tgt')?.textContent ?? '',
    })),
  )
  for (const [index, seg] of doc.segments.entries()) {
    expect(drawn[index].n).toBe(seg.n)
    expect(drawn[index].source).toBe(seg.source)
    if (seg.target.trim()) expect(drawn[index].target).toBe(seg.target)
  }

  // The source is drawn above its own translation, not in a separate column.
  const order = await page.$eval('#root .bx-seg .bx-body', (body) =>
    [...body.children].map((child) => child.className),
  )
  expect(order[0]).toBe('bx-src')
  expect(order[1]).toContain('bx-tgt')
})

test('shows a proofreader the notes, the suggestions and the gaps', async ({ page }) => {
  await mount(page, FIXTURES, 'rsmeans.bitext.json', 'light')
  await expect(page.locator('#error')).toBeHidden()
  await expect(page.locator('#root .bx-chip')).toContainText(['en → ro', 'proofread'])
  await expect(page.locator('#root .bx-note')).toHaveCount(4)
  await expect(page.locator('#root .bx-sugg')).toHaveCount(2)
  // An empty target has to read as a hole, not as a short paragraph.
  await expect(page.locator('#root .bx-missing')).toHaveCount(1)
  await expect(page.locator('#root .bx-bar')).toContainText('8 segments')

  // The filter is the reason to open this file at all: leave only the work.
  await page.locator('#root .bx-bar input[type=checkbox]').check()
  await expect(page.locator('#root .bx-seg:visible')).toHaveCount(4)
})

test('says the numbering is broken instead of quietly renumbering it', async ({ page }) => {
  await page.goto('/widgets/harness.html?widget=bitext')
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(() =>
    (window as any).__mount({
      path: 'traduceri/doc/gap.bitext.json',
      text: JSON.stringify({
        source: { lang: 'en' },
        target: { lang: 'ro' },
        segments: [
          { n: 1, source: 'One.', target: 'Unu.' },
          { n: 4, source: 'Four.', target: 'Patru.' },
        ],
      }),
    }),
  )
  await expect(page.locator('#root .bx-warn')).toContainText('jumps from 1 to 4')
})

test('says so rather than drawing nothing when the file is not a bitext', async ({ page }) => {
  await page.goto('/widgets/harness.html?widget=bitext')
  await page.waitForFunction(() => (window as any).__ready)
  for (const [text, expected] of [
    ['this is not JSON at all', /not a bitext file/i],
    ['{"title": "no segments here"}', /no "segments" array/i],
    ['{"segments": []}', /no segments/i],
  ] as const) {
    const failed = await page
      .evaluate((body) => (window as any).__mount({ path: 'traduceri/doc/x.bitext.json', text: body }), text)
      .catch((error) => String(error))
    // A blank frame is the worst outcome: nobody can tell a broken widget from
    // a document with nothing in it.
    expect(String(failed)).toMatch(expected)
    await expect(page.locator('#error')).toContainText(expected)
  }
})
