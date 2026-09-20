/**
 * Laying a parsed MIDI file out as staff notation, as an SVG string.
 *
 * The horizontal axis is *time in seconds*, taken from the file's tempo map —
 * not the usual engraver's spacing, where a bar of semiquavers is as wide as a
 * bar of semibreves. That choice is what makes the cursor honest: its x is the
 * same function of time that every notehead was placed with, so "the cursor is
 * on the note you can hear" is true by construction rather than by tuning.
 *
 * No DOM here either: this builds a string, so node can render a score and
 * check it without a browser.
 */
import { bpmAt, secondsAt, type Smf } from './smf.ts'

export type Note = {
  /** Seconds from the start of the piece. */
  start: number
  end: number
  pitch: number
}

export type Staff = {
  name: string
  clef: 'treble' | 'bass'
  notes: Note[]
}

export type Score = {
  title: string
  duration: number
  staves: Staff[]
  /** Bar line positions, in seconds. */
  bars: number[]
  bpm: number
  /** Sharps (+) or flats (-) in the key signature; decides note spelling. */
  keySf: number
}

/** Pitch class to letter (C=0..B=6) and alteration, spelled with sharps. */
const SHARP_LETTER = [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6]
const SHARP_ALTER = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
const FLAT_LETTER = [0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6]
const FLAT_ALTER = [0, -1, 0, -1, 0, 0, -1, 0, -1, 0, -1, 0]
const LETTER_NAMES = ['C', 'D', 'E', 'F', 'G', 'A', 'B']

/** A note's diatonic step number and accidental, given how the key spells it. */
export function spell(pitch: number, keySf: number): { step: number; alter: number; name: string } {
  const useFlats = keySf < 0
  const pc = ((pitch % 12) + 12) % 12
  const letter = useFlats ? FLAT_LETTER[pc] : SHARP_LETTER[pc]
  const alter = useFlats ? FLAT_ALTER[pc] : SHARP_ALTER[pc]
  // A B# spells upward into the next octave and a Cb downward; MIDI's own
  // octave is of the pitch, so take it from the unaltered letter instead.
  const octave = Math.floor((pitch - alter) / 12) - 1
  return {
    step: octave * 7 + letter,
    alter,
    name: `${LETTER_NAMES[letter]}${alter > 0 ? '#' : alter < 0 ? 'b' : ''}${octave}`,
  }
}

/** Bar line ticks implied by the file's time signatures, up to `endTick`. */
function barTicks(smf: Smf, endTick: number): number[] {
  const sigs =
    smf.timeSigs.length > 0 && smf.timeSigs[0].tick === 0
      ? smf.timeSigs
      : [{ tick: 0, numerator: 4, denominator: 4 }, ...smf.timeSigs]
  const ticks: number[] = []
  for (let i = 0; i < sigs.length; i++) {
    const sig = sigs[i]
    const until = i + 1 < sigs.length ? Math.min(sigs[i + 1].tick, endTick) : endTick
    const perBar = (smf.division * 4 * sig.numerator) / sig.denominator
    if (!(perBar > 0)) continue
    for (let tick = sig.tick; tick <= until; tick += perBar) ticks.push(tick)
    // 5000 bar lines is a corrupt file, not a long piece.
    if (ticks.length > 5000) break
  }
  return ticks
}

/**
 * Turn a parsed MIDI file into staves of timed notes.
 *
 * One staff per track that sounds anything, except that a track spanning both
 * sides of middle C by more than two octaves — a piano part written to one
 * track, which is most of them — is split into the grand staff a reader
 * expects instead of being crammed onto one with eight ledger lines.
 */
export function buildScore(smf: Smf, title: string): Score {
  const seconds = secondsAt(smf)
  const staves: Staff[] = []

  smf.tracks.forEach((track, index) => {
    if (track.notes.length === 0) return
    const pitches = track.notes.map((n) => n.pitch)
    const low = Math.min(...pitches)
    const high = Math.max(...pitches)
    const name = track.name || (smf.tracks.filter((t) => t.notes.length > 0).length > 1 ? `Track ${index + 1}` : 'Score')
    const toNote = (n: (typeof track.notes)[number]): Note => ({
      start: seconds(n.tick),
      end: seconds(n.tick + n.durTicks),
      pitch: n.pitch,
    })

    if (low < 55 && high > 67 && high - low > 24) {
      const upper = track.notes.filter((n) => n.pitch >= 60).map(toNote)
      const lower = track.notes.filter((n) => n.pitch < 60).map(toNote)
      if (upper.length > 0) staves.push({ name, clef: 'treble', notes: upper })
      if (lower.length > 0) staves.push({ name: upper.length > 0 ? '' : name, clef: 'bass', notes: lower })
      return
    }
    const median = pitches.slice().sort((a, b) => a - b)[pitches.length >> 1]
    staves.push({ name, clef: median >= 58 ? 'treble' : 'bass', notes: track.notes.map(toNote) })
  })

  let duration = 0
  for (const staff of staves) for (const note of staff.notes) duration = Math.max(duration, note.end)
  duration = Math.max(duration, seconds(smf.endTick))

  return {
    title,
    duration,
    staves,
    bars: barTicks(smf, smf.endTick).map(seconds).filter((t) => t <= duration + 0.001),
    bpm: bpmAt(smf, 0),
    keySf: smf.keySf,
  }
}

