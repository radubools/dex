/**
 * A composition: its audio, its MIDI drawn as a score, and a cursor that
 * tracks playback.
 *
 * The widget is opened on a small text sidecar (`*.score.json`) rather than on
 * the `.mid` itself, because dex only hands a widget files it can read as text
 * -- a `.mid` or a `.wav` is refused before the frame is ever built. The
 * sidecar names the two binaries; both come back through `ctx.fetchAssetUrl`,
 * and the MIDI is parsed here, from the same bytes that were synthesised.
 *
 * So there is one clock. The noteheads are placed by the MIDI's tempo map and
 * the cursor is placed by `audio.currentTime` through the identical mapping;
 * nothing is estimated, and if the audio and the MIDI ever disagree in length
 * the widget says so rather than quietly stretching one onto the other.
 */
import { parseSmf } from './smf.ts'
import { buildScore, clock, cursorAt, layout, PALETTES, timeAt, type Layout, type Score } from './score.ts'

export type Ctx = {
  path: string
  /** The sidecar's JSON text. */
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  fetchAssetUrl: (path: string) => Promise<string>
}

type Sidecar = {
  title?: string
  midi?: string
  audio?: string
}

const AUDIO_EXTENSIONS = ['.wav', '.mp3', '.ogg', '.flac', '.m4a']

/** Bytes of a sibling file: the host fetches it, we read the blob it made. */
async function fetchBytes(ctx: Ctx, name: string): Promise<Uint8Array> {
  const url = await ctx.fetchAssetUrl(name)
  try {
    return new Uint8Array(await (await fetch(url)).arrayBuffer())
  } finally {
    URL.revokeObjectURL(url)
  }
}

