import { expect, test, type Page } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Served from the repository root by the widget test server, so the fixture is
// addressed by its path relative to it.
const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..', '..', '..', '..', '..')
const FIXTURE = '/' + join(HERE, 'fixtures', 'tower.glb').slice(REPO.length + 1)

const MANIFEST = JSON.stringify({ name: 'test-tower', kind: 'landmark', footprint: [2, 2], stages: 4 })

async function mount(page: Page, opts: { theme?: string; bytes?: 'fixture' | 'junk'; manifest?: boolean } = {}) {
  await page.goto('/widgets/harness.html?widget=glb-viewer')
  await page.waitForFunction(() => (window as any).__ready)
  return page.evaluate(
    async ([fixture, manifest, theme, junk]) => {
      const glb = junk ? new TextEncoder().encode('not a glb').buffer : await (await fetch(fixture as string)).arrayBuffer()
      const assets: Record<string, unknown> = { 'tower.glb': glb }
      if (manifest) assets['manifest.json'] = manifest
      return (window as any).__mount({ path: 'game/test-tower/tower.glb', text: '', assets, theme })
    },
    [FIXTURE, opts.manifest === false ? '' : MANIFEST, opts.theme ?? 'dark', opts.bytes === 'junk'] as const,
  )
}

test('loads the model and reports what is in it', async ({ page }) => {
  await mount(page)
  await expect(page.locator('#root canvas')).toBeAttached()
  const info = page.locator('.glbv-info')
  await expect(info).toContainText('146')        // triangles
  await expect(info).toContainText('landmark')   // manifest kind
  await expect(info).toContainText('wave')
  await expect(info).toContainText('2.26 × 2.26 tiles, 5.3 high')  // bounds, scaffold included
  await expect(info).toContainText('2 × 2 tiles (manifest)')
  const found = await page.evaluate(() => (document.querySelector('.glbv') as any).__glb)
  expect(found.stages).toEqual(['stage_01', 'stage_02', 'stage_03', 'stage_04'])
  expect(found.team).toBe(1)
  expect(found.emissive).toBe(1)
  if (process.env.GLB_SHOTS) await page.screenshot({ path: join(process.env.GLB_SHOTS, 'glb-dark.png') })
})

test('construction slider builds bottom-up', async ({ page }) => {
  await mount(page)
  const slider = page.locator('input[data-role=stages]')
  await slider.fill('2')
  await expect(page.locator('.glbv-bar')).toContainText('2/4')
  if (process.env.GLB_SHOTS) await page.screenshot({ path: join(process.env.GLB_SHOTS, 'glb-stage2.png') })
})

test('team swatches, projection and night toggle', async ({ page }) => {
  await mount(page, { theme: 'light' })
  const swatches = page.locator('.glbv-swatch')
  await expect(swatches).toHaveCount(10)
  await swatches.nth(1).click()
  await expect(swatches.nth(1)).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Ortho' }).click()
  expect(await page.evaluate(() => (document.querySelector('.glbv') as any).__glb.camera())).toBe('perspective')
  await page.getByRole('button', { name: 'Night' }).click()
  expect(await page.evaluate(() => (document.querySelector('.glbv') as any).__glb.night())).toBe(true)
  if (process.env.GLB_SHOTS) await page.screenshot({ path: join(process.env.GLB_SHOTS, 'glb-light-night.png') })
})

test('fits a phone without scrolling sideways', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 760 })
  await mount(page)
  await expect(page.locator('.glbv-info')).not.toHaveAttribute('open', '')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  if (process.env.GLB_SHOTS) await page.screenshot({ path: join(process.env.GLB_SHOTS, 'glb-phone.png') })
})

test('cleans up on unmount', async ({ page }) => {
  await mount(page)
  await page.evaluate(() => (window as any).__cleanup())
  await expect(page.locator('#root')).toBeEmpty()
})

test('says so when the file is not a GLB', async ({ page }) => {
  await mount(page, { bytes: 'junk', manifest: false })
  await expect(page.locator('.glbv-error')).toContainText('Could not load tower.glb')
})
