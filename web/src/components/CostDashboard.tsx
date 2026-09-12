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
  { id: 'model', label: 'Model' },
  { id: 'project', label: 'Project' },
  { id: 'state', label: 'Outcome' },
]

/** Distinct enough at a glance, and readable on the dark background. */
const COLORS = ['#7dd3fc', '#4ade80', '#fbbf24', '#f87171', '#c4b5fd', '#67e8f9']

const money = (n: number) => (n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(3)}`)

export function CostDashboard({ onClose }: { onClose: () => void }) {
  const [period, setPeriod] = useState('week')
  const [group, setGroup] = useState('model')
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

  const chart = useMemo(() => buildChart(data?.series ?? []), [data])

  return (
    <section className="viewer costs" role="dialog" aria-label="Cost dashboard">
      <header className="viewer-bar">
        <span className="viewer-path">Cost</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <div className="viewer-body">
        <div className="cost-controls">
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
                <StackedBars chart={chart} />
                <ul className="legend">
                  {chart.series.map((name, i) => (
                    <li key={name}>
                      <span className="swatch" style={{ background: COLORS[i % COLORS.length] }} />
                      {name}
                      <span className="legend-value">{money(chart.totals[name] ?? 0)}</span>
                    </li>
                  ))}
                </ul>
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

function buildChart(points: CostPoint[]): Chart {
  const buckets: string[] = []
  const series: string[] = []
  const values: Record<string, Record<string, number>> = {}
  const totals: Record<string, number> = {}

  for (const p of points) {
    if (!buckets.includes(p.bucket)) buckets.push(p.bucket)
    if (!series.includes(p.series)) series.push(p.series)
    values[p.bucket] = values[p.bucket] ?? {}
    values[p.bucket][p.series] = (values[p.bucket][p.series] ?? 0) + p.cost
    totals[p.series] = (totals[p.series] ?? 0) + p.cost
  }
  const max = Math.max(
    ...buckets.map((b) => Object.values(values[b]).reduce((a, c) => a + c, 0)),
    0.0001,
  )
  return { buckets, series, values, totals, max }
}

/** Plain SVG: a chart library would be a large dependency for one view. */
function StackedBars({ chart }: { chart: Chart }) {
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
                    <title>{`${label(bucket)} · ${name}: ${money(value)}`}</title>
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
