/**
 * A Standard MIDI File parser, small enough to read in one sitting.
 *
 * The widget parses the `.mid` itself rather than trusting a note list written
 * beside it: a sidecar copy of the notes is a second source of truth, and the
 * first time someone regenerates the MIDI without regenerating the copy the
 * score shows a piece that is no longer playing. Here the bytes are the score.
 *
 * Nothing in this file touches the DOM, so it can be exercised from node.
 */

export type RawNote = {
  /** Ticks from the start of the file. */
  tick: number
  durTicks: number
  pitch: number
  velocity: number
  channel: number
}

export type Track = {
  name: string
  notes: RawNote[]
}

export type Tempo = { tick: number; usPerQuarter: number }
export type TimeSig = { tick: number; numerator: number; denominator: number }

export type Smf = {
  format: number
  /** Ticks per quarter note. */
  division: number
  tracks: Track[]
  tempos: Tempo[]
  timeSigs: TimeSig[]
  /** Sharps (positive) or flats (negative) in the key signature. */
  keySf: number
  /** Last tick anything happens on. */
  endTick: number
}

class Reader {
  offset = 0
  data: Uint8Array

  constructor(data: Uint8Array) {
    this.data = data
  }

  byte(): number {
    if (this.offset >= this.data.length) throw new Error('unexpected end of MIDI data')
    return this.data[this.offset++]
  }

  bytes(count: number): Uint8Array {
    const slice = this.data.subarray(this.offset, this.offset + count)
    if (slice.length < count) throw new Error('unexpected end of MIDI data')
    this.offset += count
    return slice
  }

  uint32(): number {
    const b = this.bytes(4)
    return ((b[0] << 24) >>> 0) + (b[1] << 16) + (b[2] << 8) + b[3]
  }

  uint16(): number {
    const b = this.bytes(2)
    return (b[0] << 8) + b[1]
  }

  /** The variable-length quantity MIDI uses for every delta time. */
  varint(): number {
    let value = 0
    for (let i = 0; i < 4; i++) {
      const b = this.byte()
      value = (value << 7) | (b & 0x7f)
      if ((b & 0x80) === 0) return value
    }
    throw new Error('malformed variable-length quantity')
  }

  ascii(count: number): string {
    return String.fromCharCode(...this.bytes(count))
  }
}

