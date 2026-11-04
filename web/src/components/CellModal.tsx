import { Suspense, lazy, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { readTable } from '../api'

const Markdown = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))

/** A cell at this many characters was cut by the server (`MAX_CELL`, plus its `…`). */
const CUT_AT = 5001

export const isCut = (text: string) => text.length === CUT_AT && text.endsWith('…')

/** JSON pretty-printed, when the text is an object or a list; null when it isn't. */
export function prettyJson(text: string): string | null {
  if (!/^\s*[[{]/.test(text)) return null
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return null
  }
}

/** A heading, list, quote, fence, table, link or bold: plain prose stays plain. */
const MARKDOWN = /^#{1,6} \S|^\s*(?:[-*+]|\d+\.) \S|^> |^```|^\|.*\|\s*$|\[[^\]]+\]\([^)]+\)|\*\*[^*\n]+\*\*/m

export const isMarkdown = (text: string) => MARKDOWN.test(text)

export type Cell = {
  text: string
  column: string
  /** Where to read it whole if it was cut. */
  at: { url: string; row: number; index: number; sheet?: string | null }
}

/** One cell's whole contents, formatted as what it is. */
export function CellModal({ cell, onClose }: { cell: Cell; onClose: () => void }) {
  const [text, setText] = useState(isCut(cell.text) ? null : cell.text)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (text !== null) return
    let live = true
    const { url, row, index, sheet } = cell.at
    readTable(url, { offset: row, limit: 1, sheet, full: true })
      .then((got) => {
        if (!live) return
        // JSON Lines' columns are each page's own, so this page's may differ.
        const at = got.columns[index] === cell.column ? index : got.columns.indexOf(cell.column)
        setText(got.rows[0]?.[at] ?? cell.text)
      })
      .catch((err: Error) => {
        if (!live) return
        setError(err.message)
        setText(cell.text)
      })
    return () => { live = false }
  }, [cell, text])

  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [onClose])

  const json = text === null ? null : prettyJson(text)
  return createPortal(
    <div className="cell-modal-scrim" onClick={onClose}>
      <div
        className="cell-modal"
        role="dialog"
        aria-modal="true"
        aria-label={cell.column || 'Cell'}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cell-modal-head">
          <strong>{cell.column || 'Cell'}</strong>
          <button type="button" className="ghost-btn small" onClick={onClose} autoFocus>Close</button>
        </div>
        <div className="cell-modal-body">
          {error && <p className="error small">Showing the first part only: {error}</p>}
          {text === null ? (
            <p className="muted">Loading…</p>
          ) : json !== null ? (
            <pre className="code-view"><code>{json}</code></pre>
          ) : isMarkdown(text) ? (
            <Suspense fallback={<p className="muted">Formatting…</p>}>
              <Markdown source={text} />
            </Suspense>
          ) : (
            <p className="cell-modal-text">{text}</p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
