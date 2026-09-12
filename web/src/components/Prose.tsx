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
export function Prose({ text }: { text: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(text, { async: false, breaks: true }) as string),
    [text],
  )
  return <div className="prose-md" dangerouslySetInnerHTML={{ __html: html }} />
}
