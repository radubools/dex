import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

/**
 * The widget is opened on a `*.score.json` sidecar, and everything it draws
 * comes out of the `.mid` that sidecar names. So these tests hand it real
 * Standard MIDI Files and ask the question that matters: is the cursor on the
 * note you can hear?
 *
 * They run against a real composition from `assets/music` as soon as the
 * project has one, and against the generated fixtures beside this file until
 * then -- the project is new, and a test that cannot run before the first
 * package exists is a test nobody ever writes.
 */

/**
 * Both roots are resolved from this file rather than from the working
 * directory: a task runs the suite from its own package directory, where a
 * path starting `assets/` or `widgets/` points at nothing.
 */
const REPO = fileURLToPath(new URL('../../../', import.meta.url))
const FIXTURES = join(REPO, 'widgets/music-score/test/fixtures')

/** The first real composition in the project, or the fixture standing in. */
function composition(): { dir: string; sidecar: string } {
  const root = join(REPO, 'assets/music')
  if (existsSync(root)) {
    for (const entry of readdirSync(root).sort()) {
      const dir = join(root, entry)
      if (!statSync(dir).isDirectory()) continue
      const found = readdirSync(dir).find((name) => name.endsWith('.score.json'))
      if (found) return { dir, sidecar: found }
    }
  }
  return { dir: FIXTURES, sidecar: 'piece.score.json' }
}

/**
 * Mount the widget on a sidecar, handing it the siblings it names.
 *
 * The binaries go over as base64 and are turned back into bytes inside the
 * page: a `.mid` put through `page.evaluate` as a string comes out re-encoded
 * as UTF-8, which is no longer a MIDI file.
 */
async function mount(page: Page, dir: string, sidecar: string, theme = 'dark') {
  const text = readFileSync(join(dir, sidecar), 'utf8')
  const meta = JSON.parse(text) as { midi?: string; audio?: string }
  const stem = sidecar.replace(/\.score\.json$/, '')
  const siblings = [meta.midi ?? `${stem}.mid`, meta.audio ?? `${stem}.wav`]
  const encoded: Record<string, string> = {}
  for (const name of siblings) {
    if (existsSync(join(dir, name))) encoded[name] = readFileSync(join(dir, name)).toString('base64')
  }
  await page.goto('/widgets/harness.html?widget=music-score')
  await page.waitForFunction(() => (window as any).__ready)
  return page.evaluate(
    ([path, body, assets, mode]: any) => {
      const bytes = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      return (window as any).__mount({
        path,
        text: body,
        theme: mode,
        assets: Object.fromEntries(Object.entries(assets).map(([k, v]) => [k, bytes(v as string)])),
      })
    },
    [`music/piece/${sidecar}`, text, encoded, theme],
  )
}

/** Every struck notehead: its time, and where on the page it was drawn. */
async function noteheads(page: Page) {
  return page.$$eval('#root g.note:not(.held)', (groups) =>
    groups.map((group) => {
      const head = group.querySelector('ellipse') as SVGEllipseElement
      const box = head.getBoundingClientRect()
      return {
        start: Number((group as HTMLElement).dataset.s),
        cx: Number(head.getAttribute('cx')),
        pageX: box.left + box.width / 2,
        pageY: box.top + box.height / 2,
      }
    }),
  )
}

const cursorX = (page: Page) =>
  page.$eval('#root .cursor', (line) => Number(line.getAttribute('x1')))

test('draws a score from a real MIDI file', async ({ page }) => {
  const { dir, sidecar } = composition()
  await mount(page, dir, sidecar)
  await expect(page.locator('#error')).toBeHidden()
  await expect(page.locator('#root svg')).toBeVisible()
  // Noteheads, not just an SVG: an empty staff is the failure that still draws.
  expect((await noteheads(page)).length).toBeGreaterThan(0)
  await expect(page.locator('#root')).toContainText(/bpm/)
})

test('the cursor lands on the note that sounds at that moment', async ({ page }) => {
  const { dir, sidecar } = composition()
  await mount(page, dir, sidecar)
  const heads = await noteheads(page)
  // Clicking the score seeks to the time under the pointer, so clicking a
  // notehead should put the cursor on that notehead -- including on the later
  // systems, where a mistake in the wrapping would show up as a whole line out.
  //
  // A later system is below the fold of the scrolling score, and a page
  // coordinate measured before scrolling points at whatever happens to be
  // there instead: the click misses and the cursor never moves, which reads as
  // a widget bug rather than the test aiming badly. So each head is scrolled
  // into view and re-measured immediately before it is clicked.
  for (const index of [0, Math.floor(heads.length / 2), heads.length - 1]) {
    const head = await page.evaluate((i) => {
      const group = document.querySelectorAll('#root g.note:not(.held)')[i]
      group.scrollIntoView({ block: 'center' })
      const notehead = group.querySelector('ellipse') as SVGEllipseElement
      const box = notehead.getBoundingClientRect()
      return {
        cx: Number(notehead.getAttribute('cx')),
        pageX: box.left + box.width / 2,
        pageY: box.top + box.height / 2,
      }
    }, index)
    await page.mouse.click(head.pageX, head.pageY)
    // Within a notehead's width, not to the pixel: the click lands on a whole
    // pixel and the audio element rounds the seek to its own frame, so a couple
    // of pixels of slack is the seek working. The failure this guards against
    // -- the wrong system, or the wrong bar -- is a hundred pixels out.
    await expect.poll(async () => Math.abs((await cursorX(page)) - head.cx)).toBeLessThan(3)
  }
})