export type Palette = {
  ink: string
  rule: string
  faint: string
  accent: string
  sounding: string
}

export const PALETTES: Record<'dark' | 'light', Palette> = {
  dark: { ink: '#e7e9ee', rule: '#414a5a', faint: '#8a93a5', accent: '#7fd8ff', sounding: '#ffd479' },
  light: { ink: '#14161a', rule: '#b9c0cc', faint: '#6b7383', accent: '#0b6e99', sounding: '#c2700a' },
}

/** One line of the score: the time span it covers and where it sits. */
export type System = { t0: number; t1: number; top: number; bottom: number }

export type Layout = {
  svg: string
  systems: System[]
  /** Where a system's time axis starts, in px from the left of the SVG. */
  x0: number
  pxPerSecond: number
  width: number
  height: number
}

const SP = 7 // half the gap between staff lines; a staff is 8 of these tall
const LABEL_W = 84
const RIGHT_PAD = 16
const STAFF_H = SP * 8

/** Bottom-line diatonic step of each clef: E4 for treble, G2 for bass. */
const BOTTOM_STEP = { treble: 4 * 7 + 2, bass: 2 * 7 + 4 }
const MIDDLE_STEP = { treble: 4 * 7 + 6, bass: 3 * 7 + 1 }

const esc = (text: string) =>
  text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string)

