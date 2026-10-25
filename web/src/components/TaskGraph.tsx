import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { getTaskGraph } from '../api'
import type { TaskGraph as Graph, TaskGraphNode } from '../types'

/**
 * The live task graph of a project (design/18_task_dependencies.md): every
 * task that is queued, running, waiting or blocked, the finished ones joined
 * to them, and an arrow from each task to the ones it waits on. Each group of
 * connected tasks is its own diagram, drawn bottom up (the task that asked at
 * the bottom, what it asked for above it); the groups stack newest first, and
 * tasks with no dependencies share one column at the end, newest at the top.
 * Redrawn every few seconds; a click opens a task.
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

/** When a task last moved: its latest attempt's start or end. */
const stamp = (n: TaskGraphNode) => Math.max(n.createdAt ?? 0, n.finishedAt ?? 0)

/** A diagram's tasks, as indexes into the graph's nodes. */
type Group = { nodes: number[]; edges: Graph['edges']; alone: boolean }

/**
 * The graph split into its connected groups, newest first (by when the task
 * that asked, the one at the bottom, last started: steady while what it
 * waits on moves), then the tasks with no dependencies as one group, newest first.
 */
function groups(g: Graph): Group[] {
  const index = new Map(g.nodes.map((n, i) => [n.id, i]))
  const parent = g.nodes.map((_, i) => i)
  const root = (i: number): number => (parent[i] === i ? i : (parent[i] = root(parent[i])))
  const edges = g.edges.filter((e) => index.has(e.from) && index.has(e.to))
  for (const e of edges) parent[root(index.get(e.from)!)] = root(index.get(e.to)!)
  const linked = new Set(edges.flatMap((e) => [index.get(e.from)!, index.get(e.to)!]))
  const byRoot = new Map<number, number[]>()
  g.nodes.forEach((_, i) => { if (linked.has(i)) byRoot.set(root(i), [...(byRoot.get(root(i)) ?? []), i]) })
  const waiters = new Set(edges.map((e) => index.get(e.from)!))
  const newest = (ids: number[]) => Math.max(...ids.filter((i) => waiters.has(i)).map((i) => g.nodes[i].createdAt ?? 0))
  const out: Group[] = [...byRoot.values()]
    .sort((a, b) => newest(b) - newest(a))
    .map((ids) => ({ nodes: ids, edges: edges.filter((e) => ids.includes(index.get(e.from)!)), alone: false }))
  const alone = g.nodes.map((_, i) => i).filter((i) => !linked.has(i)).sort((a, b) => stamp(g.nodes[b]) - stamp(g.nodes[a]))
  if (alone.length) out.push({ nodes: alone, edges: [], alone: true })
  return out
}

function chart(g: Graph, group: Group): string {
  // Bottom up: an edge runs from the task that waits (below) to the one it waits on (above).
  // Tasks on their own stack top down instead, linked invisibly so they form a column.
  const lines = [group.alone ? 'flowchart TD' : 'flowchart BT']
  for (const i of group.nodes) {
    const n = g.nodes[i]
    const title = esc(n.title.length > 42 ? `${n.title.slice(0, 40)}…` : n.title)
    const meta = esc(`${STATE_LABEL[n.state] ?? n.state}${n.attempt > 1 ? ` · attempt ${n.attempt}` : ''}`)
    const thread = esc(n.threadTitle ?? 'no thread')
    lines.push(`  n${i}["<b>${title}</b><br/><small>${meta}<br/>${thread}</small>"]:::${n.state}`)
  }
  const index = new Map(g.nodes.map((n, i) => [n.id, i]))
  // Newest first, so Mermaid lays the latest asks out first (on the left of their row).
  const edges = [...group.edges].sort((a, b) => stamp(g.nodes[index.get(b.to)!]) - stamp(g.nodes[index.get(a.to)!]))
  for (const e of edges) {
    lines.push(`  n${index.get(e.from)} -->|${e.kind === 'continued' ? 'asked for a change' : 'waits on'}| n${index.get(e.to)}`)
  }
  if (group.alone) for (let k = 1; k < group.nodes.length; k++) lines.push(`  n${group.nodes[k - 1]} ~~~ n${group.nodes[k]}`)
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
    const parts = groups(graph)
    const srcs = parts.map((p) => chart(graph, p))
    const all = srcs.join('\n%%\n')
    if (all === drawn.current) return // unchanged: keep the drawing (and any scroll)
    drawn.current = all
    let live = true
    const stampNow = Date.now()
    // Mermaid renders one diagram at a time.
    srcs
      .reduce<Promise<string[]>>(
        (done, src, k) => done.then(async (svgs) => [...svgs, (await mermaid.render(`task-graph-${stampNow}-${k}`, src)).svg]),
        Promise.resolve([]),
      )
      .then((svgs) => {
        if (!live || !host.current) return
        host.current.innerHTML = svgs
          .map((svg, k) => `<div class="task-graph-group${parts[k].alone ? ' alone' : ''}">${parts[k].alone ? '<p class="muted small">On their own</p>' : ''}${svg}</div>`)
          .join('')
        // One scale for every diagram (the one that fits the widest), so a
        // small group is not blown up to the full width.
        const drawnSvgs = [...host.current.querySelectorAll<SVGSVGElement>('.task-graph-group svg')]
        const widths = drawnSvgs.map((el) => Number(el.getAttribute('viewBox')?.split(/\s+/)[2]) || 0)
        const scale = Math.min(1, (host.current.clientWidth - 4) / Math.max(1, ...widths))
        drawnSvgs.forEach((el, k) => {
          el.removeAttribute('style')
          el.setAttribute('width', String(Math.round(widths[k] * scale)))
        })
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
          Each group of connected tasks is drawn bottom up, newest group first: an arrow
          runs up from a task to the tasks it waits on. A task waiting on others is
          blocked: it holds no slot, and resumes first once they are all done.
        </p>
      </div>
    </section>
  )
}
