import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import mermaid from 'mermaid'
import DOMPurify from 'dompurify'

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'strict',
  themeVariables: { fontFamily: 'ui-sans-serif, system-ui, sans-serif', fontSize: '13px' },
})

/** Markdown with ```mermaid fences rendered as diagrams. */
export function Markdown({ source }: { source: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const diagrams = useRef<string[]>([])
  /** Diagram index -> the source last rendered into it. */
  const done = useRef<Map<number, string>>(new Map())
  const [html, setHtml] = useState('')

  // 1. Markdown -> sanitized HTML, with mermaid fences replaced by empty slots.
  useEffect(() => {
    const blocks: string[] = []
    const renderer = new marked.Renderer()
    renderer.code = ({ text, lang }) => {
      if (lang === 'mermaid') {
        blocks.push(text)
        return `<div class="mermaid-slot" data-index="${blocks.length - 1}"></div>`
      }
      return `<pre class="md-code"><code>${escapeHtml(text)}</code></pre>`
    }
    const parsed = marked.parse(source, { renderer, async: false }) as string
    diagrams.current = blocks
    done.current.clear()
    setHtml(DOMPurify.sanitize(parsed, { ADD_ATTR: ['data-index'] }))
  }, [source])

  // 2. Once that HTML is mounted, render each diagram into its slot.
  useEffect(() => {
    const rendered = done.current
    const slots = ref.current?.querySelectorAll<HTMLElement>('.mermaid-slot') ?? []
    ;(async () => {
      for (const slot of slots) {
        const index = Number(slot.dataset.index)
        const src = diagrams.current[index]
        if (!src) continue
        // Keyed by source, not by a "done" flag: under StrictMode the effect
        // runs twice, and a flag set before the async render completes made the
        // second pass skip a slot the first pass had abandoned — a blank box.
        if (rendered.get(index) === src && slot.childElementCount > 0) continue
        try {
          const id = `mmd-${index}-${Math.random().toString(36).slice(2)}`
          const { svg } = await mermaid.render(id, src)
          slot.innerHTML = svg
          const el = slot.querySelector('svg')
          if (el) {
            // Mermaid emits height="100%", which collapses to nothing inside a
            // flex/grid parent. Size from the viewBox instead.
            el.removeAttribute('height')
            el.style.height = 'auto'
            el.style.maxWidth = '100%'
            el.style.display = 'block'
          }
          rendered.set(index, src)
        } catch (err) {
          slot.textContent = `Diagram failed to render: ${(err as Error).message}`
          slot.classList.add('mermaid-error')
          rendered.set(index, src)
        }
      }
    })()
  }, [html])

  return <div className="markdown" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
}

function escapeHtml(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!)
}
