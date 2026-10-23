import { networkInterfaces } from 'node:os'
import { expect, test, type Page } from '@playwright/test'

/**
 * Can the widget reach `/api/sql` from the frame dex actually puts it in, served
 * from the address the operator actually opens?
 *
 * `widgets/harness.html` mounts the module straight into the page with no
 * sandbox — deliberately, and it says so: the sandbox is dex's concern, and
 * fighting an opaque origin makes a drawing test flaky. The cost was that the
 * two things this widget depends on which *only* exist inside the sandbox — a
 * cross-origin `fetch` from a null-origin document, and finding out which origin
 * framed it — were covered by nothing. Every test passed and the first real
 * click returned "cannot reach http://127.0.0.1:4317/api/sql (Load failed)".
 *
 * So this file reproduces `WidgetHost.tsx` rather than using the harness: the
 * same `sandbox="allow-scripts"` with no `allow-same-origin`, the same `srcdoc`,
 * the same `postMessage`. And it runs against dex's **dev server on 4318**, by
 * its LAN address as well as by loopback, because a hard-coded `127.0.0.1` is
 * right from one of those and wrong from the other.
 */

const API_PORT = 4317
const WEB_PORT = 4318

/** This machine's first non-loopback IPv4 address, or null if it has none. */
function lanAddress(): string | null {
  for (const entries of Object.values(networkInterfaces())) {
    for (const entry of entries ?? []) {
      if (entry.family === 'IPv4' && !entry.internal) return entry.address
    }
  }
  return null
}

/** A frame built the way `WidgetHost.tsx` builds it, running `body` and reporting. */
function framed(body: string): string {
  return `<!doctype html>
<html><body><div id="root"></div><script type="module">
const send = (m) => parent.postMessage(m, '*')
try { send(await (async () => { ${body} })()) }
catch (err) { send({ error: String(err && err.message || err) }) }
</script></body></html>`
}

/** Run `body` inside a real sandboxed srcdoc frame on `page` and return what it posts. */
async function inSandbox(page: Page, body: string): Promise<Record<string, unknown>> {
  return page.evaluate(
    ([srcdoc]) =>
      new Promise<Record<string, unknown>>((resolve) => {
        const frame = document.createElement('iframe')
        // Exactly WidgetHost's attribute: allow-scripts, no allow-same-origin.
        frame.setAttribute('sandbox', 'allow-scripts')
        frame.srcdoc = srcdoc
        const onMessage = (event: MessageEvent) => {
          if (event.source !== frame.contentWindow) return
          window.removeEventListener('message', onMessage)
          resolve(event.data)
        }
        window.addEventListener('message', onMessage)
        document.body.append(frame)
        setTimeout(() => resolve({ error: 'timed out' }), 20000)
      }),
    [framed(body)],
  )
}

/** The origins dex is reachable on: loopback always, the LAN address if there is one. */
function origins(): string[] {
  const lan = lanAddress()
  return [`http://127.0.0.1:${WEB_PORT}`, ...(lan ? [`http://${lan}:${WEB_PORT}`] : [])]
}

test('a sandboxed frame can read the origin that framed it', async ({ page }) => {
  for (const origin of origins()) {
    await page.goto(`${origin}/`)
    const seen = await inSandbox(
      page,
      `return {
         ancestor: (() => { try { return Array.from(location.ancestorOrigins || [])[0] ?? '' }
                            catch { return '' } })(),
         referrer: document.referrer,
         ownOrigin: String(origin),
       }`,
    )
    // The frame's own origin is opaque -- this is why a relative URL cannot work.
    expect(seen.ownOrigin).toBe('null')
    // ...but the parent's origin is legible, by one route or the other, and that
    // is the whole basis of `parentOrigin()`.
    const derived = seen.ancestor || new URL(String(seen.referrer)).origin
    expect(derived, `from ${origin}`).toBe(origin)
  }
})

test('a relative /api/sql resolves against the parent, but is not what we rely on', async ({
  page,
}) => {
  // Measured, against an earlier guess of mine that was simply wrong: a `srcdoc`
  // frame *inherits the parent's base URI*, so a relative fetch does resolve and
  // does reach the dev server. (The first version of this test appeared to prove
  // the opposite -- it was pointed at the static harness on 4319, which has no
  // `/api/sql` and answered 404. A 404 is not a resolution failure.)
  await page.goto(`http://127.0.0.1:${WEB_PORT}/`)
  const result = await inSandbox(
    page,
    `try {
       const r = await fetch('/api/sql?sql=SELECT%201%20AS%20n')
       return { ok: r.ok, status: r.status, base: document.baseURI }
     } catch (e) { return { ok: false, error: String(e.message) } }`,
  )
  expect(result.ok, JSON.stringify(result)).toBe(true)
  expect(String(result.base)).toContain(`:${WEB_PORT}`)

  // The widget still builds an absolute URL from `parentOrigin()` instead. Base
  // URI inheritance through `srcdoc` is exactly the sort of detail that differs
  // between engines, and when it differs the symptom is a bare "Load failed"
  // with no status — the failure that started all this. An explicit origin costs
  // one line and cannot be silently reinterpreted.
})

