/**
 * The lines of the task panel's machine popup (components/MachineButton.tsx).
 *
 * A local model (`oc:local/…`) runs behind dex's model proxy, which reads the
 * Mac and its server every few seconds (a `machine` event): its popup always
 * has every line, in the same order, with a dash where a value is missing, so
 * a gap is seen rather than the line moving. A cloud model (Claude, Gemini,
 * OpenCode Go) has no machine to read: its popup has only the lines with a
 * value, which is its tokens per second, and no popup at all without them.
 */
import { formatRate } from './throughput'
import type { MachineReading } from './types'

export type MachineLine = {
  key: string
  emoji: string
  /** What the line is, for the screen reader and the hover title. */
  label: string
  /** null when there is nothing to show. */
  text: string | null
}

/** A model dex runs on this Mac behind its proxy. */
export const isLocal = (model: string | null | undefined) => /^(oc:)?local\//.test(model ?? '')

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const w = (v: number) => `${v.toFixed(1)} W`
const gbs = (v: number) => `${Math.round(v)} GB/s`

function battery(watts: number | undefined): string | null {
  if (!num(watts)) return null
  if (watts > 0) return `charging ${w(watts)}`
  if (watts < 0) return `discharging ${w(-watts)}`
  return 'idle'
}

function ago(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return s < 90 ? `${s} s ago` : `${Math.round(s / 60)} min ago`
}

/**
 * Every line there could be, for this model, its latest machine reading and its
 * own tokens per second (the task's timed calls, throughput.ts): all of them
 * for a local model, only those with a value for any other.
 */
export function machineLines(
  model: string | null | undefined,
  machine: (MachineReading & { ts: number }) | undefined,
  speed: { in: number | null; out: number | null },
  now: number,
): MachineLine[] {
  const p = machine?.power, d = machine?.dram, g = machine?.gpu, t = machine?.tokens ?? undefined
  const lines: MachineLine[] = [
    {
      key: 'power', emoji: '⚡', label: 'Power from the adapter',
      text: num(p?.adapter_w) ? `${w(p.adapter_w)} from the adapter${num(p.wall_w) ? ` (~${w(p.wall_w)} at the wall)` : ''}` : null,
    },
    { key: 'battery', emoji: '🔋', label: 'Battery', text: battery(p?.battery_w) },
    {
      key: 'dram', emoji: '🧠', label: 'Memory bandwidth',
      text: d?.total ? `DRAM ${gbs(d.total.avg_gbs)}`
        + (d.read || d.write ? ` (read ${d.read ? Math.round(d.read.avg_gbs) : '–'}, write ${d.write ? Math.round(d.write.avg_gbs) : '–'})` : '')
        + ` · p95 ${Math.round(d.total.p95_gbs)}` : null,
    },
    {
      key: 'gpu', emoji: '🎮', label: 'GPU',
      text: num(g?.active) ? `GPU ${Math.round(g.active * 100)}% busy${num(g.mhz) ? ` @ ${g.mhz} MHz` : ''}`
        + (num(g.busy_ceiling_tflops) ? ` (ceiling ${g.busy_ceiling_tflops.toFixed(1)} TFLOPS)` : '') : null,
    },
    { key: 'in', emoji: '📥', label: 'Input tokens per second (this task)', text: speed.in != null ? `in ${formatRate(speed.in)} t/s` : null },
    { key: 'out', emoji: '📤', label: 'Output tokens per second (this task)', text: speed.out != null ? `out ${formatRate(speed.out)} t/s` : null },
    {
      key: 'server', emoji: '🖥️', label: 'The model server, over all its requests',
      text: num(t?.in_tps) || num(t?.out_tps)
        ? `server ${formatRate(num(t?.in_tps) ? t.in_tps : null)} in · ${formatRate(num(t?.out_tps) ? t.out_tps : null)} out t/s` : null,
    },
    {
      key: 'work', emoji: '🧮', label: 'Floating-point work the tokens take (FP16, weights only)',
      text: num(t?.tflops) ? `~${t.tflops.toFixed(2)} TFLOPS FP16` : null,
    },
    {
      key: 'age', emoji: '⏱️', label: 'How old the reading is',
      text: machine ? `read ${ago(now - machine.ts)}${num(p?.fresh_s) && p.fresh_s > 0 ? ` · power ${p.fresh_s} s older` : ''}` : null,
    },
  ]
  return isLocal(model) ? lines : lines.filter((l) => l.text != null)
}
