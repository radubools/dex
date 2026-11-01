import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import dagre from '@dagrejs/dagre'
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
 * minute in case it is down); a click opens a task. dagre places the boxes and
 * they are plain HTML, so a filter or an update lands within a frame or two.
 */

// The event stream drives refreshes (App's `changes`): a fetch this long after
// the last reported change, and a slow poll in case the stream is down.
const SETTLE_MS = 400
const FALLBACK_MS = 30_000

const STATE_LABEL: Record<string, string> = {
  todo: 'todo', queued: 'queued', running: 'running', awaiting_input: 'waiting on you',
  paused: 'paused', blocked: 'waiting on tasks', succeeded: 'done',
  failed: 'failed', cancelled: 'cancelled',
}

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

/**
 * The tasks in the chosen states (none chosen: all of them), and each request
 * that started one. Arrows go with the tasks: groups() keeps only those between
 * nodes that are left.
 */
function narrowed(g: Graph, states: string[]): Graph {
  if (states.length === 0) return g
  const kept = new Set(g.nodes.filter((n) => n.kind !== 'request' && states.includes(n.state)).map((n) => n.id))
  const asked = new Set(g.edges.flatMap((e) => (kept.has(e.to) ? [e.from] : kept.has(e.from) ? [e.to] : [])))
  return { ...g, nodes: g.nodes.filter((n) => kept.has(n.id) || (n.kind === 'request' && asked.has(n.id))) }
}

/** At most this many of a task's children share a column; the rest go in further columns. */
const GRID_ROWS = 10

const NODE_WIDTH = 230
const NODE_HEIGHT = 66
const ROW_GAP = 14
const COLUMN_GAP = 14
const MIN_SCALE = 0.6
const BLOCK_PADDING = 10
const LABEL_WIDTH = 78
const LABEL_HEIGHT = 16

type Point = { x: number; y: number }
type Frame = Point & { key: string; width: number; height: number }
type Arrow = { key: string; path: string; dashed: boolean; label: string | null; at: Point }
/** A group laid out: where each node and arrow goes, in unscaled pixels. */
type Board = { key: string; alone: boolean; width: number; height: number; nodes: (TaskGraphNode & Point)[]; arrows: Arrow[]; frames: Frame[] }

/** A smooth line through dagre's bend points: each corner rounded through the middles around it. */
function curve(points: Point[]): string {
  let path = `M${points[0].x} ${points[0].y}`
  for (let k = 1; k < points.length - 1; k++) {
    const [p, next] = [points[k], points[k + 1]]
    path += ` Q${p.x} ${p.y} ${(p.x + next.x) / 2} ${(p.y + next.y) / 2}`
  }
  const end = points[points.length - 1]
  return `${path} L${end.x} ${end.y}`
}

