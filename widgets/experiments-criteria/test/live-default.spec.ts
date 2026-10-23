import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

/**
 * The one thing the skill's own suite cannot prove: that the port the widget
 * ships with actually works.
 *
 * Every fixture inside the skill sets `"api": "http://127.0.0.1:4399"` and talks
 * to `serve_standalone.py`, which is right for a hermetic test — it must pass
 * whether or not the router has been installed into dex. But it means the bases
 * a real file relies on by *omitting* that key are never exercised. These mount
 * with no `api` at all and read real rows through the installed endpoint.
 *
 * Note which base that is here: the harness page is not framed, so
 * `parentOrigin()` finds nothing and `apiBases()` is just `FALLBACK_API` —
 * dex's own port. The *derived* origin is covered in `sandbox.spec.ts`, which is
 * the only place a parent origin exists to derive.
 *
 * It lives at the top level rather than in the skill because it is a check on
 * the *installation*, not on the widget: it fails when `/api/sql` is missing
 * from `src/dex/api.py`, which is not a defect in the bundle. This directory
 * carries no `widget.json`, so `build.mjs` and dex's own widget discovery both
 * ignore it.
 *
 * It passes the bundle explicitly as `?src=`. Resolving by name goes through
 * `widgets/index.json`, which holds one path per widget name and is regenerated
 * from whatever `build.mjs` built last — with nine published versions of this
 * skill on disk the winner is alphabetical accident, and it silently pointed
 * these tests at a *published* bundle while the source under test had been
 * rewritten. Green for the wrong reason is worse than red.
 */

const REPO = fileURLToPath(new URL('../../../', import.meta.url))

/** The bundle about to ship: the working draft if there is one, else the pinned version. */
function bundleUnderTest(): string {
  const skills = join(REPO, 'skills/postgres-experiments-state')
  const pinned = JSON.parse(
    readFileSync(join(REPO, 'assets/construction/skills.json'), 'utf8'),
  ).skills['postgres-experiments-state'] as string
  for (const version of ['draft', pinned]) {
    const rel = `skills/postgres-experiments-state/${version}/widgets/experiments-criteria/dist/index.js`
    if (existsSync(join(REPO, rel))) return rel
  }
  throw new Error(`no built experiments-criteria under ${skills}`)
}

const SRC = bundleUnderTest()
const HARNESS = `/widgets/harness.html?widget=experiments-criteria&src=${encodeURIComponent(SRC)}`
const PACKAGE = join(REPO, 'assets/construction/change-order-risk-criteria-doc-5')
const TASK = 'change-order-risk-criteria-doc-5/r4'

async function mountDefault(page: Page): Promise<void> {
  // No `api` key: unframed, so the widget falls back to dex's own port.
  const pointer = JSON.stringify({
    format: 'experiments.criteria',
    task: TASK,
    chunks: 'chunks.json',
  })
  expect(pointer).not.toContain('4399')
  expect(pointer).not.toContain('api')

  await page.goto(HARNESS)
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(
    ([path, text, chunks]) =>
      (window as any).__mount({ path, text, assets: { 'chunks.json': chunks } }),
    [
      `construction/change-order-risk-criteria-doc-5/doc-5.criteria.json`,
      pointer,
      readFileSync(join(PACKAGE, 'chunks.json'), 'utf8'),
    ],
  )
}

/**
 * Mount exactly what a click does: the project's own `chunks.json`, at its own
 * path, with no pointer and no `api` override.
 *
 * This is the only test that covers the whole production path end to end — the
 * filename rule in `widgets.json`, the task derived from the directory, the
 * store read out of the opened file, and the installed endpoint on 4317.
 */
async function mountByClicking(page: Page, pkg: string): Promise<void> {
  await page.goto(HARNESS)
  await page.waitForFunction(() => (window as any).__ready)
  await page.evaluate(
    ([path, text]) => (window as any).__mount({ path, text }),
    [
      `construction/${pkg}/chunks.json`,
      readFileSync(join(REPO, 'assets/construction', pkg, 'chunks.json'), 'utf8'),
    ],
  )
}

