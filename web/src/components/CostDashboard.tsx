import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getCostTasks, getCosts } from '../api'
import type { CostPoint, CostSlice, CostsResponse, CostTasksResponse } from '../types'
import { bucketKey, bucketLabel, bucketTitle, hasOlder, timeline, type Granularity } from './costTimeline'
import { formatRate, rate } from '../throughput'

/** Each period offers only the granularities that make a readable chart. */
const PERIODS: { id: string; label: string; granularities: Granularity[] }[] = [
  { id: 'day', label: '24h', granularities: ['hour'] },
  { id: 'week', label: '7d', granularities: ['day', 'hour'] },
  { id: 'month', label: '30d', granularities: ['day', 'week'] },
  { id: 'all', label: 'All', granularities: ['week', 'day', 'month'] },
]

const GROUPS = [
  { id: 'skill', label: 'Skill' },
  { id: 'model', label: 'Model' },
  { id: 'project', label: 'Project' },
  { id: 'state', label: 'Outcome' },
]

/** Distinct enough at a glance, and readable on the dark background. */
const COLORS = ['#7dd3fc', '#4ade80', '#fbbf24', '#f87171', '#c4b5fd', '#fb923c', '#f9a8d4', '#a3e635']

type Kind = { name: string; of: (p: CostPoint) => number }

/** Output is split so the bars add up: reasoning is billed as part of it. */
const INPUT: Kind = { name: 'Input', of: (p) => p.input }
const CACHE_READ: Kind = { name: 'Cache read', of: (p) => p.cacheRead }
const CACHE_WRITE: Kind = { name: 'Cache write', of: (p) => p.cacheWrite }
const OUTPUT: Kind = { name: 'Output', of: (p) => Math.max(0, p.output - p.thinking) }
const REASONING: Kind = { name: 'Reasoning', of: (p) => p.thinking }
const KINDS = [INPUT, CACHE_READ, CACHE_WRITE, OUTPUT, REASONING]

/**
 * Cache reads outweigh everything else a hundred to one, so a chart of all five
 * shows only them. The first two views stack kinds; the rest show one kind
 * stacked by the chosen group.
 */
const TOKEN_VIEWS: { id: string; label: string; kinds?: Kind[]; of?: (p: CostPoint) => number }[] = [
  { id: 'uncached', label: 'Uncached', kinds: [INPUT, OUTPUT, REASONING] },
  { id: 'kinds', label: 'All kinds', kinds: KINDS },
  { id: 'output', label: 'Output', of: OUTPUT.of },
  { id: 'reasoning', label: 'Reasoning', of: REASONING.of },
  { id: 'cached', label: 'Cached', of: (p) => p.cacheRead + p.cacheWrite },
]