function layout(g: Graph, group: Group): Board {
  const at = (i: number) => g.nodes[i]
  const key = at(group.nodes[0]).id
  // Tasks on their own stack in one column, in the order given.
  if (group.alone) {
    const nodes = group.nodes.map((i, row) => ({ ...at(i), x: 0, y: row * (NODE_HEIGHT + ROW_GAP) }))
    return { key, alone: true, width: NODE_WIDTH, height: nodes[nodes.length - 1].y + NODE_HEIGHT, nodes, arrows: [], frames: [] }
  }
  // Left to right: an edge runs from the task that waits (left) to the ones it waits on (right).
  const d = new dagre.graphlib.Graph()
  d.setGraph({ rankdir: 'LR', nodesep: ROW_GAP, ranksep: 44, edgesep: 8, marginx: 2, marginy: 2 })
  d.setDefaultEdgeLabel(() => ({}))
  const index = new Map(g.nodes.map((n, i) => [n.id, i]))
  // Newest first, so dagre lays the latest asks out first (at the top of their column).
  const edges = [...group.edges].sort((a, b) => stamp(at(index.get(b.to)!)) - stamp(at(index.get(a.to)!)))
  // A task with more than GRID_ROWS children that wait on nothing else keeps them
  // in a block of columns of GRID_ROWS, newest first, placed here: to dagre the
  // block is one big node with one arrow. Grid links for it to honour took ten
  // times as long, and an arrow to each of forty boxes says nothing one doesn't.
  const waiters = new Map<string, string[]>()
  for (const e of edges) waiters.set(e.to, [...(waiters.get(e.to) ?? []), e.from])
  const waits = new Set(edges.map((e) => e.from))
  const blocks = new Map<string, string[]>()
  for (const from of waits) {
    const leaves = edges
      .filter((e) => e.from === from && waiters.get(e.to)?.length === 1 && !waits.has(e.to))
      .map((e) => e.to)
    if (leaves.length > GRID_ROWS) blocks.set(from, leaves)
  }
  const inBlock = new Set([...blocks.values()].flat())
  const cell = (columns: number, rows: number) => ({
    width: columns * (NODE_WIDTH + COLUMN_GAP) - COLUMN_GAP + 2 * BLOCK_PADDING,
    height: rows * (NODE_HEIGHT + ROW_GAP) - ROW_GAP + 2 * BLOCK_PADDING,
  })
  for (const i of group.nodes) if (!inBlock.has(at(i).id)) d.setNode(at(i).id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  for (const [from, leaves] of blocks) {
    d.setNode(`block:${from}`, cell(Math.ceil(leaves.length / GRID_ROWS), GRID_ROWS))
    d.setEdge(from, `block:${from}`, {})
  }
  const free = edges.filter((e) => !inBlock.has(e.to))
  for (const e of free) d.setEdge(e.from, e.to, { width: LABEL_WIDTH, height: LABEL_HEIGHT, labelpos: 'c' })
  dagre.layout(d)
  const spots = new Map<string, Point>()
  const frames = [...blocks].map(([from, leaves]) => {
    const { x, y, width, height } = d.node(`block:${from}`)
    const [left, top] = [x - width / 2, y - height / 2]
    leaves.forEach((id, k) => spots.set(id, {
      x: left + BLOCK_PADDING + Math.floor(k / GRID_ROWS) * (NODE_WIDTH + COLUMN_GAP),
      y: top + BLOCK_PADDING + (k % GRID_ROWS) * (NODE_HEIGHT + ROW_GAP),
    }))
    return { key: from, x: left, y: top, width, height }
  })
  const nodes = group.nodes.map((i) => {
    const { id } = at(i)
    if (spots.has(id)) return { ...at(i), ...spots.get(id)! }
    const { x, y } = d.node(id)
    return { ...at(i), x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 }
  })
  const arrows: Arrow[] = free.map((e) => {
    const { points, x, y } = d.edge(e.from, e.to) as { points: Point[]; x: number; y: number }
    return {
      key: `${e.from}-${e.to}`,
      path: curve(points),
      dashed: e.kind === 'continued',
      label: e.kind === 'continued' ? 'asked for a change' : e.kind === 'planned' ? 'planned' : 'waits on',
      at: { x, y },
    }
  })
  for (const from of blocks.keys()) {
    const { points } = d.edge(from, `block:${from}`) as { points: Point[] }
    arrows.push({ key: `${from}-block`, path: curve(points), dashed: false, label: null, at: points[0] })
  }
  const { width = 0, height = 0 } = d.graph()
  return { key, alone: false, width, height, nodes, arrows, frames }
}

/** A group's boxes and arrows. Memoized: the app re-renders on every streamed event. */
const GroupBoard = memo(function GroupBoard({ board, scale, activeTaskId, onOpen }: {
  board: Board
  scale: number
  activeTaskId: string | null
  onOpen: (node: TaskGraphNode) => void
}) {
  const { width, height } = board
  return (
    <div className={`task-graph-group${board.alone ? ' alone' : ''}`}>
      {board.alone && <p className="muted small">On their own</p>}
      <div style={{ width: width * scale, height: height * scale }}>
        <div className="task-graph-board" style={{ width, height, transform: `scale(${scale})` }}>
          <svg width={width} height={height} aria-hidden="true">
            <defs>
              <marker id="task-graph-head" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0 0 L8 4 L0 8 z" />
              </marker>
            </defs>
            {board.arrows.map((a) => (
              <path key={a.key} d={a.path} markerEnd="url(#task-graph-head)" strokeDasharray={a.dashed ? '4 3' : undefined} />
            ))}
          </svg>
          {board.frames.map((f) => (
            <div key={f.key} className="task-graph-block" style={{ left: f.x, top: f.y, width: f.width, height: f.height }} />
          ))}
          {board.arrows.map((a) => a.label && (
            <span key={a.key} className="task-graph-label" style={{ left: a.at.x, top: a.at.y }}>{a.label}</span>
          ))}
          {board.nodes.map((n) => {
            const request = n.kind === 'request'
            const meta = request
              ? `request · ${n.done ?? 0} of ${n.tasks ?? 0} done`
              : `${STATE_LABEL[n.state] ?? n.state}${n.attempt > 1 ? ` · attempt ${n.attempt}` : ''}`
            return (
              <button
                key={n.id}
                className={`graph-node ${n.state}${request ? ' request' : ''}${n.id === activeTaskId ? ' open' : ''}`}
                style={{ left: n.x, top: n.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
                title={n.title}
                onClick={() => onOpen(n)}
              >
                <b>{n.title}</b>
                <small>{meta}</small>
                <small>{n.threadTitle ?? 'no thread'}</small>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
})

/** Which task states the graph shows, any number of them; none chosen is all. */
function StatusFilter({ graph, chosen, onChange }: {
  graph: Graph
  chosen: string[]
  onChange: (next: string[]) => void
}) {
  const box = useRef<HTMLDetailsElement>(null)

  useEffect(() => {
    const away = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) box.current.open = false
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [])

  const counts = new Map<string, number>()
  for (const n of graph.nodes) if (n.kind !== 'request') counts.set(n.state, (counts.get(n.state) ?? 0) + 1)
  // What the graph has now, and what is ticked even if its last task just moved on.
  const states = Object.keys(STATE_LABEL).filter((s) => counts.has(s) || chosen.includes(s))
  const label = chosen.length === 0 ? 'All statuses'
    : chosen.length === 1 ? STATE_LABEL[chosen[0]]
    : `${chosen.length} statuses`

  return (
    <details className="cost-filter" ref={box}>
      <summary aria-label="Filter by status">{label}</summary>
      <div className="cost-filter-menu">
        {chosen.length > 0 && (
          <button className="ghost-btn" onClick={() => onChange([])}>All statuses</button>
        )}
        {states.map((s) => (
          <label key={s}>
            <input
              type="checkbox"
              checked={chosen.includes(s)}
              onChange={(e) => onChange(e.target.checked ? [...chosen, s] : chosen.filter((x) => x !== s))}
            />
            {STATE_LABEL[s]}
            <span className="tag-count">{counts.get(s) ?? 0}</span>
          </label>
        ))}
      </div>
    </details>
  )
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
  const [states, setStates] = useState<string[]>([])
  const host = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState(0)
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

  const shown = useMemo(() => graph && narrowed(graph, states), [graph, states])

  const boards = useMemo(
    () => (shown ? groups(shown).map((group) => layout(shown, group)) : []),
    [shown],
  )

  // Clicks by node kind, read at click time: the board keeps its memo.
  const open = useCallback((node: TaskGraphNode) => {
    if (node.kind === 'request') openRequest.current?.(node)
    else openTask.current(node)
  }, [])

  // The column's width (it changes as the side panel opens or closes).
  useLayoutEffect(() => {
    const el = host.current
    if (!el) return
    setRoom(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setRoom(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // One scale for every diagram (the one that fits the widest in the column),
  // so a small group is not blown up to the full width. Past what can be read
  // the widest scrolls sideways instead.
  const widest = Math.max(1, ...boards.map((b) => b.width))
  const scale = room > 4 ? Math.max(MIN_SCALE, Math.min(1, (room - 4) / widest)) : 1

  const ids = new Set(shown?.nodes.map((n) => n.id))
  const requests = shown?.nodes.filter((n) => n.kind === 'request').length ?? 0
  const tasks = (shown?.nodes.length ?? 0) - requests
  const edges = shown?.edges.filter((e) => e.kind !== 'planned' && ids.has(e.from) && ids.has(e.to)).length ?? 0
  return (
    <section className="task-graph" aria-label="Task graph">
      <div className="task-graph-body">
        {graph && graph.nodes.length > 0 && (
          <div className="task-graph-filters">
            <StatusFilter graph={graph} chosen={states} onChange={setStates} />
          </div>
        )}
        {graph && (
          <p className="muted small">
            {tasks} task{tasks === 1 ? '' : 's'}{requests ? ` from ${requests} request${requests === 1 ? '' : 's'}` : ''}, {edges} dependenc{edges === 1 ? 'y' : 'ies'}. Click a task to open it beside the graph, or a request to open its thread.
          </p>
        )}
        {error && <p className="error-note">{error}</p>}
        {graph && !graph.nodes.length && (
          <p className="muted">Nothing is running or waiting in this project.</p>
        )}
        {graph && graph.nodes.length > 0 && !shown?.nodes.length && (
          <p className="muted">No tasks with this status.</p>
        )}
        {!graph && !error && <p className="muted">Loading…</p>}
        <div ref={host} className="task-graph-canvas" hidden={!shown?.nodes.length}>
          {boards.map((board) => (
            <GroupBoard key={board.key} board={board} scale={scale} activeTaskId={activeTaskId} onOpen={open} />
          ))}
        </div>
        <p className="muted small">
          Each group of connected tasks is drawn left to right, newest group first: a
          request (rounded) points to the tasks its plan started, and an arrow runs
          right from a task to the tasks it waits on. More than ten are one framed
          block, in columns of ten, with one arrow to it. A task
          waiting on others is
          blocked: it holds no slot, and resumes first once they are all done.
        </p>
      </div>
    </section>
  )
}
