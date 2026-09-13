import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

/**
 * Tested against a real pose from the yoga project, not a fixture invented
 * here. A widget that only works on the data its author imagined is the failure
 * this catches.
 *
 * Resolved from this file rather than from the working directory: a task runs
 * the suite from its own package directory, where a path starting `assets/`
 * points at nothing.
 */
const POSE = fileURLToPath(
  new URL('../../../assets/yoga/bird-of-paradise-pose/poses/bird_of_paradise_pose.json', import.meta.url),
)

async function mount(page, text: string) {
  await page.goto('/widgets/harness.html?widget=pose-3d')
  await page.waitForFunction(() => (window as any).__ready)
  return page.evaluate(
    ([path, body]) => (window as any).__mount({ path, text: body }),
    ['yoga/x/poses/p.json', text],
  )
}

test('draws a real pose', async ({ page }) => {
  await mount(page, readFileSync(POSE, 'utf8'))
  // three.js renders into a canvas; its presence is the evidence it got as far
  // as a scene rather than throwing on the way.
  await expect(page.locator('#root canvas')).toBeVisible()
  await expect(page.locator('#error')).toBeHidden()
})

test('names the pose it is showing', async ({ page }) => {
  await mount(page, readFileSync(POSE, 'utf8'))
  await expect(page.locator('#root')).toContainText(/paradise/i)
})

test('says so rather than drawing nothing when there are no landmarks', async ({ page }) => {
  await page.goto('/widgets/harness.html?widget=pose-3d')
  await page.waitForFunction(() => (window as any).__ready)
  const failed = await page
    .evaluate(() => (window as any).__mount({ path: 'p.json', text: '{"landmarks":{}}' }))
    .catch((error) => String(error))
  // A blank frame is the worst outcome: the reader cannot tell a broken widget
  // from an empty pose.
  expect(String(failed)).toMatch(/landmarks/i)
})
