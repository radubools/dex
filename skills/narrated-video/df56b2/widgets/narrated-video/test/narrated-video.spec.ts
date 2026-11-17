import { expect, test } from '@playwright/test'

const VTT = `WEBVTT

00:00:00.000 --> 00:00:03.000
Lay out the array.

00:00:03.000 --> 00:00:06.500
Compare the pair.
`

async function mount(page, assets: Record<string, string>) {
  await page.goto('/widgets/harness.html?widget=narrated-video')
  await page.waitForFunction(() => (window as any).__ready)
  return page.evaluate(
    ([path, files]) => (window as any).__mount({ path, text: '', assets: files }),
    ['algorithms/x/narrated_demo.mp4', assets],
  )
}

test('reads the captions out beneath the picture', async ({ page }) => {
  await mount(page, { 'narrated_demo.vtt': VTT, 'narrated_demo.mp4': 'fake-bytes' })
  await expect(page.locator('#root video')).toBeAttached()
  // One button per cue, so a reader can replay a section.
  await expect(page.locator('#root button')).toHaveCount(2)
  await expect(page.locator('#root')).toContainText('2 sections')
})

test('plays fine when there are no captions at all', async ({ page }) => {
  // Plenty of animations are not narrated; a missing .vtt is normal, not an
  // error, and must not stop the video appearing.
  await mount(page, { 'narrated_demo.mp4': 'fake-bytes' })
  await expect(page.locator('#root video')).toBeAttached()
  await expect(page.locator('#root button')).toHaveCount(0)
  await expect(page.locator('#error')).toBeHidden()
})
