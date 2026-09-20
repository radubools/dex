import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { sourceUrl } from '../api'
import type { SourceAnchor } from '../types'

// mermaid + marked are ~600kB; only load them when markdown is opened.
const Markdown = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))
// The two Office converters are ~550kB together and most sources are not
// Office files, so neither is in the main bundle.
const OfficeDoc = lazy(() => import('./OfficeDoc').then((m) => ({ default: m.OfficeDoc })))
const Spreadsheet = lazy(() =>
  import('./OfficeDoc').then((m) => ({ default: m.Spreadsheet })),
)

/**
 * What the browser renders on its own, given the right `content-type` and an
 * `inline` disposition. PDF is the one that matters here: every engine the UI
 * runs in has a viewer for it, so a frame is the whole implementation.
 */
const FRAMED = new Set(['pdf', 'html', 'htm'])
const IMAGE = new Set(['gif', 'png', 'jpg', 'jpeg', 'webp', 'svg', 'avif'])
const VIDEO = new Set(['mp4', 'webm', 'mov'])
const AUDIO = new Set(['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aac'])
const DOC = new Set(['docx'])
const SHEET = new Set(['xlsx', 'xlsm', 'xlsb', 'ods', 'csv', 'tsv'])
/**
 * Read as text rather than guessed at. Anything not listed is offered as a
 * download instead of being decoded — a `.zip` rendered as mojibake is worse
 * than an honest "nothing here can show this".
 */
const TEXT = new Set([
  'txt', 'text', 'log', 'json', 'jsonl', 'yaml', 'yml', 'toml', 'ini', 'xml',
  'srt', 'vtt', 'css', 'js', 'ts', 'py', 'sh', 'sql', 'tex', 'rtf',
])

/**
 * The fragment that lands the viewer on an anchor.
 *
 * `#page=` is the PDF viewer's own parameter rather than dex's, which is why
 * only PDFs get one: on any other type the same fragment would either do
 * nothing or be read as an element id that does not exist. The rest scroll on
 * their own, below.
 */
function fragmentFor(ext: string, anchor?: SourceAnchor): string {
  if (!anchor) return ''
  if (ext === 'pdf' && anchor.page) return `#page=${anchor.page}`
  return ''
}

const extOf = (name: string) => name.split('.').pop()?.toLowerCase() ?? ''

/** A file the operator attached, rendered however its type allows. */
export function SourceView({
  project,
  name,
  anchor,
}: {
  project: string
  name: string
  /** Where in the source to open, when a plan row asked for a spot in it. */
  anchor?: SourceAnchor
}) {
  const ext = extOf(name)
  const url = sourceUrl(project, name) + fragmentFor(ext, anchor)

  if (FRAMED.has(ext)) {
    // The sandbox differs by type, and it has to.
    //
    // An uploaded HTML file is markup dex did not write and may carry scripts
    // from whoever produced it, so it gets `sandbox=""` — no scripts, and no
    // access to this origin's cookies or storage.
    //
    // A PDF must NOT be sandboxed. Chromium renders PDFs with a privileged
    // internal viewer that a sandboxed frame is not allowed to instantiate, so
    // `sandbox` on a `.pdf` yields a blank pane with no error anywhere — which
    // is exactly what the first cut of this did. Nothing is given up by
    // dropping it: a PDF document cannot reach the embedding page's DOM.
    const html = ext !== 'pdf'
    return (
      <iframe
        className="source-frame"
        src={url}
        title={name}
        {...(html ? { sandbox: '' as const } : {})}
      />
    )
  }
  if (IMAGE.has(ext)) return <img className="source-image" src={url} alt={name} />
  if (VIDEO.has(ext)) return <video className="source-media" src={url} controls />
  if (AUDIO.has(ext)) return <audio className="source-media" src={url} controls />
  if (DOC.has(ext)) {
    return (
      <Suspense fallback={<p className="muted">Loading the document reader…</p>}>
        <OfficeDoc url={url} name={name} />
      </Suspense>
    )
  }
  if (SHEET.has(ext)) {
    return (
      <Suspense fallback={<p className="muted">Loading the spreadsheet reader…</p>}>
        <Spreadsheet url={url} name={name} />
      </Suspense>
    )
  }
  if (ext === 'md' || ext === 'markdown' || TEXT.has(ext)) {
    return (
      <TextSource
        url={url}
        name={name}
        markdown={ext === 'md' || ext === 'markdown'}
        line={anchor?.line}
      />
    )
  }
  return <Download url={url} name={name} note="Nothing here can render this format." />
}

/** Fetched rather than framed: a `.txt` in an iframe gets no styling at all. */
function TextSource({
  url,
  name,
  markdown,
  line,
}: {
  url: string
  name: string
  markdown: boolean
  /** Scroll here once the text is in. A plain view has no anchors to jump to. */
  line?: number
}) {
  const [state, setState] = useState<
    { s: 'loading' } | { s: 'ok'; text: string } | { s: 'error'; message: string }
  >({ s: 'loading' })

  useEffect(() => {
    let live = true
    setState({ s: 'loading' })
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`${r.status}`))))
      .then((text) => live && setState({ s: 'ok', text }))
      .catch((err: Error) => live && setState({ s: 'error', message: err.message }))
    return () => {
      live = false
    }
  }, [url])

  if (state.s === 'loading') return <p className="muted">Loading…</p>
  if (state.s === 'error') return <Download url={url} name={name} note={state.message} />
  if (markdown) {
    return (
      <Suspense fallback={<p className="muted">Rendering diagrams…</p>}>
        <Markdown source={state.text} />
      </Suspense>
    )
  }
  return <Lines text={state.text} line={line} />
}

/**
 * Plain text with a line to land on.
 *
 * The line is found by counting rather than by an id per line: a source can be
 * hundreds of thousands of lines, and giving every one of them a DOM node to
 * scroll to costs more than the whole view is worth. One measured line height
 * and an offset does the same job.
 */
function Lines({ text, line }: { text: string; line?: number }) {
  const box = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const el = box.current
    if (!el || !line || line < 2) return
    const height = parseFloat(getComputedStyle(el).lineHeight)
    if (Number.isFinite(height)) el.scrollTop = (line - 1) * height
  }, [text, line])
  return <pre className="code-view" ref={box}><code>{text}</code></pre>
}

/** The fallback that is still useful: the bytes, named, one click away. */
export function Download({ url, name, note }: { url: string; name: string; note?: string }) {
  return (
    <div className="source-download">
      {note && <p className="muted">{note}</p>}
      <a className="pill" href={url} download={name}>
        Download {name}
      </a>
    </div>
  )
}