/** Parse a Standard MIDI File (format 0, 1 or 2) into notes and a tempo map. */
export function parseSmf(data: Uint8Array): Smf {
  const reader = new Reader(data)
  if (reader.ascii(4) !== 'MThd') throw new Error('not a MIDI file: no MThd header')
  const headerLength = reader.uint32()
  const format = reader.uint16()
  const trackCount = reader.uint16()
  const division = reader.uint16()
  // Anything the header carries beyond the six documented bytes is skipped
  // rather than assumed absent.
  reader.offset += Math.max(0, headerLength - 6)
  if (division & 0x8000) {
    throw new Error('SMPTE-timecode MIDI is not supported; use ticks per quarter note')
  }
  if (division === 0) throw new Error('MIDI header declares zero ticks per quarter note')

  const tracks: Track[] = []
  const tempos: Tempo[] = []
  const timeSigs: TimeSig[] = []
  let keySf = 0
  let endTick = 0

  for (let index = 0; index < trackCount && reader.offset < data.length; index++) {
    // Some writers pad between chunks; skip anything that is not a track.
    const id = reader.ascii(4)
    const length = reader.uint32()
    if (id !== 'MTrk') {
      reader.offset += length
      index--
      continue
    }
    const end = reader.offset + length
    const track: Track = { name: '', notes: [] }
    // One list per (channel, pitch): a note-on for a pitch already sounding is
    // a legal re-articulation, and the matching note-off closes the oldest.
    const sounding = new Map<number, RawNote[]>()
    let tick = 0
    let status = 0

    while (reader.offset < end) {
      tick += reader.varint()
      let head = reader.byte()
      if (head < 0x80) {
        // Running status: the event reuses the previous status byte.
        if (status === 0) throw new Error('running status with no preceding status byte')
        reader.offset--
        head = status
      } else if (head < 0xf0) {
        status = head
      }

      if (head === 0xff) {
        const type = reader.byte()
        const payload = reader.bytes(reader.varint())
        if (type === 0x51 && payload.length === 3) {
          tempos.push({ tick, usPerQuarter: (payload[0] << 16) + (payload[1] << 8) + payload[2] })
        } else if (type === 0x58 && payload.length >= 2) {
          timeSigs.push({ tick, numerator: payload[0], denominator: 2 ** payload[1] })
        } else if (type === 0x59 && payload.length >= 1) {
          keySf = payload[0] > 127 ? payload[0] - 256 : payload[0]
        } else if ((type === 0x03 || type === 0x04) && !track.name) {
          track.name = String.fromCharCode(...payload).trim()
        }
        continue
      }
      if (head === 0xf0 || head === 0xf7) {
        reader.offset += reader.varint()
        continue
      }

      const kind = head & 0xf0
      const channel = head & 0x0f
      if (kind === 0x90 || kind === 0x80) {
        const pitch = reader.byte()
        const velocity = reader.byte()
        const key = channel * 128 + pitch
        // A note-on with velocity 0 is the usual way to end a note.
        if (kind === 0x90 && velocity > 0) {
          const note: RawNote = { tick, durTicks: 0, pitch, velocity, channel }
          const open = sounding.get(key)
          if (open) open.push(note)
          else sounding.set(key, [note])
          track.notes.push(note)
        } else {
          const open = sounding.get(key)
          const note = open?.shift()
          if (note) note.durTicks = Math.max(1, tick - note.tick)
          if (open && open.length === 0) sounding.delete(key)
        }
      } else if (kind === 0xc0 || kind === 0xd0) {
        reader.byte()
      } else {
        reader.byte()
        reader.byte()
      }
    }

    // A note still sounding at the end of its track runs to the end of it.
    for (const open of sounding.values()) {
      for (const note of open) note.durTicks = Math.max(1, tick - note.tick)
    }
    reader.offset = end
    track.notes.sort((a, b) => a.tick - b.tick || a.pitch - b.pitch)
    endTick = Math.max(endTick, tick)
    tracks.push(track)
  }

  tempos.sort((a, b) => a.tick - b.tick)
  timeSigs.sort((a, b) => a.tick - b.tick)
  return { format, division, tracks, tempos, timeSigs, keySf, endTick }
}

/**
 * A tick-to-seconds function built from the file's own tempo map.
 *
 * This is the whole reason the cursor cannot drift: every position it draws
 * comes through here, from the tempo changes the file actually carries, rather
 * than from an assumed 120 bpm or a measured average.
 */
export function secondsAt(smf: Smf): (tick: number) => number {
  const points: { tick: number; seconds: number; secondsPerTick: number }[] = []
  const first = smf.tempos.length > 0 && smf.tempos[0].tick === 0 ? smf.tempos[0].usPerQuarter : 500000
  let seconds = 0
  let secondsPerTick = first / 1e6 / smf.division
  let tick = 0
  points.push({ tick: 0, seconds: 0, secondsPerTick })
  for (const tempo of smf.tempos) {
    if (tempo.tick <= 0) {
      secondsPerTick = tempo.usPerQuarter / 1e6 / smf.division
      points[0].secondsPerTick = secondsPerTick
      continue
    }
    seconds += (tempo.tick - tick) * secondsPerTick
    tick = tempo.tick
    secondsPerTick = tempo.usPerQuarter / 1e6 / smf.division
    points.push({ tick, seconds, secondsPerTick })
  }
  return (at: number) => {
    let low = 0
    let high = points.length - 1
    while (low < high) {
      const mid = (low + high + 1) >> 1
      if (points[mid].tick <= at) low = mid
      else high = mid - 1
    }
    const point = points[low]
    return point.seconds + (at - point.tick) * point.secondsPerTick
  }
}

/** Beats per minute in force at a tick, for the tempo readout and note shapes. */
export function bpmAt(smf: Smf, tick: number): number {
  let usPerQuarter = 500000
  for (const tempo of smf.tempos) {
    if (tempo.tick > tick) break
    usPerQuarter = tempo.usPerQuarter
  }
  return 6e7 / usPerQuarter
}
