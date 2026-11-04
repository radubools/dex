/**
 * Word files, rendered in the browser.
 *
 * No engine renders these natively, so each is converted to HTML here rather
 * than framed. The library is heavy and not needed to open a PDF, which is the
 * common case — `SourceView` loads this module lazily, so it arrives only when
 * a Word file is actually opened.
 *
 * The 2007+ XML format only. A pre-2007 binary `.doc` is a different container
 * that it does not read, and falls through to a download, which is honest
 * about it.
 */
import { useEffect, useState } from 'react'
import { Download } from './SourceView'

type State<T> =
  | { s: 'loading' }
  | { s: 'ok'; value: T }
  | { s: 'error'; message: string }

/** Fetch the bytes once, then hand them to whichever converter asked. */
function useBytes(url: string): State<ArrayBuffer> {
  const [state, setState] = useState<State<ArrayBuffer>>({ s: 'loading' })
  useEffect(() => {
    let live = true
    setState({ s: 'loading' })
    fetch(url)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`))))
      .then((value) => live && setState({ s: 'ok', value }))
      .catch((err: Error) => live && setState({ s: 'error', message: err.message }))
    return () => {
      live = false
    }
  }, [url])
  return state
}

/** A `.docx`, converted to semantic HTML. */
export function OfficeDoc({ url, name }: { url: string; name: string }) {
  const bytes = useBytes(url)
  const [html, setHtml] = useState<State<string>>({ s: 'loading' })

  useEffect(() => {
    if (bytes.s !== 'ok') return
    let live = true
    void (async () => {
      try {
        // Plain `mammoth`: its package `browser` field already swaps the two
        // node-only files for browser equivalents, which the bundler honours.
        const mammoth = await import('mammoth')
        const result = await mammoth.default.convertToHtml({ arrayBuffer: bytes.value })
        if (live) setHtml({ s: 'ok', value: result.value })
      } catch (err) {
        if (live) setHtml({ s: 'error', message: (err as Error).message })
      }
    })()
    return () => {
      live = false
    }
  }, [bytes])

  if (bytes.s === 'error') return <Download url={url} name={name} note={bytes.message} />
  if (bytes.s === 'loading' || html.s === 'loading') return <p className="muted">Converting…</p>
  if (html.s === 'error') {
    return <Download url={url} name={name} note={`Could not read this document: ${html.message}`} />
  }
  // mammoth emits a fixed, small set of tags from the document's own styles —
  // no scripts and no attributes it did not choose. The alternative, an
  // iframe, would cost a second document just to style it.
  return <div className="source-doc" dangerouslySetInnerHTML={{ __html: html.value }} />
}