/** Lay the score out at a given pixel width. Pure: returns markup and geometry. */
export function layout(score: Score, width: number, palette: Palette): Layout {
  // The staff starts at `staffX`, but time zero is placed after the clef, so a
  // notehead can sit exactly on its own time without landing on the clef.
  const staffX = LABEL_W + 6
  const x0 = staffX + 40
  const available = Math.max(160, width - x0 - RIGHT_PAD)

  // Wide enough to read, dense enough that a long piece is not fifty lines.
  let pxPerSecond = 62
  const maxSystems = 36
  if (score.duration > 0 && score.duration / (available / pxPerSecond) > maxSystems) {
    pxPerSecond = Math.max(12, (maxSystems * available) / score.duration)
  }
  const rawSpan = available / pxPerSecond

  // How much room each staff needs above and below its five lines, from the
  // notes it actually carries rather than a guess that is always wrong once.
  const padding = score.staves.map((staff) => {
    let above = 0
    let below = 0
    for (const note of staff.notes) {
      const { step } = spell(note.pitch, score.keySf)
      const bottom = BOTTOM_STEP[staff.clef]
      above = Math.max(above, (step - (bottom + 8)) * (SP / 2))
      below = Math.max(below, (bottom - step) * (SP / 2))
    }
    return {
      above: Math.min(Math.max(above + SP * 2.6, SP * 3), SP * 12),
      below: Math.min(Math.max(below + SP * 2.6, SP * 3), SP * 12),
    }
  })
  const systemH = padding.reduce((sum, p) => sum + p.above + STAFF_H + p.below, 0) + 14

  // Time spans, snapped to bar lines so a system never ends mid-bar.
  const systems: System[] = []
  let t0 = 0
  let guard = 0
  while (t0 < score.duration - 1e-6 && guard++ < 500) {
    let t1 = t0 + rawSpan
    const lastBar = score.bars.filter((b) => b > t0 + rawSpan * 0.35 && b <= t1 + 1e-6).pop()
    if (lastBar !== undefined) t1 = lastBar
    if (t1 <= t0) t1 = t0 + rawSpan
    systems.push({ t0, t1: Math.min(t1, score.duration), top: 0, bottom: 0 })
    t0 = t1
  }
  if (systems.length === 0) systems.push({ t0: 0, t1: Math.max(score.duration, 1), top: 0, bottom: 0 })

  const parts: string[] = []
  let y = 8
  systems.forEach((system, index) => {
    system.top = y
    const spanW = (system.t1 - system.t0) * pxPerSecond
    const endX = x0 + Math.max(spanW, 2)

    // The elapsed time at the start of the line, the way a DAW rules its
    // timeline -- the one label that makes a cursor halfway down verifiable.
    parts.push(
      `<text x="4" y="${(y + 11).toFixed(1)}" fill="${palette.faint}" font-size="10" font-family="ui-monospace,monospace">${clock(system.t0)}</text>`,
    )

    let staffY = y + 12
    score.staves.forEach((staff, staffIndex) => {
      const pad = padding[staffIndex]
      const topLine = staffY + pad.above
      const bottomLine = topLine + STAFF_H
      for (let line = 0; line < 5; line++) {
        const ly = (topLine + line * SP * 2).toFixed(1)
        parts.push(
          `<line x1="${staffX}" y1="${ly}" x2="${endX.toFixed(1)}" y2="${ly}" stroke="${palette.rule}" stroke-width="1"/>`,
        )
      }
      if (staff.name) {
        parts.push(
          `<text x="${LABEL_W}" y="${(topLine + STAFF_H / 2 + 4).toFixed(1)}" text-anchor="end" fill="${palette.faint}" font-size="11">${esc(staff.name)}</text>`,
        )
      }
      parts.push(clefGlyph(staff.clef, staffX + 4, topLine))

      for (const bar of score.bars) {
        if (bar <= system.t0 + 1e-6 || bar > system.t1 + 1e-6) continue
        // Three pixels early: on a time axis the downbeat note is *at* the bar
        // time, and a line through its head is how engraving does not look.
        const bx = (x0 + (bar - system.t0) * pxPerSecond - 3).toFixed(1)
        parts.push(
          `<line x1="${bx}" y1="${topLine.toFixed(1)}" x2="${bx}" y2="${bottomLine.toFixed(1)}" stroke="${palette.rule}" stroke-width="1"/>`,
        )
      }
      parts.push(
        `<line x1="${staffX}" y1="${topLine.toFixed(1)}" x2="${staffX}" y2="${bottomLine.toFixed(1)}" stroke="${palette.rule}" stroke-width="1.4"/>`,
      )

      for (const note of staff.notes) {
        if (note.end <= system.t0 + 1e-6 || note.start >= system.t1 - 1e-6) continue
        parts.push(
          drawNote(note, staff.clef, score, system, x0, pxPerSecond, bottomLine, palette),
        )
      }
      staffY = bottomLine + pad.below
    })

    // The closing double bar, so the last system reads as an ending.
    if (index === systems.length - 1) {
      const top = y + 12 + padding[0].above
      const bottom = staffY - padding[padding.length - 1].below
      parts.push(
        `<line x1="${(endX - 3).toFixed(1)}" y1="${top.toFixed(1)}" x2="${(endX - 3).toFixed(1)}" y2="${bottom.toFixed(1)}" stroke="${palette.rule}" stroke-width="1"/>`,
        `<line x1="${endX.toFixed(1)}" y1="${top.toFixed(1)}" x2="${endX.toFixed(1)}" y2="${bottom.toFixed(1)}" stroke="${palette.rule}" stroke-width="2.4"/>`,
      )
    }

    system.bottom = y + systemH
    y += systemH
  })

  parts.push(
    `<line class="cursor" x1="0" y1="0" x2="0" y2="0" stroke="${palette.accent}" stroke-width="1.6" stroke-linecap="round" opacity="0.9"/>`,
  )

  const height = Math.ceil(y + 6)
  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="ui-sans-serif,system-ui,sans-serif">${parts.join('')}</svg>`,
    systems,
    x0,
    pxPerSecond,
    width,
    height,
  }
}

function drawNote(
  note: Note,
  clef: 'treble' | 'bass',
  score: Score,
  system: System,
  x0: number,
  pxPerSecond: number,
  bottomLine: number,
  palette: Palette,
): string {
  const { step, alter } = spell(note.pitch, score.keySf)
  const bottom = BOTTOM_STEP[clef]
  const cy = bottomLine - (step - bottom) * (SP / 2)
  // A note tied over from the previous system is drawn at the line's start,
  // so its sustain bar still shows the reader it is sounding.
  const held = note.start < system.t0
  // No nudge: the head's centre *is* its time, which is what lets the cursor
  // land on the note rather than near it.
  const cx = x0 + Math.max(0, note.start - system.t0) * pxPerSecond
  const rx = SP * 0.66
  const ry = SP * 0.5
  const secondsPerBeat = 60 / (score.bpm || 120)
  const open = note.end - note.start >= secondsPerBeat * 1.9

  const pieces: string[] = []
  const endX = x0 + (Math.min(note.end, system.t1) - system.t0) * pxPerSecond
  const barFrom = held ? cx : cx + rx * 0.8
  if (endX > barFrom + 1) {
    pieces.push(
      `<rect x="${barFrom.toFixed(1)}" y="${(cy - 1.1).toFixed(1)}" width="${(endX - barFrom).toFixed(1)}" height="2.2" rx="1.1" fill="${palette.ink}" opacity="0.22"/>`,
    )
  }

  // Carried over from the previous system: the sustain bar alone. Drawing a
  // second notehead here would tell the reader the note is struck again, and
  // would put a head on the staff that the cursor never arrives at.
  if (held) {
    return `<g class="note held" data-s="${note.start.toFixed(4)}" data-e="${note.end.toFixed(4)}">${pieces.join('')}</g>`
  }

  // Ledger lines, every other step beyond the staff.
  for (let s = bottom + 10; s <= step; s += 2) {
    const ly = (bottomLine - (s - bottom) * (SP / 2)).toFixed(1)
    pieces.push(
      `<line x1="${(cx - rx * 1.7).toFixed(1)}" y1="${ly}" x2="${(cx + rx * 1.7).toFixed(1)}" y2="${ly}" stroke="${palette.rule}" stroke-width="1"/>`,
    )
  }
  for (let s = bottom - 2; s >= step; s -= 2) {
    const ly = (bottomLine - (s - bottom) * (SP / 2)).toFixed(1)
    pieces.push(
      `<line x1="${(cx - rx * 1.7).toFixed(1)}" y1="${ly}" x2="${(cx + rx * 1.7).toFixed(1)}" y2="${ly}" stroke="${palette.rule}" stroke-width="1"/>`,
    )
  }

  if (alter !== 0 && !held) {
    pieces.push(
      `<text x="${(cx - rx - 3).toFixed(1)}" y="${(cy + 4).toFixed(1)}" text-anchor="end" fill="${palette.ink}" font-size="13">${alter > 0 ? '♯' : '♭'}</text>`,
    )
  }

  const up = step < MIDDLE_STEP[clef]
  const stemX = (cx + (up ? rx * 0.92 : -rx * 0.92)).toFixed(1)
  const stemY = (cy + (up ? -SP * 3.3 : SP * 3.3)).toFixed(1)
  pieces.push(
    `<line x1="${stemX}" y1="${cy.toFixed(1)}" x2="${stemX}" y2="${stemY}" stroke="${palette.ink}" stroke-width="1.3"/>`,
  )
  pieces.push(
    `<ellipse cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" rx="${rx}" ry="${ry}" transform="rotate(-20 ${cx.toFixed(1)} ${cy.toFixed(1)})" fill="${open ? 'none' : palette.ink}" stroke="${palette.ink}" stroke-width="${open ? 1.5 : 0.8}"/>`,
  )

  return `<g class="note" data-s="${note.start.toFixed(4)}" data-e="${note.end.toFixed(4)}">${pieces.join('')}</g>`
}

