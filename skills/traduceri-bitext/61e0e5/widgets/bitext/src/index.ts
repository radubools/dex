/**
 * A translation, read the way it is checked: chunk by numbered chunk, the
 * source paragraph and then the paragraph that replaced it, one under the
 * other.
 *
 * Everything the reader needs is inside the `.bitext.json` itself, so this
 * widget fetches no siblings. That is deliberate: the sidecar is the alignment,
 * and a viewer that had to re-derive it from the original page or PDF would be
 * asserting an alignment nobody wrote down.
 */

export type Ctx = {
  path: string
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  fetchAssetUrl: (path: string) => Promise<string>
}

export type Segment = {
  n: number
  source: string
  target: string
  /** `heading` is set apart; everything else reads as running prose. */
  kind?: string
  /** Where the chunk came from: a page URL, or `p12` for a PDF page. */
  ref?: string
  /** Proofreading: what is wrong with `target`. */
  note?: string
  /** Proofreading: the corrected text `target` should become. */
  suggestion?: string
}

export type Bitext = {
  title?: string
  mode?: string
  source: { lang: string; origin?: string }
  target: { lang: string; file?: string }
  segments: Segment[]
}

/**
 * Parse and check the sidecar far enough to know the viewer has a real one.
 *
 * Throws rather than drawing an empty frame: a reader cannot tell a widget
 * that failed from a document with nothing in it, and the difference matters
 * most on exactly the file that went wrong.
 */
export function parseBitext(text: string): Bitext {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (err) {
    throw new Error(`not a bitext file: ${(err as Error).message}`)
  }
  const doc = raw as Partial<Bitext>
  if (!doc || typeof doc !== 'object' || !Array.isArray(doc.segments)) {
    throw new Error('not a bitext file: no "segments" array')
  }
  if (doc.segments.length === 0) throw new Error('bitext file has no segments')
  for (const [index, seg] of doc.segments.entries()) {
    if (typeof seg?.source !== 'string') {
      throw new Error(`segment ${index + 1} has no "source" string`)
    }
  }
  return {
    ...doc,
    source: doc.source ?? { lang: '?' },
    target: doc.target ?? { lang: '?' },
    segments: doc.segments,
  } as Bitext
}

/**
 * Segment numbers that are missing, repeated, or out of order.
 *
 * Numbering is the whole contract of the format — it is what lets a reader
 * walk back from a translated line to the paragraph it came from — so a break
 * in it is shown rather than quietly renumbered.
 */
export function numberingProblems(segments: Segment[]): string[] {
  const problems: string[] = []
  const seen = new Set<number>()
  let previous = 0
  for (const [index, seg] of segments.entries()) {
    const n = seg.n
    if (typeof n !== 'number' || !Number.isFinite(n)) {
      problems.push(`segment at position ${index + 1} has no number`)
      continue
    }
    if (seen.has(n)) problems.push(`number ${n} appears more than once`)
    else if (n !== previous + 1 && previous !== 0) problems.push(`jumps from ${previous} to ${n}`)
    seen.add(n)
    previous = n
  }
  return problems
}

const CSS = `
.bx { font: 15px/1.55 system-ui, sans-serif; display: flex; flex-direction: column; gap: 14px; }
.bx-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; }
.bx-title { font-size: 19px; font-weight: 650; margin: 0; }
.bx-chip { font-size: 12px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--bx-line); }
.bx-meta { font-size: 12px; opacity: .65; font-family: ui-monospace, monospace; word-break: break-all; }
.bx-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 13px; }
.bx-bar label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.bx-warn { font-size: 13px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--bx-warn-line);
  background: var(--bx-warn-bg); }
.bx-segs { display: flex; flex-direction: column; gap: 4px; }
.bx-seg { display: grid; grid-template-columns: 3.2em 1fr; gap: 0 12px; padding: 10px 0;
  border-top: 1px solid var(--bx-line); }
.bx-seg[hidden] { display: none; }
.bx-n { font: 12px/1.9 ui-monospace, monospace; opacity: .5; text-align: right; }
.bx-ref { font: 11px/1.4 ui-monospace, monospace; opacity: .35; text-align: right; word-break: break-all; }
.bx-body { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.bx-src, .bx-tgt { margin: 0; }
.bx-src { opacity: .62; }
.bx-tgt { }
.bx-seg.bx-heading .bx-src, .bx-seg.bx-heading .bx-tgt { font-size: 17px; font-weight: 650; }
.bx-missing { font-style: italic; color: var(--bx-alarm); opacity: .9; }
.bx-note, .bx-sugg { margin: 2px 0 0; padding: 6px 10px; border-left: 3px solid var(--bx-accent);
  background: var(--bx-accent-bg); font-size: 13.5px; border-radius: 0 6px 6px 0; }
.bx-sugg { border-left-color: var(--bx-ok); background: var(--bx-ok-bg); }
.bx-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .6;
  display: block; margin-bottom: 2px; }
`