test('the cursor keeps up with the audio as it plays', async ({ page }) => {
  const { dir, sidecar } = composition()
  await mount(page, dir, sidecar)
  const heads = await noteheads(page)
  // Pixels per second, read back off the drawing itself rather than assumed:
  // two noteheads on the first system and the times they carry.
  const early = heads[0]
  const later = heads.find((h) => h.start > early.start && h.cx > early.cx)!
  const pxPerSecond = (later.cx - early.cx) / (later.start - early.start)
  expect(pxPerSecond).toBeGreaterThan(0)

  const sample = () =>
    page.evaluate(() => ({
      x: Number(document.querySelector('#root .cursor')!.getAttribute('x1')),
      at: performance.now(),
    }))
  const parked = await cursorX(page)
  await page.click('#root button')
  // Playback does not begin the instant the button is clicked -- the element
  // decodes first -- so measure from the moment the cursor actually moves.
  // Charging that start-up to the tracking is measuring the wrong thing.
  await expect.poll(() => cursorX(page), { timeout: 10_000 }).toBeGreaterThan(parked + 2)
  const first = await sample()
  await page.waitForTimeout(3000)
  const second = await sample()

  const elapsed = (second.at - first.at) / 1000
  const moved = second.x - first.x
  expect(elapsed).toBeGreaterThan(2)
  // A cursor on a fixed guess, or one wired to the wrong clock, fails here: it
  // has to cover the same pixels-per-second the noteheads were placed with.
  // The band is wide because the reference is the wall clock and headless
  // Chromium plays to a null audio sink that runs a few percent behind it;
  // exact agreement between cursor and score is the seeking test above, which
  // compares the two drawings rather than two clocks.
  expect(moved / elapsed).toBeGreaterThan(pxPerSecond * 0.8)
  expect(moved / elapsed).toBeLessThan(pxPerSecond * 1.2)
})

test('a note held across a system break is not struck twice', async ({ page }) => {
  await mount(page, FIXTURES, 'piece.score.json')
  // The carried-over fragment draws its sustain, and no notehead: a second
  // head would tell the reader to play the note again.
  const held = await page.$$eval('#root g.note.held', (groups) => ({
    fragments: groups.length,
    heads: groups.filter((g) => g.querySelector('ellipse')).length,
  }))
  expect(held.fragments).toBeGreaterThan(0)
  expect(held.heads).toBe(0)
})

test('splits one wide track onto a grand staff and spells the key', async ({ page }) => {
  // Format 0, running status, 2/4, three flats, one track crossing middle C.
  await mount(page, FIXTURES, 'piano.score.json', 'light')
  await expect(page.locator('#error')).toBeHidden()
  const glyphs = await page.$$eval('#root text', (nodes) => nodes.map((n) => n.textContent))
  expect(glyphs).toContain('\u{1D11E}')
  expect(glyphs).toContain('\u{1D122}')
  expect(glyphs).toContain('♭')
  expect(glyphs).not.toContain('♯')
})

test('draws the score but disables playback when no audio sits beside it', async ({ page }) => {
  await mount(page, FIXTURES, 'piano.score.json')
  expect((await noteheads(page)).length).toBeGreaterThan(0)
  await expect(page.locator('#root button')).toBeDisabled()
  await expect(page.locator('#root')).toContainText(/no rendered audio/i)
})

test('says so rather than drawing nothing when the MIDI is not a MIDI file', async ({ page }) => {
  await page.goto('/widgets/harness.html?widget=music-score')
  await page.waitForFunction(() => (window as any).__ready)
  const failed = await page
    .evaluate(() =>
      (window as any).__mount({
        path: 'music/x/broken.score.json',
        text: '{"midi": "broken.mid"}',
        assets: { 'broken.mid': new TextEncoder().encode('this is not a MIDI file') },
      }),
    )
    .catch((error) => String(error))
  // A blank frame is the worst outcome: nobody can tell a broken widget from
  // a piece with no notes in it.
  expect(String(failed)).toMatch(/not a MIDI file/i)
  await expect(page.locator('#error')).toContainText(/not a MIDI file/i)
})
