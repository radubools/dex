import { useEffect, useMemo, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { getTaskGraph } from '../api'
import type { TaskGraph as Graph, TaskGraphNode } from '../types'

/**
 * The live task graph of a project (design/18_task_dependencies.md): every
 * task that is queued, running, waiting or blocked, the finished ones joined
 * to them, and an arrow from each task to the ones it waits on. Each request
 * whose plan started tasks stands at the head of them, an arrow to each. Each group of
 * connected tasks is its own diagram, drawn left to right (the task that asked
 * on the left, what it asked for to its right, in columns of at most ten); the
 * groups stack newest first, and
 * tasks with no dependencies share one column at the end, newest at the top.
 * Refetched when the event stream reports a task change (and every half
 * minute in case it is down); a click opens a task.
 */

// The same look as the diagrams in Markdown; initialising twice is harmless.
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'strict',
  themeVariables: { fontFamily: 'ui-sans-serif, system-ui, sans-serif', fontSize: '13px' },
})

// The event stream drives refreshes (App's `changes`): a fetch this long after
// the last reported change, and a slow poll in case the stream is down.
const SETTLE_MS = 400
const FALLBACK_MS = 30_000

// Mermaid's render is not reentrant: every drawing waits for the one before.
let renderQueue: Promise<void> = Promise.resolve()

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

/** At most this many of a task's children share a column; the rest go in further columns. */
const GRID_ROWS = 10

function chart(g: Graph, group: Group): string {
  // Left to right: an edge runs from the task that waits (left) to the ones it waits on (right).
  // Tasks on their own stack top down instead, linked invisibly so they form a column.
  const lines = [group.alone ? 'flowchart TD' : 'flowchart LR']
  for (const i of group.nodes) {
    const n = g.nodes[i]
    const title = esc(n.title.length > 42 ? `${n.title.slice(0, 40)}…` : n.title)
    const thread = esc(n.threadTitle ?? 'no thread')
    if (n.kind === 'request') {
      // The operator's request: a rounded box, its tasks counted.
      const meta = esc(`request · ${n.done ?? 0} of ${n.tasks ?? 0} done`)
      lines.push(`  n${i}(["<b>${title}</b><br/><small>${meta}<br/>${thread}</small>"]):::${n.state}`)
      lines.push(`  class n${i} request`)
      continue
    }
    const meta = esc(`${STATE_LABEL[n.state] ?? n.state}${n.attempt > 1 ? ` · attempt ${n.attempt}` : ''}`)
    lines.push(`  n${i}["<b>${title}</b><br/><small>${meta}<br/>${thread}</small>"]:::${n.state}`)
  }
  const index = new Map(g.nodes.map((n, i) => [n.id, i]))
  // Newest first, so Mermaid lays the latest asks out first (at the top of their column).
  const edges = [...group.edges].sort((a, b) => stamp(g.nodes[index.get(b.to)!]) - stamp(g.nodes[index.get(a.to)!]))
  // Each task's children (in the grid of the first task that waits on them) fill
  // columns of GRID_ROWS, newest first: an invisible link from the child in the
  // same row one column back pushes each later column one rank to the right.
  const children = new Map<number, number[]>()
  const placed = new Set<number>()
  for (const e of edges) {
    const child = index.get(e.to)!
    if (placed.has(child)) continue
    placed.add(child)
    const from = index.get(e.from)!
    children.set(from, [...(children.get(from) ?? []), child])
  }
  // A label is a layout step of its own, which knocks a grid's rows out of line:
  // a task whose children need more than one column gets plain arrows, dashed
  // for an asked-for change, and the footnote says what an arrow means.
  for (const e of edges) {
    const from = index.get(e.from)!
    const to = index.get(e.to)!
    const label = e.kind === 'continued' ? 'asked for a change' : e.kind === 'planned' ? 'planned' : 'waits on'
    if ((children.get(from)?.length ?? 0) > GRID_ROWS) lines.push(`  n${from} ${e.kind === 'continued' ? '-.->' : '-->'} n${to}`)
    else lines.push(`  n${from} -->|${label}| n${to}`)
  }
  for (const kids of children.values()) {
    for (let k = GRID_ROWS; k < kids.length; k++) lines.push(`  n${kids[k - GRID_ROWS]} ~~~ n${kids[k]}`)
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
    // A request keeps its state's border, on a fill of its own.
    '  classDef request fill:#2b2140,color:#f1eaff,stroke-width:2px',
  )
  return lines.join('\n')
}

/**
 * One scale for every diagram (the one that fits the widest in the column),
 * so a small group is not blown up to the full width.
 */
function fit(host: HTMLElement) {
  const svgs = [...host.querySelectorAll<SVGSVGElement>('.task-graph-group svg')]
  const widths = svgs.map((el) => Number(el.getAttribute('viewBox')?.split(/\s+/)[2]) || 0)
  const room = host.clientWidth - 4
  const scale = room > 0 ? Math.min(1, room / Math.max(1, ...widths)) : 1
  svgs.forEach((el, k) => {
    el.removeAttribute('style')
    el.setAttribute('width', String(Math.round(widths[k] * scale)))
  })
}

/** Outlines the node of the task open in the side panel (none: `id` null). */
function mark(host: HTMLElement, id: string | null) {
  host.querySelectorAll('g.node.open').forEach((g) => g.classList.remove('open'))
  if (id) host.querySelector(`g.node[data-task="${CSS.escape(id)}"]`)?.classList.add('open')
}

