import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

/**
 * Inline markdown for chat and activity text.
 *
 * Deliberately not the full `Markdown` component: this renders constantly (on
 * every streamed token) and must not pull in mermaid, so fenced blocks are
 * shown as plain code.
 */
export function Prose({ text, inline }: { text: string; inline?: boolean }) {
  const html = useMemo(
    () =>
      DOMPurify.sanitize(
        // `inline` skips the block pass, so there is no <p> to fight with
        // inside a button or a one-line label.
        inline
          ? (marked.parseInline(text, { async: false }) as string)
          : (marked.parse(text, { async: false, breaks: true }) as string),
      ),
    [text, inline],
  )
  if (inline) return <span className="prose-md inline" dangerouslySetInnerHTML={{ __html: html }} />
  return <div className="prose-md" dangerouslySetInnerHTML={{ __html: html }} />
}
