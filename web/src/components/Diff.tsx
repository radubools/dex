import { useMemo, useState } from 'react'

type Line = { kind: 'add' | 'del' | 'ctx' | 'hunk'; text: string }

/**
 * Unified-diff renderer. Kept deliberately plain: on a phone the useful signal
 * is which lines changed, so hunks are collapsed to their content and long
 * lines wrap rather than scroll off.
 */
export function Diff({ patch, collapsed = false }: { patch: string; collapsed?: boolean }) {
  const [expanded, setExpanded] = useState(!collapsed)

  const lines = useMemo<Line[]>(
    () =>
      patch
        .split('\n')
        .filter((l) => !l.startsWith('---') && !l.startsWith('+++') && !l.startsWith('Index:') && !l.startsWith('==='))
        .map((text) => {
          if (text.startsWith('@@')) return { kind: 'hunk' as const, text }
          if (text.startsWith('+')) return { kind: 'add' as const, text: text.slice(1) }
          if (text.startsWith('-')) return { kind: 'del' as const, text: text.slice(1) }
          return { kind: 'ctx' as const, text: text.replace(/^ /, '') }
        })
        .filter((l, i, arr) => !(l.text === '' && i === arr.length - 1)),
    [patch],
  )

  const shown = expanded ? lines : lines.filter((l) => l.kind !== 'ctx').slice(0, 8)

  return (
    <div className="diff">
      {shown.map((line, i) => (
        <div key={i} className={`diff-line diff-${line.kind}`}>
          <span className="diff-gutter">{line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}</span>
          <code>{line.text || ' '}</code>
        </div>
      ))}
      {!expanded && lines.length > shown.length && (
        <button className="diff-more" onClick={() => setExpanded(true)}>
          Show all {lines.length} lines
        </button>
      )}
    </div>
  )
}