export function TaskGraph({
  project,
  changes = 0,
  activeTaskId = null,
  onOpenTask,
  onOpenRequest,
}: {
  project: string
  /** Bumped by the app when the event stream reports a task change: refetch soon. */
  changes?: number
  /** The task open in the side panel: its node is outlined. */
  activeTaskId?: string | null
  onOpenTask: (node: TaskGraphNode) => void
  /** A request's node: its thread, where the plan and its tasks are. */
  onOpenRequest?: (node: TaskGraphNode) => void
}) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const host = useRef<HTMLDivElement>(null)
  const activeRef = useRef(activeTaskId)
  activeRef.current = activeTaskId
  // What is on screen now: set only once a drawing has landed, so a drawing
  // that was interrupted is tried again rather than taken as done.
  const drawn = useRef('')
  // The parent's handler changes on every render of the app (which re-renders
  // on every streamed event); read it at click time instead of redrawing for it.
  const openTask = useRef(onOpenTask)
  openTask.current = onOpenTask
  const openRequest = useRef(onOpenRequest)
  openRequest.current = onOpenRequest

  // Fetches: at once, soon after each change the stream reports (several
  // close together make one fetch), and every so often in case the stream dropped.
  const load = useRef<() => void>(() => {})
  useEffect(() => {
    let live = true
    load.current = () => {
      getTaskGraph(project)
        .then((g) => live && (setGraph(g), setError(null)))
        .catch((e: Error) => live && setError(e.message))
    }
    load.current()
    const timer = setInterval(() => load.current(), FALLBACK_MS)
    return () => { live = false; clearInterval(timer) }
  }, [project])
  useEffect(() => {
    if (!changes) return
    const t = setTimeout(() => load.current(), SETTLE_MS)
    return () => clearTimeout(t)
  }, [changes])

  // The diagrams' sources; a fetch with the same content draws nothing new.
  const plan = useMemo(() => {
    if (!graph || !graph.nodes.length) return null
    const parts = groups(graph)
    const srcs = parts.map((p) => chart(graph, p))
    return { graph, parts, srcs, all: srcs.join('\n%%\n') }
  }, [graph])
  const planRef = useRef(plan)
  planRef.current = plan

  useEffect(() => {
    const p = planRef.current
    if (!p || !host.current) return
    if (p.all === drawn.current) return // unchanged: keep the drawing (and any scroll)
    let live = true
    const stampNow = Date.now()
    // Mermaid draws one diagram at a time, across every caller.
    const svgs: string[] = []
    const job = p.srcs.reduce<Promise<void>>(
      (done, src, k) => done.then(async () => {
        if (!live) return
        svgs.push((await mermaid.render(`task-graph-${stampNow}-${k}`, src)).svg)
      }),
      renderQueue,
    )
    renderQueue = job.catch(() => {})
    job
      .then(() => {
        if (!live || !host.current || svgs.length !== p.srcs.length) return
        host.current.innerHTML = svgs
          .map((svg, k) => `<div class="task-graph-group${p.parts[k].alone ? ' alone' : ''}">${p.parts[k].alone ? '<p class="muted small">On their own</p>' : ''}${svg}</div>`)
          .join('')
        drawn.current = p.all
        fit(host.current)
        // Clicks by node, without Mermaid's own click hooks (they need a loose
        // security level): its node groups carry the id we gave them.
        p.graph.nodes.forEach((n, i) => {
          const g = host.current?.querySelector<SVGGElement>(`g.node[id*="-n${i}-"]`)
          if (!g) return
          g.style.cursor = 'pointer'
          g.dataset.task = n.id
          g.addEventListener('click', () => (n.kind === 'request' ? openRequest.current?.(n) : openTask.current(n)))
        })
        mark(host.current, activeRef.current)
      })
      .catch((e: Error) => live && setError(`could not draw the graph: ${e.message}`))
    return () => { live = false }
  }, [plan?.all])

  // The open task's node outlined, as the side panel changes.
  useEffect(() => {
    if (host.current) mark(host.current, activeTaskId)
  }, [activeTaskId])

  // Refit when the column's width changes (the side panel opening or closing).
  useEffect(() => {
    const el = host.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let width = el.clientWidth
    const ro = new ResizeObserver(() => {
      if (el.clientWidth === width) return
      width = el.clientWidth
      fit(el)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const requests = graph?.nodes.filter((n) => n.kind === 'request').length ?? 0
  const tasks = (graph?.nodes.length ?? 0) - requests
  const edges = graph?.edges.filter((e) => e.kind !== 'planned').length ?? 0
  return (
    <section className="task-graph" aria-label="Task graph">
      <div className="task-graph-body">
        {graph && (
          <p className="muted small">
            {tasks} task{tasks === 1 ? '' : 's'}{requests ? ` from ${requests} request${requests === 1 ? '' : 's'}` : ''}, {edges} dependenc{edges === 1 ? 'y' : 'ies'}. Click a task to open it beside the graph, or a request to open its thread.
          </p>
        )}
        {error && <p className="error-note">{error}</p>}
        {graph && !graph.nodes.length && (
          <p className="muted">Nothing is running or waiting in this project.</p>
        )}
        {!graph && !error && <p className="muted">Loading…</p>}
        <div ref={host} className="task-graph-canvas" hidden={!graph?.nodes.length} />
        <p className="muted small">
          Each group of connected tasks is drawn left to right, newest group first: a
          request (rounded) points to the tasks its plan started, and an arrow runs
          right from a task to the tasks it waits on, in columns of at most ten. A task
          waiting on others is
          blocked: it holds no slot, and resumes first once they are all done.
        </p>
      </div>
    </section>
  )
}