function clefGlyph(clef: 'treble' | 'bass', x: number, topLine: number): string {
  // The Unicode clefs sit on the baseline; these offsets put the treble's
  // curl on the G line and the bass's dots either side of the F line.
  const y = clef === 'treble' ? topLine + SP * 6.6 : topLine + SP * 3.4
  const size = clef === 'treble' ? SP * 7.2 : SP * 4.8
  return `<text x="${x}" y="${y.toFixed(1)}" font-size="${size.toFixed(1)}" fill="currentColor" font-family="serif">${clef === 'treble' ? '\u{1D11E}' : '\u{1D122}'}</text>`
}

/** Seconds as m:ss, the way every transport shows them. */
export function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Where the cursor goes at time `t`: which system, and x within it. */
export function cursorAt(view: Layout, t: number): { x: number; top: number; bottom: number } | null {
  const system = view.systems.find((s) => t >= s.t0 && t < s.t1) ?? (t >= (view.systems.at(-1)?.t1 ?? 0) ? view.systems.at(-1) : view.systems[0])
  if (!system) return null
  const clamped = Math.min(Math.max(t, system.t0), system.t1)
  return {
    x: view.x0 + (clamped - system.t0) * view.pxPerSecond,
    top: system.top + 10,
    bottom: system.bottom - 4,
  }
}

/** The time a click at (x, y) over the score means. */
export function timeAt(view: Layout, x: number, y: number): number | null {
  const system = view.systems.find((s) => y >= s.top && y < s.bottom)
  if (!system) return null
  const t = system.t0 + (x - view.x0) / view.pxPerSecond
  return Math.min(Math.max(t, system.t0), system.t1)
}
