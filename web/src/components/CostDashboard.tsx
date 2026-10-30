import { useEffect, useMemo, useState } from 'react'
import { getCosts } from '../api'
import type { CostPoint, CostsResponse } from '../types'

const PERIODS = [
  { id: 'day', label: '24h', granularity: 'hour' },
  { id: 'week', label: '7d', granularity: 'day' },
  { id: 'month', label: '30d', granularity: 'day' },
  { id: 'all', label: 'All', granularity: 'week' },
]

const GROUPS = [
  { id: 'skill', label: 'Skill' },
  { id: 'model', label: 'Model' },
  { id: 'project', label: 'Project' },
  { id: 'state', label: 'Outcome' },
]

/** Distinct enough at a glance, and readable on the dark background. */
const COLORS = ['#7dd3fc', '#4ade80', '#fbbf24', '#f87171', '#c4b5fd', '#67e8f9', '#f9a8d4', '#a3e635']

/** Output is split so the bars add up: reasoning is billed as part of it. */
const TOKEN_KINDS: { name: string; of: (p: CostPoint) => number }[] = [
  { name: 'Input', of: (p) => p.input },
  { name: 'Cache read', of: (p) => p.cacheRead },
  { name: 'Cache write', of: (p) => p.cacheWrite },
  { name: 'Output', of: (p) => Math.max(0, p.output - p.thinking) },
  { name: 'Reasoning', of: (p) => p.thinking },
]

const money = (n: number) => (n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`)

const count = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k`
  : String(Math.round(n))

export function CostDashboard({ onClose, onBack }: { onClose: () => void; onBack?: () => void }) {
  const [period, setPeriod] = useState('week')
  const [metric, setMetric] = useState<'cost' | 'tokens'>('cost')
  const [group, setGroup] = useState('skill')
  const [granularity, setGranularity] = useState('day')
  const [data, setData] = useState<CostsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getCosts(period, granularity, group)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
  }, [period, granularity, group])

  const chart = useMemo(
    () => (metric === 'cost' ? buildChart(data?.series ?? []) : buildTokenChart(data?.series ?? [])),
    [data, metric],
  )
  const breakdown = useMemo(() => buildBreakdown(data?.series ?? []), [data])
  const format = metric === 'cost' ? money : count

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
                  setGranularity(p.granularity)
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="segmented">
            {['hour', 'day', 'week', 'month'].map((g) => (
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
        </div>

        {error && <p className="error">{error}</p>}

        {data && (
          <>
            <div className="cost-grid wide">
              {(['day', 'week', 'month', 'all_time'] as const).map((k) => (
                <div key={k} className="cost-cell">
                  <span className="cost-label">{k === 'all_time' ? 'all time' : k}</span>
                  <span className="cost-value">{money(data.totals[k])}</span>
                </div>
              ))}
            </div>

            {chart.buckets.length === 0 ? (
              <p className="muted small">No spend recorded in this period.</p>
            ) : (
              <>
                <StackedBars chart={chart} format={format} />
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

type Chart = {
  buckets: string[]
  series: string[]
  values: Record<string, Record<string, number>>
  totals: Record<string, number>
  max: number
}

/** More series than colours reads as noise, so the smallest fold into Other. */
function buildChart(points: CostPoint[]): Chart {
  const spent: Record<string, number> = {}
  for (const p of points) spent[p.series] = (spent[p.series] ?? 0) + p.cost
  const shown = Object.keys(spent).sort((a, b) => spent[b] - spent[a]).slice(0, COLORS.length - 1)
  return collect(
    points.map((p) => ({
      bucket: p.bucket,
      series: Object.keys(spent).length < COLORS.length || shown.includes(p.series) ? p.series : 'Other',
      value: p.cost,
    })),
  )
}

function buildTokenChart(points: CostPoint[]): Chart {
  return collect(
    points.flatMap((p) => TOKEN_KINDS.map((k) => ({ bucket: p.bucket, series: k.name, value: k.of(p) }))),
  )
}

function collect(points: { bucket: string; series: string; value: number }[]): Chart {
  const buckets: string[] = []
  const series: string[] = []
  const values: Record<string, Record<string, number>> = {}
  const totals: Record<string, number> = {}

  for (const p of points) {
    if (!buckets.includes(p.bucket)) buckets.push(p.bucket)
    if (!series.includes(p.series)) series.push(p.series)
    values[p.bucket] = values[p.bucket] ?? {}
    values[p.bucket][p.series] = (values[p.bucket][p.series] ?? 0) + p.value
    totals[p.series] = (totals[p.series] ?? 0) + p.value
  }
  const max = Math.max(
    ...buckets.map((b) => Object.values(values[b]).reduce((a, c) => a + c, 0)),
    0.0001,
  )
  return { buckets, series, values, totals, max }
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
              <td>{money(r.cost)}</td>
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
function Coverage({ coverage }: { coverage: CostsResponse['coverage'] }) {
  const { spend, with_tokens: tokens, inferred } = coverage
  if (spend <= 0) return null
  const share = (n: number) => `${Math.round((n / spend) * 100)}%`
  return (
    <p className="muted small">
      Tokens known for {share(tokens)} of this spend.
      {inferred > 0 && ` ${share(inferred)} was attributed afterwards from history, so its skills are a best guess.`}
    </p>
  )
}

/** Plain SVG: a chart library would be a large dependency for one view. */
function StackedBars({ chart, format }: { chart: Chart; format: (n: number) => string }) {
  const height = 180
  const gap = 4
  const width = Math.max(chart.buckets.length * 28, 260)
  const barWidth = width / chart.buckets.length - gap

  return (
    <div className="chart-scroll">
      <svg className="cost-chart" width={width} height={height + 26} role="img"
           aria-label="Cost over time">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1="0" x2={width} y1={height - height * f} y2={height - height * f}
                className="grid" />
        ))}
        {chart.buckets.map((bucket, i) => {
          let offset = 0
          return (
            <g key={bucket} transform={`translate(${i * (barWidth + gap)}, 0)`}>
              {chart.series.map((name, si) => {
                const value = chart.values[bucket][name] ?? 0
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
                  >
                    <title>{`${label(bucket)} · ${name}: ${format(value)}`}</title>
                  </rect>
                )
              })}
              <text x={barWidth / 2} y={height + 16} className="tick">
                {label(bucket)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function label(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso.slice(5, 10)
    : d.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })
}
