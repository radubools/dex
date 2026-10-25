import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { getTaskGraph } from '../api'
import type { TaskGraph as Graph, TaskGraphNode } from '../types'

/**
 * The live task graph of a project (design/18_task_dependencies.md): every
 * task that is queued, running, waiting or blocked, the finished ones joined
 * to them, and an arrow from each task to the ones it waits on. Tasks with no
 * dependencies stand alone. Redrawn every few seconds; a click opens a task.
 */

// The same look as the diagrams in Markdown; initialising twice is harmless.
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'strict',
  themeVariables: { fontFamily: 'ui-sans-serif, system-ui, sans-serif', fontSize: '13px' },
})

const REFRESH_MS = 4000

const STATE_LABEL: Record<string, string> = {
  queued: 'queued', running: 'running', awaiting_input: 'waiting on you',
  paused: 'paused', blocked: 'waiting on tasks', succeeded: 'done',
  failed: 'failed', cancelled: 'cancelled',
}

/** Text safe inside a quoted Mermaid label. */
const esc = (s: string) => s.replace(/["#<>]/g, (c) => `#${c.charCodeAt(0)};`).replace(/\s+/g, ' ')

function chart(g: Graph): string {
  const lines = ['flowchart TD']
  g.nodes.forEach((n, i) => {
    const title = esc(n.title.length > 42 ? `${n.title.slice(0, 40)}…` : n.title)
    const meta = esc(`${STATE_LABEL[n.state] ?? n.state}${n.attempt > 1 ? ` · attempt ${n.attempt}` : ''}`)
    const thread = esc(n.threadTitle ?? 'no thread')
    lines.push(`  n${i}["<b>${title}</b><br/><small>${meta}<br/>${thread}</small>"]:::${n.state}`)
  })
  const index = new Map(g.nodes.map((n, i) => [n.id, i]))
  for (const e of g.edges) {
    const a = index.get(e.from), b = index.get(e.to)
    if (a === undefined || b === undefined) continue
    lines.push(`  n${a} -->|${e.kind === 'continued' ? 'asked for a change' : 'waits on'}| n${b}`)
  }
  lines.push(
    '  classDef running fill:#1e3a5f,stroke:#7dd3fc,color:#e5f2ff',
    '  classDef queued fill:#26262c,stroke:#9ca3af,color:#e5e7eb',
    '  classDef paused fill:#26262c,stroke:#7dd3fc,color:#e5e7eb,stroke-dasharray:4 3',
    '  classDef blocked fill:#3a2412,stroke:#fb923c,color:#fff1e6',
    '  classDef awaiting_input fill:#3b2f12,stroke:#f59e0b,color:#fff7e0',
    '  classDef succeeded fill:#14301f,stroke:#4ade80,color:#e7fbe9',
    '  classDef failed fill:#3a1717,stroke:#f87171,color:#ffe8e8',
    '  classDef cancelled fill:#2a1f1f,stroke:#f87171,color:#ffe8e8,stroke-dasharray:4 3',
  )
  return lines.join('\n')
}

export function TaskGraph({
  project,
  onOpenTask,
  onClose,
}: {
  project: string
  onOpenTask: (node: TaskGraphNode) => void
  onClose: () => void
}) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const host = useRef<HTMLDivElement>(null)
  const drawn = useRef('')

  useEffect(() => {
    let live = true
    const load = () =>
      getTaskGraph(project)
        .then((g) => live && (setGraph(g), setError(null)))
        .catch((e: Error) => live && setError(e.message))
    void load()
    const timer = setInterval(load, REFRESH_MS)
    return () => { live = false; clearInterval(timer) }
  }, [project])

  useEffect(() => {
    const el = host.current
    if (!el || !graph || !graph.nodes.length) return
    const src = chart(graph)
    if (src === drawn.current) return // unchanged: keep the drawing (and any scroll)
    drawn.current = src
    let live = true
    mermaid
      .render(`task-graph-${Date.now()}`, src)
      .then(({ svg }) => {
        if (!live || !host.current) return
        host.current.innerHTML = svg
        // Clicks by node, without Mermaid's own click hooks (they need a loose
        // security level): its node groups carry the id we gave them.
        graph.nodes.forEach((n, i) => {
          const g = host.current?.querySelector<SVGGElement>(`g.node[id*="-n${i}-"]`)
          if (!g) return
          g.style.cursor = 'pointer'
          g.addEventListener('click', () => onOpenTask(n))
        })
      })
      .catch((e: Error) => live && setError(`could not draw the graph: ${e.message}`))
    return () => { live = false }
  }, [graph, onOpenTask])

  const edges = graph?.edges.length ?? 0
  return (
    <section className="viewer task-graph" role="dialog" aria-label="Task graph">
      <header className="viewer-bar">
        <span className="viewer-path">
          Task graph
          {graph && (
            <span className="muted">
              {' '}· {graph.nodes.length} task{graph.nodes.length === 1 ? '' : 's'}, {edges} dependenc{edges === 1 ? 'y' : 'ies'}
            </span>
          )}
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>
      <div className="viewer-body">
        {error && <p className="error-note">{error}</p>}
        {graph && !graph.nodes.length && (
          <p className="muted">Nothing is running or waiting in this project.</p>
        )}
        {!graph && !error && <p className="muted">Loading…</p>}
        <div ref={host} className="task-graph-canvas" hidden={!graph?.nodes.length} />
        <p className="muted small">
          An arrow runs from a task to the task it waits on. A task waiting on others is
          blocked: it holds no slot, and resumes first once they are all done.
        </p>
      </div>
    </section>
  )
}