export async function mount(el: HTMLElement, ctx: Ctx): Promise<() => void> {
  const dark = ctx.theme !== 'light'
  const palette = PALETTES[dark ? 'dark' : 'light']
  const file = ctx.path.split('/').pop() ?? ''
  const stem = file.replace(/\.score\.json$/i, '').replace(/\.json$/i, '')

  let sidecar: Sidecar = {}
  try {
    sidecar = JSON.parse(ctx.text) as Sidecar
  } catch {
    throw new Error(`${file} is not valid JSON`)
  }

  const midiName = sidecar.midi ?? `${stem}.mid`
  const smf = parseSmf(await fetchBytes(ctx, midiName))
  const score = buildScore(smf, sidecar.title ?? stem.replace(/[-_]+/g, ' '))
  if (score.staves.length === 0) throw new Error(`${midiName} contains no notes`)

  // The audio, by the name the sidecar gives or by trying the usual endings.
  // A composition with no rendered audio still draws: the score is worth
  // seeing, and the transport simply has nothing to play.
  let audioUrl: string | null = null
  let audioName = ''
  for (const candidate of sidecar.audio ? [sidecar.audio] : AUDIO_EXTENSIONS.map((ext) => stem + ext)) {
    try {
      audioUrl = await ctx.fetchAssetUrl(candidate)
      audioName = candidate
      break
    } catch {
      /* try the next ending */
    }
  }

  // ---------------------------------------------------------------- chrome
  const wrap = document.createElement('div')
  wrap.style.cssText = 'display:flex;flex-direction:column;gap:10px'

  const bar = document.createElement('div')
  bar.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap'

  const play = document.createElement('button')
  play.type = 'button'
  play.textContent = '▶'
  play.setAttribute('aria-label', 'Play')
  play.style.cssText = `width:34px;height:34px;border-radius:999px;cursor:pointer;font:inherit;font-size:13px;
    border:1px solid ${palette.rule};background:${dark ? '#161a21' : '#fff'};color:inherit`
  play.disabled = audioUrl === null

  const seek = document.createElement('div')
  seek.style.cssText = `position:relative;flex:1;min-width:140px;height:6px;border-radius:999px;cursor:pointer;
    background:${dark ? '#232833' : '#e4e8ef'}`
  const seekFill = document.createElement('div')
  seekFill.style.cssText = `position:absolute;inset:0 100% 0 0;border-radius:999px;background:${palette.accent}`
  seek.appendChild(seekFill)

  const readout = document.createElement('span')
  readout.style.cssText = 'font:12px ui-monospace,monospace;opacity:.75;min-width:84px;text-align:right'

  const title = document.createElement('div')
  title.style.cssText = 'display:flex;gap:10px;align-items:baseline;flex-wrap:wrap'
  const titleText = document.createElement('strong')
  titleText.style.cssText = 'font-size:14px'
  titleText.textContent = score.title
  const meta = document.createElement('span')
  meta.style.cssText = 'font-size:12px;opacity:.6'
  meta.textContent = `${Math.round(score.bpm)} bpm · ${score.staves.length} stave${score.staves.length === 1 ? '' : 's'} · ${clock(score.duration)}`
  const warn = document.createElement('span')
  warn.style.cssText = 'font-size:12px;color:#e0a33a'
  title.append(titleText, meta, warn)

  bar.append(play, seek, readout)

  const stage = document.createElement('div')
  stage.style.cssText = `max-height:460px;overflow:auto;border-radius:10px;color:${palette.ink};
    background:${dark ? '#10141b' : '#fff'};border:1px solid ${palette.rule}`
  const hint = document.createElement('div')
  hint.style.cssText = 'font-size:11px;opacity:.5'
  hint.textContent = audioUrl
    ? `click the score to seek · ${audioName}`
    : 'no rendered audio beside this file — score only'

  wrap.append(title, bar, stage, hint)
  el.replaceChildren(wrap)

  // ----------------------------------------------------------------- score
  let view: Layout = layout(score, 900, palette)
  let cursor: SVGLineElement | null = null
  let notes: { el: SVGGElement; start: number; end: number }[] = []
  let sounding = new Set<SVGGElement>()

  const draw = () => {
    const width = Math.max(360, Math.floor(stage.clientWidth || el.clientWidth || 900) - 2)
    view = layout(score, width, palette)
    stage.innerHTML = view.svg
    cursor = stage.querySelector('.cursor')
    notes = [...stage.querySelectorAll<SVGGElement>('g.note')].map((node) => ({
      el: node,
      start: Number(node.dataset.s),
      end: Number(node.dataset.e),
    }))
    sounding = new Set()
    place(at())
  }

  const at = () => (audio ? audio.currentTime : 0)

  let lastSystemTop = -1
  const place = (t: number) => {
    const spot = cursorAt(view, t)
    if (!cursor || !spot) return
    cursor.setAttribute('x1', spot.x.toFixed(1))
    cursor.setAttribute('x2', spot.x.toFixed(1))
    cursor.setAttribute('y1', spot.top.toFixed(1))
    cursor.setAttribute('y2', spot.bottom.toFixed(1))

    // Colour the notes that are sounding right now. Only the ones that
    // changed are touched, so this stays cheap at 60 frames a second.
    for (const note of notes) {
      const on = t >= note.start && t < note.end
      if (on === sounding.has(note.el)) continue
      if (on) {
        sounding.add(note.el)
        note.el.setAttribute('fill', palette.sounding)
        note.el.style.color = palette.sounding
        for (const child of note.el.children) {
          if (child.getAttribute('fill') && child.getAttribute('fill') !== 'none') child.setAttribute('fill', palette.sounding)
          if (child.getAttribute('stroke')) child.setAttribute('stroke', palette.sounding)
        }
      } else {
        sounding.delete(note.el)
        note.el.removeAttribute('fill')
        for (const child of note.el.children) {
          if (child.getAttribute('fill') && child.getAttribute('fill') !== 'none') child.setAttribute('fill', palette.ink)
          if (child.getAttribute('stroke')) child.setAttribute('stroke', child.tagName === 'line' && child.getAttribute('stroke-width') === '1' ? palette.rule : palette.ink)
        }
      }
    }

    // Follow the music down the page, but only when the line changes -- a
    // scroll every frame fights the reader's own scrolling.
    if (spot.top !== lastSystemTop) {
      lastSystemTop = spot.top
      if (spot.bottom > stage.scrollTop + stage.clientHeight || spot.top < stage.scrollTop) {
        stage.scrollTop = Math.max(0, spot.top - 12)
      }
    }

    const total = duration()
    readout.textContent = `${clock(t)} / ${clock(total)}`
    seekFill.style.right = `${(100 - (total > 0 ? Math.min(t / total, 1) * 100 : 0)).toFixed(2)}%`
  }

  // ------------------------------------------------------------- transport
  const audio = audioUrl ? new Audio(audioUrl) : null
  if (audio) audio.preload = 'metadata'
  const duration = () =>
    audio && Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : score.duration

  let frame = 0
  const tick = () => {
    place(at())
    frame = requestAnimationFrame(tick)
  }
  const start = () => {
    if (!frame) frame = requestAnimationFrame(tick)
  }
  const stop = () => {
    cancelAnimationFrame(frame)
    frame = 0
  }

  const onPlay = () => {
    play.textContent = '❚❚'
    play.setAttribute('aria-label', 'Pause')
    start()
  }
  const onPause = () => {
    play.textContent = '▶'
    play.setAttribute('aria-label', 'Play')
    stop()
    place(at())
  }
  const onLoaded = () => {
    // The audio is the piece's real length. Audio that stops before the score
    // does is the failure worth shouting about -- the cursor would run on over
    // silence -- so that is flagged tightly. A render that rings on past the
    // last note is just a release tail, and only a wild overshoot is worth a
    // word. Neither case scales one clock onto the other: that would hide the
    // drift instead of showing it.
    const midi = score.duration
    const real = audio ? audio.duration : midi
    if (Number.isFinite(real) && real < midi - 0.35) {
      warn.textContent = `audio ends at ${clock(real)}, score runs to ${clock(midi)} — the render is short`
    } else if (Number.isFinite(real) && real > midi + Math.max(3, midi * 0.15)) {
      warn.textContent = `audio ${clock(real)} vs MIDI ${clock(midi)} — they may have drifted apart`
    }
    place(at())
  }

  play.addEventListener('click', () => {
    if (!audio) return
    if (audio.paused) void audio.play()
    else audio.pause()
  })
  audio?.addEventListener('play', onPlay)
  audio?.addEventListener('pause', onPause)
  audio?.addEventListener('ended', onPause)
  audio?.addEventListener('seeked', () => place(at()))
  // The element's own report of where it is. A seek completes asynchronously,
  // and while paused there is no animation frame to notice that it landed
  // somewhere other than where it was asked to go.
  audio?.addEventListener('timeupdate', () => {
    if (audio.paused) place(at())
  })
  audio?.addEventListener('loadedmetadata', onLoaded)

  const seekTo = (t: number) => {
    if (!audio) return
    audio.currentTime = Math.min(Math.max(t, 0), Math.max(duration() - 0.01, 0))
    place(audio.currentTime)
  }
  seek.addEventListener('click', (event) => {
    const box = seek.getBoundingClientRect()
    seekTo(((event.clientX - box.left) / box.width) * duration())
  })
  stage.addEventListener('click', (event) => {
    const svg = stage.querySelector('svg')
    if (!svg) return
    const box = svg.getBoundingClientRect()
    const t = timeAt(view, event.clientX - box.left, event.clientY - box.top)
    if (t !== null) seekTo(t)
  })

  draw()

  // Re-lay out on a width change: the number of systems depends on it.
  let width = 0
  const observer = new ResizeObserver(() => {
    const next = Math.floor(stage.clientWidth)
    if (Math.abs(next - width) < 8) return
    width = next
    lastSystemTop = -1
    draw()
  })
  observer.observe(stage)

  return () => {
    stop()
    observer.disconnect()
    audio?.pause()
    if (audioUrl) URL.revokeObjectURL(audioUrl)
  }
}
