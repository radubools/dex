import { useEffect, useState } from 'react'
import { readTable, type TablePage } from '../api'
import { CellModal, isCut, type Cell } from './CellModal'
import { Download } from './SourceView'

/** The file types this shows, here and in `dex/tables.py`. */
export const TABLE = new Set(['csv', 'tsv', 'jsonl', 'ndjson', 'xls', 'xlsx', 'xlsm', 'xlsb', 'ods'])

const PAGE_SIZES = [100, 200, 500]

const clipped = (td: HTMLElement, text: string) => td.scrollWidth > td.clientWidth || isCut(text)

/**
 * Whether a `.json` is a list of objects, which shows as a table; anything else
 * is shown as code. Only the server can say, having parsed it. null until it has.
 */
export function useTabular(url: string | null): boolean | null {
  const [tabular, setTabular] = useState<boolean | null>(null)
  useEffect(() => {
    if (!url) return
    let live = true
    setTabular(null)
    readTable(url, { offset: 0, limit: 1 })
      .then(() => live && setTabular(true))
      .catch(() => live && setTabular(false))
    return () => { live = false }
  }, [url])
  return tabular
}

/**
 * A data file a page at a time, with a tab per sheet for a spreadsheet.
 *
 * The server cuts the pages (`dex/tables.py`): these files can be far larger
 * than a browser should be handed. The page on screen stays until the next one
 * arrives, so moving through a file does not flash.
 */
export function TableView({
  url,
  raw,
  name,
  sheet: firstSheet,
  version = 0,
}: {
  /** The table route for this file, query string included. */
  url: string
  /** The file itself, for when it cannot be read. */
  raw: string
  name: string
  sheet?: string
  /** Bumped when the file changes. */
  version?: number
}) {
  const [limit, setLimit] = useState(PAGE_SIZES[0])
  const [offset, setOffset] = useState(0)
  const [sheet, setSheet] = useState<string | undefined>(firstSheet)
  const [page, setPage] = useState<TablePage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [opened, setOpened] = useState<Cell | null>(null)

  useEffect(() => {
    let live = true
    readTable(url, { offset, limit, sheet })
      .then((got) => {
        if (!live) return
        setPage(got)
        setError(null)
        // Rows went away under the reader: the last page there is.
        if (got.total > 0 && offset >= got.total) setOffset(Math.floor((got.total - 1) / limit) * limit)
      })
      .catch((err: Error) => live && setError(err.message))
    return () => { live = false }
  }, [url, offset, limit, sheet, version])

  if (error) return <Download url={raw} name={name} note={`Could not read this file: ${error}`} />
  if (!page) return <p className="muted">Loading…</p>

  const pages = Math.max(1, Math.ceil(page.total / limit))
  const at = Math.floor(offset / limit)
  const go = (to: number) => setOffset(Math.min(Math.max(to, 0), pages - 1) * limit)
  const sheets = page.sheets ?? []

  return (
    <div className="source-sheet table-view">
      {/* One sheet needs no tab strip; a workbook of twelve does. */}
      {sheets.length > 1 && (
        <div className="sheet-tabs" role="tablist">
          {sheets.map((s) => (
            <button
              key={s}
              type="button"
              role="tab"
              aria-selected={s === page.sheet}
              className={`sheet-tab ${s === page.sheet ? 'on' : ''}`}
              onClick={() => {
                setSheet(s)
                setOffset(0)
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <div className="sheet-body">
        <table>
          <thead>
            <tr>
              <th className="table-row-number">#</th>
              {page.columns.map((c, i) => <th key={i} title={c}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row, r) => (
              <tr key={page.offset + r}>
                <td className="table-row-number">{page.offset + r + 1}</td>
                {row.map((cell, c) => (
                  <td
                    key={c}
                    title={cell}
                    // Only the width on screen says whether it fits, so it is asked here.
                    onPointerEnter={(e) => e.currentTarget.classList.toggle('clipped', clipped(e.currentTarget, cell))}
                    onClick={(e) => {
                      if (!clipped(e.currentTarget, cell)) return
                      setOpened({
                        text: cell,
                        column: page.columns[c],
                        at: { url, row: page.offset + r, index: c, sheet: page.sheet },
                      })
                    }}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {page.total === 0 && <p className="muted small">No rows.</p>}
      </div>
      {opened && <CellModal cell={opened} onClose={() => setOpened(null)} />}
      <div className="table-pager">
        <button type="button" className="ghost-btn small" aria-label="First page" disabled={at === 0} onClick={() => go(0)}>«</button>
        <button type="button" className="ghost-btn small" aria-label="Previous page" disabled={at === 0} onClick={() => go(at - 1)}>‹</button>
        <span className="small">Page {at + 1} of {pages.toLocaleString()}</span>
        <button type="button" className="ghost-btn small" aria-label="Next page" disabled={at >= pages - 1} onClick={() => go(at + 1)}>›</button>
        <button type="button" className="ghost-btn small" aria-label="Last page" disabled={at >= pages - 1} onClick={() => go(pages - 1)}>»</button>
        <select
          aria-label="Rows per page"
          value={limit}
          onChange={(e) => {
            const size = Number(e.target.value)
            // The row at the top stays on the page it lands on.
            setOffset(Math.floor(offset / size) * size)
            setLimit(size)
          }}
        >
          {PAGE_SIZES.map((size) => <option key={size} value={size}>{size} per page</option>)}
        </select>
        <span className="muted small table-count">{page.total.toLocaleString()} rows</span>
      </div>
    </div>
  )
}
