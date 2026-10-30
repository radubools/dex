// Open each player.html given and print, as JSON, the key it settled on and
// the signs it drew. Used by test_player.py.
import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
const results = []
for (const file of process.argv.slice(2)) {
  const errors = []
  page.removeAllListeners('pageerror')
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto('file://' + file)
  await page.waitForFunction(() => window.__player && window.__player.systems.length)
  const drawn = await page.evaluate(() => {
    const notes = window.__player.voices.flatMap(v => v.notes)
    const noteSigns = [...document.querySelectorAll('.note .accidental')]
    // A sign is placed when its ink is measured, sits left of its notehead
    // and spans the note's height.
    const misplaced = noteSigns.filter(sign => {
      const box = sign.getBoundingClientRect()
      const head = sign.parentNode.querySelector('ellipse').getBoundingClientRect()
      const y = head.top + head.height / 2
      return !(box.height > 10 && box.right <= head.left + 0.5 && box.top < y && box.bottom > y)
    }).length
    return {
      keySf: window.__player.keySf,
      inferred: inferKey(notes.map(n => n.pitch)),
      keySigns: [...document.querySelectorAll('.accidental.key')].map(s => s.dataset.accidental),
      noteSigns: noteSigns.map(s => s.dataset.accidental),
      misplaced,
      steps: notes.map(n => [n.pitch, n.step])
    }
  })
  results.push({ ...drawn, errors })
}
await browser.close()
console.log(JSON.stringify(results))