test('the derived origin answers /api/sql from every address dex is open on', async ({ page }) => {
  for (const origin of origins()) {
    await page.goto(`${origin}/`)
    const result = await inSandbox(
      page,
      `const base = Array.from(location.ancestorOrigins || [])[0] || new URL(document.referrer).origin
       const r = await fetch(base + '/api/sql?sql=SELECT%201%20AS%20n')
       return { base, ok: r.ok, status: r.status, body: (await r.text()).slice(0, 60) }`,
    )
    // The dev server proxies /api to the API port, so the page's own origin
    // answers -- which is the address a browser is least likely to refuse.
    expect(result.ok, `from ${origin}: ${JSON.stringify(result)}`).toBe(true)
    expect(result.base).toBe(origin)
    expect(String(result.body)).toContain('"n":1')
  }
})

test('the real widget draws inside the sandbox, from the LAN address', async ({ page }) => {
  const lan = lanAddress()
  test.skip(!lan, 'no non-loopback address on this machine')
  const origin = `http://${lan}:${WEB_PORT}`

  // The bundle is fetched by the host (the frame cannot fetch it -- opaque
  // origin), inlined as base64, and imported from a data: URL. That is what
  // WidgetHost does, and it is the only way the module runs in there at all.
  await page.goto(`http://127.0.0.1:4319/widgets/harness.html?widget=none`)
  const index = await page.evaluate(() => fetch('/widgets/index.json').then((r) => r.json()))
  const code = await page.evaluate(
    (src: string) => fetch(`/${src.replace(/^\/+/, '')}`).then((r) => r.text()),
    index['experiments-criteria'],
  )
  const pkg = 'change-order-risk-criteria-doc-5'
  const store = await page.evaluate(
    (p: string) => fetch(`/assets/construction/${p}/chunks.json`).then((r) => r.text()),
    pkg,
  )

  // Now do it from the LAN origin, so `parentOrigin()` has to earn its keep.
  await page.goto(`${origin}/`)
  const result = await page.evaluate(
    async ([bundle, text, path]) =>
      new Promise<Record<string, unknown>>((resolve) => {
        const encoded = btoa(
          Array.from(new TextEncoder().encode(bundle), (b) => String.fromCharCode(b)).join(''),
        )
        const ctx = JSON.stringify({ path, text, theme: 'dark' })
        const srcdoc = `<!doctype html><html><body><div id="root"></div>
<script type="application/json" id="ctx">${ctx.replace(/</g, '\\u003c')}</script>
<script type="module">
const send = (m) => parent.postMessage(m, '*')
try {
  const mod = await import('data:text/javascript;base64,' + "${encoded}")
  const c = JSON.parse(document.getElementById('ctx').textContent)
  await mod.mount(document.getElementById('root'), {
    ...c,
    fetchAsset: async () => { throw new Error('no siblings offered') },
    fetchAssetUrl: async () => { throw new Error('no siblings offered') },
  })
  send({
    ok: true,
    reached: mod.reached,
    task: document.querySelector('.cd-task')?.textContent ?? '',
    criteria: document.querySelectorAll('.cd-select[data-role=criterion] option').length,
    chunks: document.querySelectorAll('.cd-chunk').length,
  })
} catch (err) { send({ ok: false, error: String(err && err.message || err) }) }
<\/script></body></html>`
        const frame = document.createElement('iframe')
        frame.setAttribute('sandbox', 'allow-scripts')
        frame.srcdoc = srcdoc
        const onMessage = (event: MessageEvent) => {
          if (event.source !== frame.contentWindow) return
          window.removeEventListener('message', onMessage)
          resolve(event.data)
        }
        window.addEventListener('message', onMessage)
        document.body.append(frame)
        setTimeout(() => resolve({ ok: false, error: 'timed out' }), 20000)
      }),
    [code, store, `construction/${pkg}/chunks.json`],
  )

  expect(result.ok, JSON.stringify(result)).toBe(true)
  // The base it used is the LAN origin, not the hard-coded loopback port.
  expect(result.reached).toBe(origin)
  expect(String(result.task)).toContain(pkg)
  expect(Number(result.criteria)).toBeGreaterThan(0)
  expect(Number(result.chunks)).toBeGreaterThan(0)
})
