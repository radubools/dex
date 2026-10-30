import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getCosts } from '../api'
import type { CostPoint, CostsResponse } from '../types'
import { bucketKey, bucketLabel, bucketTitle, hasOlder, timeline, type Granularity } from './costTimeline'

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

export function CostDashboard({ onClose, onBack }: { onClose: () => void; onBack?: () => void }) {
  const [metric, setMetric] = useState<'cost' | 'tokens'>('cost')
  const [tokenView, setTokenView] = useState(TOKEN_VIEWS[0].id)
  const [period, setPeriod] = useState('week')
  const [group, setGroup] = useState('skill')
  const [granularity, setGranularity] = useState<Granularity>('day')
  const [data, setData] = useState<CostsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [projects, setProjects] = useState<string[]>([])
  /** How many periods back the chart is; 0 is the one ending now. */
  const [offset, setOffset] = useState(0)

  // Grouping by project already separates them, so there the filter rests.
  const filter = group === 'project' ? [] : projects
  const filterKey = filter.join(',')

  useEffect(() => {
    let live = true
    getCosts(period, granularity, group, filter, offset)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filterKey is `filter`, by value
  }, [period, granularity, group, filterKey, offset])

  const view = TOKEN_VIEWS.find((v) => v.id === tokenView) ?? TOKEN_VIEWS[0]
  const chart = useMemo(() => {
    const points = data?.series ?? []
    const shown = metric === 'cost' ? stacked(points, (p) => p.cost)
      : view.kinds ? kinds(points, view.kinds)
      : stacked(points, view.of ?? (() => 0))
    // Drawn with the granularity the data came with, not a click still in flight.
    return collect(shown, data?.period ?? period, (data?.granularity ?? granularity) as Granularity,
                   data?.earliest, data?.offset ?? 0)
  }, [data, metric, view, period, granularity])
  const breakdown = useMemo(() => buildBreakdown(data?.series ?? []), [data])
  const format = metric === 'cost' ? money : count
  const allowed = PERIODS.find((p) => p.id === period)?.granularities ?? []

  return (
    <section className="viewer costs" role="dialog" aria-label="Cost dashboard">
      <header className="viewer-bar">
        <button className="back-btn" onClick={onBack ?? onClose} aria-label="Back">‹</button>
        <span className="viewer-path">Cost</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <div className="viewer-body">
        <div className="cost-controls">
          <div className="segmented">
            <button className={metric === 'cost' ? 'on' : ''} onClick={() => setMetric('cost')}>Cost</button>
            <button className={metric === 'tokens' ? 'on' : ''} onClick={() => setMetric('tokens')}>Tokens</button>
          </div>
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
          <div className="segmented">
            {GROUPS.map((g) => (
              <button key={g.id} className={group === g.id ? 'on' : ''} onClick={() => setGroup(g.id)}>
                {g.label}
              </button>
            ))}
          </div>
          {group !== 'project' && (
            <ProjectFilter all={data?.projects ?? []} chosen={projects} onChange={setProjects} />
          )}
          {metric === 'tokens' && (
            <div className="segmented">
              {TOKEN_VIEWS.map((v) => (
                <button key={v.id} className={view.id === v.id ? 'on' : ''} onClick={() => setTokenView(v.id)}>
                  {v.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {error && <p className="error">{error}</p>}

        {data && (
          <>
            <div className="cost-grid wide">
              {metric === 'cost'
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
              <p className="muted small">No spend recorded in this period.</p>
            ) : (
              <>
                <StackedBars key={offset} chart={chart} format={format} />
                <ul className="legend">
                  {chart.series.map((name, i) => (
                    <li key={name}>
                      <span className="swatch" style={{ background: COLORS[i % COLORS.length] }} />
                      {name}
                      <span className="legend-value">{format(chart.totals[name] ?? 0)}</span>
                    </li>
                  ))}
                </ul>
                <Breakdown rows={breakdown} />
                <Coverage coverage={data.coverage} />
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
  labels: string[]
  titles: string[]
  /** A week of hours can't label every few bars, so it labels each day. */
  dayTicks: boolean
  series: string[]
  values: Record<string, Record<string, number>>
  totals: Record<string, number>
  max: number
}

type Mark = { bucket: string; series: string; value: number }

/** More series than colours reads as noise, so the smallest fold into Other. */
function stacked(points: CostPoint[], of: (p: CostPoint) => number): Mark[] {
  const spent: Record<string, number> = {}
  for (const p of points) spent[p.series] = (spent[p.series] ?? 0) + of(p)
  const names = Object.keys(spent).sort((a, b) => spent[b] - spent[a])
  const kept = names.length <= COLORS.length ? names : names.slice(0, COLORS.length - 1)
  return points.map((p) => ({
    bucket: p.bucket,
    series: kept.includes(p.series) ? p.series : 'Other',
    value: of(p),
  }))
}

function kinds(points: CostPoint[], list: Kind[]): Mark[] {
  return points.flatMap((p) => list.map((k) => ({ bucket: p.bucket, series: k.name, value: k.of(p) })))
}

const rangeOf = (chart: Chart) =>
  chart.titles.length > 1 ? `${chart.titles[0]} – ${chart.titles[chart.titles.length - 1]}` : chart.titles[0] ?? ''

function collect(
  marks: Mark[], period: string, g: Granularity, anchor?: string | null, offset = 0,
): Chart {
  const values: Record<string, Record<string, number>> = {}
  const totals: Record<string, number> = {}
  for (const m of marks) {
    const key = bucketKey(m.bucket, g)
    values[key] = values[key] ?? {}
    values[key][m.series] = (values[key][m.series] ?? 0) + m.value
    totals[m.series] = (totals[m.series] ?? 0) + m.value
  }
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
    dayTicks,
    labels: buckets.map((b) => bucketLabel(b, dayTicks ? 'day' : g)),
    titles: buckets.map((b) => bucketTitle(b, g)),
    series,
    values,
    totals,
    max,
  }
}

type Row = { name: string; cost: number; input: number; cached: number; output: number; thinking: number }

function buildBreakdown(points: CostPoint[]): Row[] {
  const rows: Record<string, Row> = {}
  for (const p of points) {
    const r = (rows[p.series] ??= { name: p.series, cost: 0, input: 0, cached: 0, output: 0, thinking: 0 })
    r.cost += p.cost
    r.input += p.input
    r.cached += p.cacheRead + p.cacheWrite
    r.output += Math.max(0, p.output - p.thinking)
    r.thinking += p.thinking
  }
  return Object.values(rows).sort((a, b) => b.cost - a.cost)
}

function Breakdown({ rows }: { rows: Row[] }) {
  return (
    <div className="chart-scroll">
      <table className="cost-table">
        <thead>
          <tr><th /><th>Cost</th><th>Input</th><th>Cached</th><th>Output</th><th>Reasoning</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <th scope="row">{r.name}</th>
              <td>{whole(r.cost)}</td>
              <td>{count(r.input)}</td>
              <td>{count(r.cached)}</td>
              <td>{count(r.output)}</td>
              <td>{count(r.thinking)}</td>
            </tr>
          ))}
        </tbody>
      </table>
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

/** Plain SVG: a chart library would be a large dependency for one view. */
function StackedBars({ chart, format }: { chart: Chart; format: (n: number) => string }) {
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
    <div className="cost-plot" onPointerLeave={() => setHovered(null)}>
      <div className="chart-scroll" ref={scroller}>
      <svg className="cost-chart" width={width} height={height + 26} role="img"
           aria-label="Cost over time">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1="0" x2={width} y1={height - height * f} y2={height - height * f}
                className="grid" />
        ))}
        {chart.buckets.map((bucket, i) => {
          let offset = 0
          return (
            <g key={bucket} transform={`translate(${i * slot}, 0)`} className={hovered === i ? 'on' : undefined}>
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
                onPointerEnter={() => setHovered(i)}
                onPointerDown={() => setHovered(i)}
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
      {hovered !== null && <BucketCard chart={chart} index={hovered} format={format} left={cardLeft} />}
    </div>
  )
}

/** A bar's values, largest first, so nothing has to be read off the chart. */
function BucketCard({ chart, index, format, left }: {
  chart: Chart
  index: number
  format: (n: number) => string
  left: number
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
      {rows.length === 0 && <div className="muted small">Nothing spent</div>}
      {rows.map((r) => (
        <div key={r.name} className="cost-card-row">
          <span className="swatch" style={{ background: r.color }} />
          <span className="cost-card-name">{r.name}</span>
          <span>{format(r.value)}</span>
        </div>
      ))}
    </div>
  )
}
