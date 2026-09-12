import { useState } from 'react'

/**
 * A failure, stated in one line with the detail a click away.
 *
 * The detail is what makes a failure actionable — which call, which exception —
 * but it is long and it is noise once you have read it, so it stays folded.
 */
export function ErrorNote({ text, detail }: { text: string; detail?: string }) {
  const [open, setOpen] = useState(false)
  // Nothing to expand when the detail is just the summary again.
  const extra = detail && detail.trim() && detail.trim() !== text.trim() ? detail : null

  return (
    <div className="error-note">
      <div className="error-line">
        <span className="error-text">{text}</span>
        {extra && (
          <button className="error-more" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? 'Less' : 'Details'}
          </button>
        )}
      </div>
      {open && extra && <pre className="error-box">{extra}</pre>}
    </div>
  )
}
