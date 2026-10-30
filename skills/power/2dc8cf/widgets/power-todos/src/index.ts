/**
 * A todo list as it stands: what was asked, how far along it is, and each todo
 * with what it spawned, status and cost.
 *
 * dex rewrites `todos.json` whenever anything about the list changes, and the
 * host reopens the widget on the new text, so everything here is drawn from the
 * file. It fetches nothing.
 */

export type Ctx = { path: string; text: string; theme: string }

export type Todo = {
  id: string
  slug: string
  title: string
  state: string
  activity?: string | null
  costUsd?: number
  /** The todo or task that started this one, if any. */
  spawnedBy?: string | null
}

export type TodoList = { request?: string; updatedAt?: number; todos: Todo[] }

const LABELS: Record<string, string> = {
  todo: 'Todo',
  queued: 'Starting',
  running: 'Running',
  awaiting_input: 'Waiting on you',
  blocked: 'Waiting on tasks',
  paused: 'Paused',
  succeeded: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  archived: 'Archived',
}

/** Finished, whichever way: what the progress counts. */
const ENDED = new Set(['succeeded', 'failed', 'cancelled', 'archived'])

const CSS = `
.pt { font: 13px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); padding: 14px; min-height: 100%; box-sizing: border-box; }
.pt-title { font-size: 15px; font-weight: 600; margin: 0 0 8px; }
.pt-summary { display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.pt-bar { height: 4px; border-radius: 2px; background: var(--line); margin-bottom: 12px; overflow: hidden; }
.pt-bar > i { display: block; height: 100%; background: var(--done); }
.pt-list, .pt-list ul { list-style: none; margin: 0; padding: 0; }
.pt-list ul { margin-left: 16px; border-left: 1px solid var(--line); padding-left: 10px; }
.pt-row { display: flex; align-items: baseline; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }
.pt-main { flex: 1; min-width: 0; }
.pt-name { overflow-wrap: anywhere; }
.pt-activity { color: var(--muted); font-size: 11px; }
.pt-cost { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.pt-chip { flex: none; font-size: 10px; padding: 1px 6px; border-radius: 9px; background: var(--line); }
.pt-chip.running, .pt-chip.queued { background: var(--run); color: #06222e; }
.pt-chip.succeeded { background: var(--done); color: #052e14; }
.pt-chip.failed { background: var(--bad); color: #300; }
.pt-chip.awaiting_input { background: var(--warn); color: #2e2100; }
.pt-empty { color: var(--muted); }
`

const THEMES: Record<string, string> = {
  dark: '--fg:#e6e8ee;--bg:#0f1218;--muted:#8b93a3;--line:#262b35;--run:#7dd3fc;--done:#4ade80;--bad:#f87171;--warn:#fbbf24',
  light: '--fg:#1a1d24;--bg:#fff;--muted:#667085;--line:#e4e7ec;--run:#7dd3fc;--done:#4ade80;--bad:#f87171;--warn:#fbbf24',
}

function el(tag: string, className = '', text?: string): HTMLElement {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

export function parseList(text: string): TodoList {
  const doc = JSON.parse(text)
  if (!doc || !Array.isArray(doc.todos)) throw new Error('not a todo list: it has no `todos`')
  return doc
}

/** A todo's row and, under it, everything it spawned. */
function item(todo: Todo, children: Map<string, Todo[]>): HTMLElement {
  const li = el('li')
  li.dataset.state = todo.state
  const row = el('div', 'pt-row')
  const main = el('div', 'pt-main')
  main.append(el('div', 'pt-name', todo.title))
  if (todo.activity && !ENDED.has(todo.state)) main.append(el('div', 'pt-activity', todo.activity))
  row.append(el('span', `pt-chip ${todo.state}`, LABELS[todo.state] ?? todo.state), main)
  if (todo.costUsd) row.append(el('span', 'pt-cost', `$${todo.costUsd.toFixed(2)}`))
  li.append(row)
  const under = children.get(todo.id)
  if (under?.length) {
    const nested = el('ul')
    nested.append(...under.map((child) => item(child, children)))
    li.append(nested)
  }
  return li
}

export async function mount(host: HTMLElement, ctx: Ctx): Promise<() => void> {
  const doc = parseList(ctx.text)
  const root = el('div', 'pt')
  root.setAttribute('style', THEMES[ctx.theme === 'light' ? 'light' : 'dark'])
  const style = el('style')
  style.textContent = CSS
  root.append(style, el('h1', 'pt-title', doc.request ?? 'Todos'))

  const ids = new Set(doc.todos.map((t) => t.id))
  const children = new Map<string, Todo[]>()
  for (const todo of doc.todos) {
    if (todo.spawnedBy && ids.has(todo.spawnedBy)) {
      children.set(todo.spawnedBy, [...(children.get(todo.spawnedBy) ?? []), todo])
    }
  }
  const tops = doc.todos.filter((t) => !t.spawnedBy || !ids.has(t.spawnedBy))

  if (doc.todos.length === 0) {
    root.append(el('p', 'pt-empty', 'Nothing has been added to this list yet.'))
  } else {
    const ended = doc.todos.filter((t) => ENDED.has(t.state)).length
    const cost = doc.todos.reduce((sum, t) => sum + (t.costUsd ?? 0), 0)
    const summary = el('div', 'pt-summary')
    summary.append(el('span', 'pt-count', `${ended} of ${doc.todos.length} done`), el('span', 'pt-total', `$${cost.toFixed(2)}`))
    const bar = el('div', 'pt-bar')
    const fill = el('i')
    fill.style.width = `${(ended / doc.todos.length) * 100}%`
    bar.append(fill)
    const list = el('ul', 'pt-list')
    list.append(...tops.map((t) => item(t, children)))
    root.append(summary, bar, list)
  }

  host.replaceChildren(root)
  return () => host.replaceChildren()
}