test('the fallback base reaches the installed /api/sql', async ({ page }) => {
  await mountDefault(page)
  await expect(page.locator('#error')).toBeHidden()
  await expect(page.locator('#root .cd-head')).toContainText('live')

  // 28 criteria is what this task has in experiments.state today; asserting the
  // count rather than ">0" is what would catch the query silently collapsing.
  await expect(page.locator('#root .cd-select[data-role=criterion] option')).toHaveCount(28)
  await expect(page.locator('#root .cd-select[data-role=criterion]')).toHaveValue(
    'co_risk.phased_construction',
  )
  // The hits ride in the option label, which is the only ordering cue left.
  await expect(
    page.locator('#root .cd-select[data-role=criterion] option').first(),
  ).toHaveText(/ {2}29$/)

  // Real chunk text, joined by id to the sibling the pointer named.
  const first = page.locator('#root .cd-chunk').first()
  await expect(first.locator('.cd-text')).not.toHaveText(/^\(no text/)
  await expect(page.locator('#root .cd-count').last()).toContainText('of 36 chunks')
})

test('the threshold still filters against live rows', async ({ page }) => {
  await mountDefault(page)
  const atDefault = await page.locator('#root .cd-chunk').count()
  await page.locator('#root .cd-bar input[type=range]').fill('95')
  const atNinetyFive = await page.locator('#root .cd-chunk').count()
  expect(atNinetyFive).toBeLessThan(atDefault)
  // The cards are the run's recorded numbers and must not move with the slider.
  await expect(page.locator('#root .cd-card[data-metric=hits] .cd-card-v')).toHaveText('29')
})

test('clicking a package’s own chunks.json draws its dashboard', async ({ page }) => {
  // The rule in widgets.json claims this filename in 87 packages; this is one of
  // them, opened exactly as dex would open it.
  await mountByClicking(page, 'change-order-risk-criteria-doc-10')
  await expect(page.locator('#error')).toBeHidden()
  await expect(page.locator('#root .cd-head')).toContainText('live')
  await expect(page.locator('#root .cd-task')).toHaveText(
    /change-order-risk-criteria-doc-10/,
  )

  // doc-10 is scored by both models in one round, so the model chooser offers
  // both plus "both" -- the head no longer carries a chip per model, the
  // dropdown is where that lives now.
  await expect(page.locator('#root .cd-select[data-role=model] option')).toHaveCount(3)
  await expect(page.locator('#root .cd-select[data-role=model]')).toHaveValue('')

  // No sibling was offered to the harness, so the chunk text can only have come
  // out of the opened file. An ids-only fallback would have raised the note.
  await expect(page.locator('#root .cd-note')).toHaveCount(0)
  await expect(page.locator('#root .cd-chunk').first().locator('.cd-text')).not.toHaveText(
    /^\(no text/,
  )

  // doc-10 is the one package in the project whose answers are stored as bare
  // numbers rather than objects, so every chunk here has a score and no
  // confidence. Before the query normalised the three shapes this listing was
  // empty — 243 criteria, every verdict NULL, and no error anywhere.
  await expect(page.locator('#root .cd-chunk')).not.toHaveCount(0)
  await expect(page.locator('#root .cd-chunk').first().locator('.cd-conf')).toHaveCount(0)

  // doc-10 has exactly one round, so that chooser is hidden; the model chooser
  // is not. Assert the hidden state rather than absence: both are always built.
  await expect(page.locator('#root .cd-select[data-role=round]')).toBeHidden()
  await expect(page.locator('#root .cd-select[data-role=model]')).toBeVisible()
})

test('a package with a chunks.json but nothing scored explains itself', async ({ page }) => {
  // 11 of the 87 packages holding a chunks.json have no rows. Opening one must
  // say why rather than showing an empty frame.
  const fs = await import('node:fs')
  const root = join(REPO, 'assets/construction')
  const withStore = fs
    .readdirSync(root)
    .filter((d) => fs.existsSync(join(root, d, 'chunks.json')))
  let bare: string | null = null
  for (const pkg of withStore) {
    const r = await fetch(
      `http://127.0.0.1:4317/api/sql?sql=${encodeURIComponent(
        "SELECT count(*) AS n FROM experiments.history h JOIN experiments.state s ON s.id = h.state_id WHERE s.kind IN ('laya_criterion','jev_criterion') AND h.task ~ $1",
      )}&params=${encodeURIComponent(JSON.stringify([`(^|/)${pkg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}($|[/#])`]))}`,
    )
    if ((await r.json()).rows[0].n === 0) {
      bare = pkg
      break
    }
  }
  expect(bare, 'expected at least one package with a store but no rows').not.toBeNull()

  await page.goto(HARNESS)
  await page.waitForFunction(() => (window as any).__ready)
  const failed = await page
    .evaluate(
      ([path, text]) => (window as any).__mount({ path, text }),
      [
        `construction/${bare}/chunks.json`,
        readFileSync(join(root, bare!, 'chunks.json'), 'utf8'),
      ],
    )
    .catch((error) => String(error))
  expect(String(failed)).toMatch(new RegExp(`names the package "${bare}"`))
})

// ---------------------------------------------------------------------------
// The model chooser and the portrait layout
// ---------------------------------------------------------------------------

const MODEL = '#root .cd-select[data-role=model]'
const ROUND = '#root .cd-select[data-role=round]'
const CRITERION = '#root .cd-select[data-role=criterion]'

test('the model dropdown filters in place when both models share one round', async ({ page }) => {
  // doc-10 keeps jev-latest (124) and laya (119) in the same task, which 6 of the
  // 21 two-model packages do. Switching must not change the round.
  await mountByClicking(page, 'change-order-risk-criteria-doc-10')
  await expect(page.locator('#error')).toBeHidden()

  const options = await page.locator(`${MODEL} option`).allTextContents()
  expect(options[0]).toMatch(/^both/)
  await expect(page.locator(MODEL)).toHaveValue('')
  expect(options.join(' ')).toMatch(/jev-latest/)
  expect(options.join(' ')).toMatch(/laya/)

  const round = await page.locator(ROUND).inputValue().catch(() => '')
  const both = await page.locator(`${CRITERION} option`).count()

  await page.locator(MODEL).selectOption('laya')
  const layaOnly = await page.locator(`${CRITERION} option`).count()
  expect(layaOnly).toBeLessThan(both)
  // Every remaining criterion really is Laya's: the detail line names the model.
  await expect(page.locator('#root .cd-meta')).toContainText('laya')

  await page.locator(MODEL).selectOption('jev-latest')
  await expect(page.locator('#root .cd-meta')).toContainText('jev-latest')
  const jevOnly = await page.locator(`${CRITERION} option`).count()
  expect(jevOnly + layaOnly).toBe(both)

  // Same round throughout -- no refetch was needed.
  if (round) await expect(page.locator(ROUND)).toHaveValue(round)
})

test('the model dropdown changes round when the models live in different ones', async ({
  page,
}) => {
  // doc-1 has laya in run_round.py and jev-latest in ask_jev.py. This is the
  // majority case (15 of 21), and the one a filter-in-place would have shown as
  // an empty list.
  await mountByClicking(page, 'change-order-risk-criteria-doc-1')
  await expect(page.locator('#error')).toBeHidden()

  await page.locator(MODEL).selectOption('laya')
  await expect(page.locator(ROUND)).toHaveValue(/run_round\.py/)
  await expect(page.locator('#root .cd-task')).toHaveText(/run_round\.py/)
  await expect(page.locator('#root .cd-meta')).toContainText('laya')
  await expect(page.locator(`${CRITERION} option`)).not.toHaveCount(0)
  await expect(page.locator('#root .cd-chunk')).not.toHaveCount(0)

  await page.locator(MODEL).selectOption('jev-latest')
  await expect(page.locator(ROUND)).toHaveValue(/ask_jev\.py/)
  await expect(page.locator('#root .cd-task')).toHaveText(/ask_jev\.py/)
  await expect(page.locator('#root .cd-meta')).toContainText('jev-latest')
  await expect(page.locator(`${CRITERION} option`)).not.toHaveCount(0)
})

test('nothing overflows sideways on a phone in portrait', async ({ page }) => {
  // iPhone 14 logical portrait width. The old layout put a 260px criterion list
  // beside the detail, which left the chunk text unreadable at this size.
  await page.setViewportSize({ width: 390, height: 844 })
  await mountByClicking(page, 'change-order-risk-criteria-doc-5')
  await expect(page.locator('#error')).toBeHidden()

  const box = await page.evaluate(() => {
    const root = document.querySelector('#root .cd') as HTMLElement
    const criterion = document.querySelector(
      '#root .cd-select[data-role=criterion]',
    ) as HTMLElement
    const main = document.querySelector('#root .cd-main') as HTMLElement
    const widest = Math.max(
      ...[...document.querySelectorAll('#root *')].map((n) => (n as HTMLElement).scrollWidth),
    )
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      widest,
      criterionBottom: criterion.getBoundingClientRect().bottom,
      mainTop: main.getBoundingClientRect().top,
      columns: getComputedStyle(root).gridTemplateColumns,
      direction: getComputedStyle(root).flexDirection,
    }
  })

  // No horizontal scrolling, and nothing inside is wider than the viewport.
  expect(box.scrollWidth).toBeLessThanOrEqual(box.clientWidth)
  expect(box.widest).toBeLessThanOrEqual(390)
  // The criterion chooser sits *above* the detail, which is what was asked for.
  expect(box.criterionBottom).toBeLessThanOrEqual(box.mainTop)
  // One column, not the old two.
  expect(box.direction).toBe('column')
})