const money = (n: number) => (n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`)
const whole = (n: number) => (n >= 100 ? `$${Math.round(n)}` : money(n))

const count = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k`
  : String(Math.round(n))

type Metric = 'cost' | 'tokens' | 'code' | 'assets'

const METRICS: { id: Metric; label: string }[] = [
  { id: 'cost', label: 'Cost' },
  { id: 'tokens', label: 'Tokens' },
  { id: 'code', label: 'Code lines' },
  { id: 'assets', label: 'Asset lines' },
]

/** A clicked slice of the chart, and where its list of tasks is. */
type Drill = {
  /** The chart it was picked on: any other chart shows no selection. */
  context: string
  /** The bucket's key on the axis, and the segment clicked in it (none: the whole bar). */
  key: string
  segment: string | null
  slice: CostSlice
  label: string
  sort: string
  page: number
}

/**
 * What the dashboard showed when it was left, so coming back from a task it
 * opened (or any other step back) finds the same chart, slice and page.
 */
const remembered: {
  metric?: Metric; tokenView?: string; period?: string; group?: string; granularity?: Granularity
  projects?: string[]; offset?: number; picked?: string[]; pickedKinds?: string[]; drill?: Drill | null
} = {}

export function CostDashboard({ onClose, onBack, onOpenTask }: {
  onClose: () => void
  onBack?: () => void
  /** Opens a task's own view, with its activity and files. */
  onOpenTask?: (id: string) => void
}) {
  const [metric, setMetric] = useState<Metric>(remembered.metric ?? 'cost')
  const [tokenView, setTokenView] = useState(remembered.tokenView ?? TOKEN_VIEWS[0].id)
  const [period, setPeriod] = useState(remembered.period ?? 'week')
  const [group, setGroup] = useState(remembered.group ?? 'skill')
  const [granularity, setGranularity] = useState<Granularity>(remembered.granularity ?? 'day')
  const [data, setData] = useState<CostsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [projects, setProjects] = useState<string[]>(remembered.projects ?? [])
  /** How many periods back the chart is; 0 is the one ending now. */
  const [offset, setOffset] = useState(remembered.offset ?? 0)
  /** The series left on, by the group's names. Empty means all of them. */
  const [picked, setPicked] = useState<string[]>(remembered.picked ?? [])
  /** The same for the token kinds, when the chart stacks kinds. */
  const [pickedKinds, setPickedKinds] = useState<string[]>(remembered.pickedKinds ?? [])
  /** The slice whose tasks are listed under the chart. */
  const [drill, setDrill] = useState<Drill | null>(remembered.drill ?? null)

  useEffect(() => {
    Object.assign(remembered, { metric, tokenView, period, group, granularity, projects, offset, picked, pickedKinds, drill })
  }, [metric, tokenView, period, group, granularity, projects, offset, picked, pickedKinds, drill])

  // Grouping by project already separates them, so there the filter rests.
  const filter = group === 'project' ? [] : projects
  const filterKey = filter.join(',')
  const context = [period, granularity, group, filterKey, offset, metric, tokenView].join('|')
  const selected = drill?.context === context ? drill : null

  useEffect(() => {
    let live = true
    getCosts(period, granularity, group, filter, offset)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filterKey is `filter`, by value
  }, [period, granularity, group, filterKey, offset])

  const view = TOKEN_VIEWS.find((v) => v.id === tokenView) ?? TOKEN_VIEWS[0]
  const stacksKinds = metric === 'tokens' && !!view.kinds
  const chart = useMemo(() => {
    const points = data?.series ?? []
    const lines = data?.lines ?? []
    const shown = metric === 'cost' ? stacked(points, (p) => p.cost)
      : metric === 'code' ? stacked(lines, (p) => p.code)
      : metric === 'assets' ? stacked(lines, (p) => p.assets)
      : view.kinds ? kinds(points, view.kinds)
      : stacked(points, view.of ?? (() => 0))
    // Kinds are one chart's worth of each group, so a group left off leaves
    // their totals too. Groups keep theirs, to show what clicking would bring back.
    const base = stacksKinds ? shown.filter((m) => wanted(picked, m.raw)) : shown
    const on = stacksKinds
      ? base.filter((m) => wanted(pickedKinds, m.series))
      : base.filter((m) => wanted(picked, m.raw))
    // Drawn with the granularity the data came with, not a click still in flight.
    return collect(base, on, data?.period ?? period, (data?.granularity ?? granularity) as Granularity,
                   data?.earliest, data?.offset ?? 0)
  }, [data, metric, view, period, granularity, picked, pickedKinds, stacksKinds])
  const breakdown = useMemo(() => buildBreakdown(data?.series ?? []), [data])
  // Throughput is about models, not lines or token kinds: by bucket, and by
  // series where the chart's series are the group's.
  const speed = useMemo(
    () => metric === 'code' || metric === 'assets' ? undefined
      : speedByBucket(data?.series ?? [], (data?.granularity ?? granularity) as Granularity,
                      stacksKinds ? undefined : chart.members),
    [data, metric, granularity, stacksKinds, chart],
  )
  const format = metric === 'cost' ? money : count
  const picks = stacksKinds ? pickedKinds : picked
  const setPicks = stacksKinds ? setPickedKinds : setPicked
  const allowed = PERIODS.find((p) => p.id === period)?.granularities ?? []

  /** A click on a bar: its segment, or the whole bar above the stack. Again clears it. */
  const select = (index: number, segment: string | null) => {
    const key = chart.buckets[index]
    const raw = chart.raws[key]
    if (!raw || (selected && selected.key === key && selected.segment === segment)) {
      setDrill(null)
      return
    }
    // A token kind is not a set of tasks: its bar's tasks are the groups shown.
    const groupSeg = stacksKinds ? null : segment
    const series = groupSeg ? chart.members[groupSeg] ?? [groupSeg] : picked.length ? picked : null
    const what = segment ?? (picked.length === 0 ? 'all series' : picked.length === 1 ? picked[0] : `${picked.length} series`)
    setDrill({
      context, key, segment,
      slice: {
        period, granularity, group, projects: filter, offset, bucket: raw, series,
        source: metric === 'code' || metric === 'assets' ? 'lines' : 'usage',
      },
      label: `${chart.titles[index]} · ${what}${stacksKinds && segment && picked.length ? ` (${picked.join(', ')})` : ''}`,
      sort: metric === 'code' || metric === 'assets' ? 'lines' : 'cost',
      page: 0,
    })
  }

  return (
    <section className="viewer costs" role="dialog" aria-label="Cost dashboard">
      <header className="viewer-bar">
        <button className="back-btn" onClick={onBack ?? onClose} aria-label="Back">‹</button>
        <span className="viewer-path">Cost</span>
      </header>

      <div className="viewer-body">
        {/* When, then what to split it by, then what to measure. */}
        <div className="cost-controls">
          <div className="cost-row">
            <div className="segmented">
              {PERIODS.map((p) => (
                <button
                  key={p.id}
                  className={period === p.id ? 'on' : ''}
                  onClick={() => {
                    setPeriod(p.id)
                    setGranularity(p.granularities[0])
                    setOffset(0)
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="segmented">
              {allowed.map((g) => (
                <button key={g} className={granularity === g ? 'on' : ''} onClick={() => setGranularity(g)}>
                  {g}
                </button>
              ))}
            </div>
          </div>
          <div className="cost-row">
            <div className="segmented">
              {GROUPS.map((g) => (
                <button
                  key={g.id}
                  className={group === g.id ? 'on' : ''}
                  onClick={() => {
                    if (g.id !== group) setPicked([])
                    setGroup(g.id)
                  }}
                >
                  {g.label}
                </button>
              ))}
            </div>
            {group !== 'project' && (
              <ProjectFilter all={data?.projects ?? []} chosen={projects} onChange={setProjects} />
            )}
          </div>
          <div className="cost-row">
            <div className="segmented">
              {METRICS.map((m) => (
                <button key={m.id} className={metric === m.id ? 'on' : ''} onClick={() => setMetric(m.id)}>{m.label}</button>
              ))}
            </div>
            {metric === 'tokens' && (
              <div className="segmented">
                {TOKEN_VIEWS.map((v) => (
                  <button
                    key={v.id}
                    className={view.id === v.id ? 'on' : ''}
                    onClick={() => {
                      setTokenView(v.id)
                      setPickedKinds([])
                    }}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {error && <p className="error">{error}</p>}

        {data && (
          <>
            <div className="cost-grid wide">
              {metric === 'code' || metric === 'assets'
                ? ([['Code lines', 'code'], ['Asset lines', 'assets']] as const).map(([name, key]) => (
                    <div key={key} className="cost-cell">
                      <span className="cost-label">{name}</span>
                      <span className="cost-value">{count((data.lines ?? []).reduce((a, p) => a + p[key], 0))}</span>
                    </div>
                  ))
                : metric === 'cost'
                ? (['day', 'week', 'month', 'all_time'] as const).map((k) => (
                    <div key={k} className="cost-cell">
                      <span className="cost-label">{k === 'all_time' ? 'all time' : k}</span>
                      <span className="cost-value">{money(data.totals[k])}</span>
                    </div>
                  ))
                : KINDS.map((k) => (
                    <div key={k.name} className="cost-cell">
                      <span className="cost-label">{k.name}</span>
                      <span className="cost-value">{count(data.series.reduce((a, p) => a + k.of(p), 0))}</span>
                    </div>
                  ))}
            </div>

            {period !== 'all' && (
              <div className="cost-pager">
                <button
                  className="icon-btn"
                  aria-label="Previous period"
                  disabled={!hasOlder(data.earliest, period, granularity, offset)}
                  onClick={() => setOffset(offset + 1)}
                >‹</button>
                <span className="cost-range">{rangeOf(chart)}</span>
                <button
                  className="icon-btn"
                  aria-label="Next period"
                  disabled={offset === 0}
                  onClick={() => setOffset(offset - 1)}
                >›</button>
              </div>
            )}

            {chart.buckets.length === 0 ? (
              <p className="muted small">
                {metric === 'code' || metric === 'assets' ? 'No lines changed in this period.' : 'No spend recorded in this period.'}
              </p>
            ) : (
              <>
                <StackedBars key={offset} chart={chart} format={format} speed={speed}
                             selected={selected ? { key: selected.key, segment: selected.segment } : null}
                             onSelect={select} />
                {selected ? (
                  <SliceTasks
                    drill={selected}
                    onChange={(next) => setDrill({ ...selected, ...next })}
                    onClear={() => setDrill(null)}
                    onWhole={() => select(chart.buckets.indexOf(selected.key), null)}
                    onOpen={onOpenTask}
                  />
                ) : (
                  <p className="muted small cost-hint">Click a bar, or a part of one, to list its tasks.</p>
                )}
                <ul className="legend">
                  <li>
                    <button
                      className={`legend-item ${picks.length === 0 ? '' : 'off'}`}
                      aria-pressed={picks.length === 0}
                      onClick={() => setPicks([])}
                    >
                      <span className="swatch all" />
                      All series
                      <span className="legend-value">
                        {format(Object.values(chart.totals).reduce((a, c) => a + c, 0))}
                      </span>
                    </button>
                  </li>
                  {chart.series.map((name, i) => {
                    const names = chart.members[name] ?? [name]
                    const on = picks.length > 0 && names.every((n) => picks.includes(n))
                    return (
                      <li key={name}>
                        <button
                          className={`legend-item ${picks.length === 0 || on ? '' : 'off'}`}
                          aria-pressed={on}
                          onClick={() => setPicks(toggled(picks, names))}
                        >
                          <span className="swatch" style={{ background: COLORS[i % COLORS.length] }} />
                          {name}
                          <span className="legend-value">{format(chart.totals[name] ?? 0)}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
                {(metric === 'cost' || metric === 'tokens') && (
                  <>
                    <Breakdown rows={breakdown} picked={picked} onPick={setPicked} />
                    <Coverage coverage={data.coverage} />
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
    </section>
  )
}

function ProjectFilter({ all, chosen, onChange }: {
  all: string[]
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

  const label = chosen.length === 0 ? 'All projects'
    : chosen.length === 1 ? chosen[0]
    : `${chosen.length} projects`

  return (
    <details className="cost-filter" ref={box}>
      <summary aria-label="Filter by project">{label}</summary>
      <div className="cost-filter-menu">
        {chosen.length > 0 && (
          <button className="ghost-btn" onClick={() => onChange([])}>All projects</button>
        )}
        {all.map((p) => (
          <label key={p}>
            <input
              type="checkbox"
              checked={chosen.includes(p)}
              onChange={(e) => onChange(e.target.checked ? [...chosen, p] : chosen.filter((x) => x !== p))}
            />
            {p}
          </label>
        ))}
      </div>
    </details>
  )
}

type Chart = {
  buckets: string[]
  /** Each bucket's start as the server sent it, which is how the drill-down names it. */
  raws: Record<string, string>
  labels: string[]
  titles: string[]
  /** A week of hours can't label every few bars, so it labels each day. */
  dayTicks: boolean
  series: string[]
  /** What each series stands for: Other is several groups. */
  members: Record<string, string[]>
  values: Record<string, Record<string, number>>
  totals: Record<string, number>
  max: number
}

/** `series` is what the chart stacks; `raw` the group the value belongs to. */
type Mark = { bucket: string; series: string; raw: string; value: number }

const wanted = (picks: string[], name: string) => picks.length === 0 || picks.includes(name)

/** The first click narrows to one; later ones add or remove; none left is all. */
const toggled = (picks: string[], names: string[]) =>
  names.every((n) => picks.includes(n))
    ? picks.filter((p) => !names.includes(p))
    : [...new Set([...picks, ...names])]

/** More series than colours reads as noise, so the smallest fold into Other. */
function stacked<P extends { bucket: string; series: string }>(points: P[], of: (p: P) => number): Mark[] {
  const spent: Record<string, number> = {}
  for (const p of points) spent[p.series] = (spent[p.series] ?? 0) + of(p)
  const names = Object.keys(spent).sort((a, b) => spent[b] - spent[a])
  const kept = names.length <= COLORS.length ? names : names.slice(0, COLORS.length - 1)
  return points.map((p) => ({
    bucket: p.bucket,
    series: kept.includes(p.series) ? p.series : 'Other',
    raw: p.series,
    value: of(p),
  }))
}

function kinds(points: CostPoint[], list: Kind[]): Mark[] {
  return points.flatMap((p) => list.map((k) => ({ bucket: p.bucket, series: k.name, raw: p.series, value: k.of(p) })))
}

const rangeOf = (chart: Chart) =>
  chart.titles.length > 1 ? `${chart.titles[0]} – ${chart.titles[chart.titles.length - 1]}` : chart.titles[0] ?? ''

function collect(
  marks: Mark[], on: Mark[], period: string, g: Granularity, anchor?: string | null, offset = 0,
): Chart {
  const values: Record<string, Record<string, number>> = {}
  const totals: Record<string, number> = {}
  const members: Record<string, string[]> = {}
  const raws: Record<string, string> = {}
  for (const m of marks) raws[bucketKey(m.bucket, g)] ??= m.bucket
  for (const m of on) {
    const key = bucketKey(m.bucket, g)
    values[key] = values[key] ?? {}
    values[key][m.series] = (values[key][m.series] ?? 0) + m.value
  }
  for (const m of marks) {
    totals[m.series] = (totals[m.series] ?? 0) + m.value
    const names = (members[m.series] ??= [])
    if (m.series === 'Other' && !names.includes(m.raw)) names.push(m.raw)
  }
  for (const name of Object.keys(members)) if (name !== 'Other') members[name] = [name]
  // Marks of `kinds` come in display order; everything else goes largest first.
  const order = [...new Set(marks.map((m) => m.series))]
  const isKinds = order.every((s) => KINDS.some((k) => k.name === s))
  const series = isKinds ? order : order.sort((a, b) =>
    a === 'Other' ? 1 : b === 'Other' ? -1 : totals[b] - totals[a])
  const buckets = timeline(marks.map((m) => m.bucket), period, g, anchor, offset)
  const max = Math.max(
    ...buckets.map((b) => Object.values(values[b] ?? {}).reduce((a, c) => a + c, 0)),
    0.0001,
  )
  const dayTicks = g === 'hour' && buckets.length > 24
  return {
    buckets,
    raws,
    dayTicks,
    labels: buckets.map((b) => bucketLabel(b, dayTicks ? 'day' : g)),
    titles: buckets.map((b) => bucketTitle(b, g)),
    series,
    members,
    values,
    totals,
    max,
  }
}

type Row = {
  name: string; cost: number; input: number; cached: number; output: number; thinking: number
  speed: Sums
}

function buildBreakdown(points: CostPoint[]): Row[] {
  const rows: Record<string, Row> = {}
  for (const p of points) {
    const r = (rows[p.series] ??= {
      name: p.series, cost: 0, input: 0, cached: 0, output: 0, thinking: 0, speed: noSums(),
    })
    addSums(r.speed, p)
    r.cost += p.cost
    r.input += p.input
    r.cached += p.cacheRead + p.cacheWrite
    r.output += Math.max(0, p.output - p.thinking)
    r.thinking += p.thinking
  }
  return Object.values(rows).sort((a, b) => b.cost - a.cost)
}

function Breakdown({ rows, picked, onPick }: {
  rows: Row[]
  picked: string[]
  onPick: (next: string[]) => void
}) {
  const sum = (of: (r: Row) => number) => rows.reduce((a, r) => a + of(r), 0)
  const all: Row = {
    name: 'All series',
    cost: sum((r) => r.cost),
    input: sum((r) => r.input),
    cached: sum((r) => r.cached),
    output: sum((r) => r.output),
    thinking: sum((r) => r.thinking),
    speed: rows.reduce((a, r) => plus(a, r.speed), noSums()),
  }
  return (
    <div className="chart-scroll">
      <table className="cost-table">
        <thead>
          <tr>
            <th /><th>Cost</th><th>Input</th><th>Cached</th><th>Output</th><th>Reasoning</th>
            <th title="Average input tokens read per second (uncached), over timed model calls">In t/s</th>
            <th title="Average output tokens written per second, over timed model calls">Out t/s</th>
          </tr>
        </thead>
        <tbody>
          {[all, ...rows].map((r) => (
            <tr
              key={r.name}
              className={r === all ? 'all' : picked.length === 0 || picked.includes(r.name) ? '' : 'off'}
              onClick={() => onPick(r === all ? [] : toggled(picked, [r.name]))}
            >
              <th scope="row">{r.name}</th>
              <td>{whole(r.cost)}</td>
              <td>{count(r.input)}</td>
              <td>{count(r.cached)}</td>
              <td>{count(r.output)}</td>
              <td>{count(r.thinking)}</td>
              <td>{formatRate(rate(r.speed.timedIn, r.speed.inSeconds))}</td>
              <td>{formatRate(rate(r.speed.timedOut, r.speed.outSeconds))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** A span's throughput sums: rates are always these divided, never averaged. */
type Sums = { timedIn: number; inSeconds: number; timedOut: number; outSeconds: number }
type SpeedMap = Record<string, Record<string, Sums>>

const noSums = (): Sums => ({ timedIn: 0, inSeconds: 0, timedOut: 0, outSeconds: 0 })
const plus = (a: Sums, b: Sums): Sums => ({
  timedIn: a.timedIn + b.timedIn, inSeconds: a.inSeconds + b.inSeconds,
  timedOut: a.timedOut + b.timedOut, outSeconds: a.outSeconds + b.outSeconds,
})
function addSums(into: Sums, p: CostPoint) {
  into.timedIn += p.timedIn ?? 0
  into.inSeconds += p.inSeconds ?? 0
  into.timedOut += p.timedOut ?? 0
  into.outSeconds += p.outSeconds ?? 0
}

/** Each bucket's sums, overall under '' and by the chart's series (Other folded). */
function speedByBucket(points: CostPoint[], g: Granularity, members?: Record<string, string[]>): SpeedMap {
  const other = new Set(members?.Other ?? [])
  const map: SpeedMap = {}
  for (const p of points) {
    const key = bucketKey(p.bucket, g)
    const bucket = (map[key] ??= {})
    addSums((bucket[''] ??= noSums()), p)
    if (members) addSums((bucket[other.has(p.series) ? 'Other' : p.series] ??= noSums()), p)
  }
  return map
}

function SpeedLine({ sums, className }: { sums: Sums; className?: string }) {
  const tin = rate(sums.timedIn, sums.inSeconds)
  const tout = rate(sums.timedOut, sums.outSeconds)
  if (tin == null && tout == null) return null
  return (
    <div className={`muted small ${className ?? ''}`} title="Average tokens per second over timed model calls">
      in {formatRate(tin)} t/s · out {formatRate(tout)} t/s
    </div>
  )
}

/** Old spend has no exact record: say how much, instead of implying it does. */
function Coverage({ coverage }: { coverage?: CostsResponse['coverage'] }) {
  // An older server sends none: the page may be newer than the dex it talks to.
  if (!coverage || coverage.spend <= 0) return null
  const { spend, with_tokens: tokens, inferred } = coverage
  const share = (n: number) => `${Math.round((n / spend) * 100)}%`
  return (
    <p className="muted small">
      Tokens known for {share(tokens)} of this spend.
      {inferred > 0 && ` ${share(inferred)} was attributed afterwards from history, so its skills are a best guess.`}
    </p>
  )
}

const MIN_SLOT = 7
const TICK_WIDTH = 38
const CARD_WIDTH = 200
const PIN_MS = 5000

/** Plain SVG: a chart library would be a large dependency for one view. */
function StackedBars({ chart, format, speed, selected, onSelect }: {
  chart: Chart
  format: (n: number) => string
  speed?: SpeedMap
  /** The slice listed below: a bucket, and the segment in it (none: the whole bar). */
  selected?: { key: string; segment: string | null } | null
  onSelect?: (index: number, segment: string | null) => void
}) {
  const height = 180
  const scroller = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState(320)

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const measure = () => setRoom(el.clientWidth)
    measure()
    const watch = new ResizeObserver(measure)
    watch.observe(el)
    return () => watch.disconnect()
  }, [])

  const slot = Math.max(room / chart.buckets.length, MIN_SLOT)
  const width = slot * chart.buckets.length
  const gap = Math.min(4, slot * 0.2)
  const barWidth = slot - gap
  const everyTick = Math.ceil(TICK_WIDTH / slot)
  const [hovered, setHovered] = useState<number | null>(null)
  const plot = useRef<HTMLDivElement>(null)
  const unpin = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // A finger lifting counts as leaving, so a tap pins the card instead: until
  // this runs out, another bar is tapped, or a tap lands outside the plot.
  const pin = (i: number) => {
    setHovered(i)
    clearTimeout(unpin.current)
    unpin.current = setTimeout(() => setHovered(null), PIN_MS)
  }

  useEffect(() => {
    const away = (e: PointerEvent) => {
      if (plot.current?.contains(e.target as Node)) return
      clearTimeout(unpin.current)
      setHovered(null)
    }
    document.addEventListener('pointerdown', away)
    return () => {
      document.removeEventListener('pointerdown', away)
      clearTimeout(unpin.current)
    }
  }, [])

  const scrolled = scroller.current?.scrollLeft ?? 0
  // Beside the bar, on whichever side has room.
  const barAt = hovered === null ? 0 : hovered * slot - scrolled
  const cardLeft = barAt > room / 2 ? Math.max(0, barAt - CARD_WIDTH - 6) : Math.min(barAt + slot + 6, room - CARD_WIDTH)

  // The newest bars are the ones wanted, and they sit at the far end.
  useLayoutEffect(() => {
    const el = scroller.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [chart, room])

  return (
    <div
      className="cost-plot"
      ref={plot}
      onPointerLeave={(e) => e.pointerType === 'mouse' && setHovered(null)}
    >
      <div className="chart-scroll" ref={scroller}>
      <svg className="cost-chart" width={width} height={height + 26} role="img"
           aria-label="Cost over time">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1="0" x2={width} y1={height - height * f} y2={height - height * f}
                className="grid" />
        ))}
        {chart.buckets.map((bucket, i) => {
          let offset = 0
          const picked = selected?.key === bucket
          const classes = [hovered === i ? 'on' : '', selected && !picked ? 'dim' : '', picked ? 'picked' : '']
          return (
            <g key={bucket} transform={`translate(${i * slot}, 0)`} className={classes.filter(Boolean).join(' ') || undefined}>
              {chart.series.map((name, si) => {
                const value = chart.values[bucket]?.[name] ?? 0
                if (value <= 0) return null
                const h = (value / chart.max) * height
                offset += h
                return (
                  <rect
                    key={name}
                    x="0"
                    y={height - offset}
                    width={barWidth}
                    height={Math.max(h, 1)}
                    fill={COLORS[si % COLORS.length]}
                    rx="2"
                    data-series={name}
                    className={picked && selected?.segment && selected.segment !== name ? 'dim' : undefined}
                  />
                )
              })}
              {/* The whole column answers, so a tiny or empty bar can still be read. */}
              <rect
                className="cost-column"
                x="0"
                y="0"
                width={slot}
                height={height}
                onPointerEnter={(e) => e.pointerType === 'mouse' && setHovered(i)}
                onPointerDown={(e) => (e.pointerType === 'mouse' ? setHovered(i) : pin(i))}
                onClick={(e) => {
                  if (!onSelect) return
                  // The segment under the pointer, from the same stack the bars are drawn from.
                  const box = e.currentTarget.getBoundingClientRect()
                  const y = box.height ? ((e.clientY - box.top) / box.height) * height : height
                  onSelect(i, segmentAt(chart, bucket, height - y, height))
                }}
              />
              {(chart.dayTicks ? bucket.endsWith('T00') : (chart.buckets.length - 1 - i) % everyTick === 0) && (
                <text x={i === chart.buckets.length - 1 ? barWidth : barWidth / 2} y={height + 16} className="tick"
                      style={i === chart.buckets.length - 1 ? { textAnchor: 'end' } : undefined}>
                  {chart.labels[i]}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      </div>
      {hovered !== null && (
        <BucketCard chart={chart} index={hovered} format={format} left={cardLeft}
                    speed={speed?.[chart.buckets[hovered]]} />
      )}
    </div>
  )
}

/** Which series' segment of a bar sits `up` pixels above its foot; none above the stack. */
function segmentAt(chart: Chart, bucket: string, up: number, height: number): string | null {
  let top = 0
  for (const name of chart.series) {
    const value = chart.values[bucket]?.[name] ?? 0
    if (value <= 0) continue
    top += (value / chart.max) * height
    if (up <= top) return name
  }
  return null
}

const PAGE_SIZE = 10

const when = (seconds: number | null) =>
  seconds == null ? '' : new Date(seconds * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })

/** The tasks a clicked slice sums, a page at a time, largest first. */
function SliceTasks({ drill, onChange, onClear, onWhole, onOpen }: {
  drill: Drill
  onChange: (next: Partial<Pick<Drill, 'sort' | 'page'>>) => void
  onClear: () => void
  /** From a segment to its whole bar: the tallest bar has no room above it to click. */
  onWhole: () => void
  onOpen?: (id: string) => void
}) {
  const [data, setData] = useState<CostTasksResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ask = JSON.stringify([drill.slice, drill.sort, drill.page])

  useEffect(() => {
    let live = true
    setError(null)
    getCostTasks(drill.slice, drill.sort, drill.page, PAGE_SIZE)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `ask` is the request, by value
  }, [ask])

  const lines = drill.slice.source === 'lines'
  const pages = data ? Math.max(1, Math.ceil(data.total / (data.limit || PAGE_SIZE))) : 1
  const sorter = (id: string, label: string) => (
    <button
      className={`cost-sort ${drill.sort === id ? 'on' : ''}`}
      aria-pressed={drill.sort === id}
      onClick={() => onChange({ sort: id, page: 0 })}
    >{label}</button>
  )

  return (
    <section className="cost-tasks" aria-label="Tasks in this slice">
      <div className="cost-tasks-head">
        <span className="cost-tasks-label">{drill.label}</span>
        {data && (
          <span className="muted small">
            {data.total} {data.total === 1 ? 'task' : 'tasks'}
            {lines ? ` · ${count((data.code ?? 0) + (data.assets ?? 0))} lines` : ` · ${money(data.cost ?? 0)}`}
          </span>
        )}
        {drill.segment && <button className="ghost-btn" onClick={onWhole}>Whole bar</button>}
        <button className="icon-btn" aria-label="Clear selection" onClick={onClear}>✕</button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && data.tasks.length === 0 && <p className="muted small">No tasks in this slice.</p>}
      {data && data.tasks.length > 0 && (
        <div className="chart-scroll">
          <table className="cost-table cost-task-table">
            <thead>
              <tr>
                <th>Task</th><th>Model</th><th>State</th>
                {lines ? <th>{sorter('lines', 'Lines')}</th> : <><th>{sorter('cost', 'Cost')}</th><th>{sorter('tokens', 'Tokens')}</th></>}
                <th>{sorter('date', 'Date')}</th>
              </tr>
            </thead>
            <tbody>
              {data.tasks.map((t) => (
                <tr key={t.id} onClick={() => onOpen?.(t.id)}>
                  <th scope="row">
                    <button className="cost-task-title" onClick={(e) => { e.stopPropagation(); onOpen?.(t.id) }}>
                      {t.title || t.slug}
                    </button>
                    {t.project && <span className="muted"> · {t.project}</span>}
                  </th>
                  <td>{t.model ?? ''}</td>
                  <td className="cost-task-state"><span className={`state-dot ${t.state}`} />{t.state.replace('_', ' ')}</td>
                  {lines
                    ? <td title={`${count(t.code ?? 0)} code, ${count(t.assets ?? 0)} asset`}>{count((t.code ?? 0) + (t.assets ?? 0))}</td>
                    : <><td>{money(t.cost ?? 0)}</td><td>{count(t.tokens ?? 0)}</td></>}
                  <td>{when(t.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && pages > 1 && (
        <div className="cost-pager">
          <button className="icon-btn" aria-label="Previous page of tasks" disabled={drill.page === 0}
                  onClick={() => onChange({ page: drill.page - 1 })}>‹</button>
          <span className="cost-range">Page {drill.page + 1} of {pages}</span>
          <button className="icon-btn" aria-label="Next page of tasks" disabled={drill.page + 1 >= pages}
                  onClick={() => onChange({ page: drill.page + 1 })}>›</button>
        </div>
      )}
    </section>
  )
}

/** A bar's values, largest first, so nothing has to be read off the chart. */
function BucketCard({ chart, index, format, left, speed }: {
  chart: Chart
  index: number
  format: (n: number) => string
  left: number
  /** This bucket's throughput, overall ('') and by series. */
  speed?: Record<string, Sums>
}) {
  const values = chart.values[chart.buckets[index]] ?? {}
  const rows = chart.series
    .map((name, i) => ({ name, color: COLORS[i % COLORS.length], value: values[name] ?? 0 }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
  const total = rows.reduce((a, r) => a + r.value, 0)

  return (
    <div className="cost-card" role="tooltip" style={{ left }}>
      <div className="cost-card-head">
        <span>{chart.titles[index]}</span>
        <strong>{format(total)}</strong>
      </div>
      {speed?.[''] && <SpeedLine sums={speed['']} className="cost-card-speed" />}
      {rows.length === 0 && <div className="muted small">Nothing spent</div>}
      {rows.map((r) => (
        <div key={r.name}>
          <div className="cost-card-row">
            <span className="swatch" style={{ background: r.color }} />
            <span className="cost-card-name">{r.name}</span>
            <span>{format(r.value)}</span>
          </div>
          {speed?.[r.name] && <SpeedLine sums={speed[r.name]} className="cost-card-speed indent" />}
        </div>
      ))}
    </div>
  )
}