const THEMES: Record<string, string> = {
  dark: `--bx-line:#2a2f3a; --bx-accent:#d08b45; --bx-accent-bg:#2a2016; --bx-ok:#4f9d69;
         --bx-ok-bg:#16241b; --bx-alarm:#e0736b; --bx-warn-line:#5a3b1c; --bx-warn-bg:#2a1f12;`,
  light: `--bx-line:#d9dde4; --bx-accent:#a2650f; --bx-accent-bg:#fdf3e3; --bx-ok:#2f7d4f;
          --bx-ok-bg:#eef7f0; --bx-alarm:#b3372c; --bx-warn-line:#e3c99a; --bx-warn-bg:#fdf6e8;`,
}

const el = (tag: string, cls?: string, text?: string) => {
  const node = document.createElement(tag)
  if (cls) node.className = cls
  if (text !== undefined) node.textContent = text
  return node
}

export async function mount(host: HTMLElement, ctx: Ctx): Promise<() => void> {
  const doc = parseBitext(ctx.text)
  const dark = ctx.theme !== 'light'

  const root = el('div', 'bx') as HTMLDivElement
  root.setAttribute('style', THEMES[dark ? 'dark' : 'light'])
  const style = el('style')
  style.textContent = CSS

  const pair = `${doc.source.lang ?? '?'} → ${doc.target.lang ?? '?'}`
  const head = el('div', 'bx-head')
  head.append(
    el('h1', 'bx-title', doc.title ?? ctx.path.split('/').pop() ?? 'bitext'),
    el('span', 'bx-chip', pair),
    el('span', 'bx-chip', doc.mode ?? 'translate'),
  )

  const meta = el('div', 'bx-meta')
  const origin = doc.source.origin ? `from ${doc.source.origin}` : ''
  const written = doc.target.file ? ` · translation written to ${doc.target.file}` : ''
  meta.textContent = `${origin}${written}`

  const flagged = doc.segments.filter((s) => s.note || s.suggestion)
  const missing = doc.segments.filter((s) => !String(s.target ?? '').trim())

  const bar = el('div', 'bx-bar')
  const count = el(
    'span',
    undefined,
    `${doc.segments.length} segments · ${flagged.length} with notes · ${missing.length} untranslated`,
  )
  bar.append(count)

  const toggle = document.createElement('input')
  toggle.type = 'checkbox'
  const toggleLabel = el('label')
  toggleLabel.append(toggle, el('span', undefined, 'only segments needing attention'))
  if (flagged.length || missing.length) bar.append(toggleLabel)

  const segs = el('div', 'bx-segs')
  const rows: { node: HTMLElement; flagged: boolean }[] = []
  for (const seg of doc.segments) {
    const row = el('section', 'bx-seg')
    if (seg.kind === 'heading') row.classList.add('bx-heading')
    row.dataset.n = String(seg.n)

    const gutter = el('div')
    gutter.append(el('div', 'bx-n', String(seg.n)))
    if (seg.ref) gutter.append(el('div', 'bx-ref', seg.ref))

    const body = el('div', 'bx-body')
    body.append(el('p', 'bx-src', seg.source))
    const target = String(seg.target ?? '').trim()
    body.append(
      target ? el('p', 'bx-tgt', target) : el('p', 'bx-tgt bx-missing', '— not translated —'),
    )
    for (const [cls, label, text] of [
      ['bx-note', 'note', seg.note],
      ['bx-sugg', 'suggested', seg.suggestion],
    ] as const) {
      if (!text) continue
      const box = el('div', cls)
      box.append(el('span', 'bx-tag', label), document.createTextNode(text))
      body.append(box)
    }

    row.append(gutter, body)
    segs.append(row)
    rows.push({ node: row, flagged: Boolean(seg.note || seg.suggestion || !target) })
  }

  root.append(head, meta, bar)
  const problems = numberingProblems(doc.segments)
  if (problems.length) {
    root.append(el('div', 'bx-warn', `segment numbering: ${problems.join('; ')}`))
  }
  root.append(segs)

  const onToggle = () => {
    for (const row of rows) row.node.hidden = toggle.checked && !row.flagged
  }
  toggle.addEventListener('change', onToggle)

  host.replaceChildren(style, root)
  return () => {
    toggle.removeEventListener('change', onToggle)
    host.replaceChildren()
  }
}
