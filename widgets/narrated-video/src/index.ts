/**
 * A narrated animation: the video, its captions read out beneath the picture,
 * and the sections it can be stepped through.
 *
 * Captions are pulled from the sibling `.vtt` through the host bridge rather
 * than loaded as a <track src>: the frame has an opaque origin and no network,
 * so every file it wants comes back through `ctx.fetchAsset`. Rendering them
 * ourselves is also why the text sits below the picture instead of painted over
 * the bottom of it, where it covers the thing being explained.
 */

export type Ctx = {
  path: string
  /** Unused for a video: the bytes come from the URL the host supplies. */
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  /** A blob URL for a binary sibling, fetched by the host on our behalf. */
  fetchAssetUrl: (path: string) => Promise<string>
}

type Cue = { start: number; end: number; text: string }

/** Parse WebVTT far enough to read cues out loud. Not a general parser. */
export function parseVtt(source: string): Cue[] {
  const cues: Cue[] = []
  const seconds = (stamp: string) => {
    const parts = stamp.trim().split(':').map(Number)
    if (parts.some(Number.isNaN)) return NaN
    return parts.length === 3
      ? parts[0] * 3600 + parts[1] * 60 + parts[2]
      : parts[0] * 60 + parts[1]
  }
  for (const block of source.replace(/\r/g, '').split('\n\n')) {
    const lines = block.split('\n').filter(Boolean)
    const timing = lines.find((l) => l.includes('-->'))
    if (!timing) continue
    const [from, to] = timing.split('-->')
    const start = seconds(from)
    const end = seconds(to)
    if (Number.isNaN(start) || Number.isNaN(end)) continue
    const text = lines.slice(lines.indexOf(timing) + 1).join(' ').trim()
    if (text) cues.push({ start, end, text })
  }
  return cues
}

export async function mount(el: HTMLElement, ctx: Ctx): Promise<() => void> {
  const dark = ctx.theme !== 'light'
  const name = ctx.path.split('/').pop() ?? 'video'
  const wrap = document.createElement('div')
  wrap.style.cssText = 'display:flex;flex-direction:column;gap:10px'

  const video = document.createElement('video')
  video.controls = true
  video.playsInline = true
  video.preload = 'metadata'
  // Not a '/api/...' src: this frame's origin is opaque, so such a request
  // carries no session and 401s once sign-in is on. The host fetches the bytes
  // and we wrap them in a blob URL here.
  const objectUrl = await ctx.fetchAssetUrl(ctx.path.split('/').pop() ?? '')
  video.src = objectUrl
  video.style.cssText = `width:100%;border-radius:10px;background:${dark ? '#0e1117' : '#f2f4f8'}`

  const line = document.createElement('p')
  line.style.cssText =
    'margin:0;min-height:2.6em;font-size:15px;line-height:1.45;opacity:.92'

  const sections = document.createElement('div')
  sections.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px'

  wrap.append(video, line, sections)
  el.replaceChildren(wrap)

  // Captions: the sibling .vtt, if there is one. Absent is normal -- plenty of
  // animations are not narrated -- so a miss is silent rather than an error.
  let cues: Cue[] = []
  const vttName = name.replace(/\.(mp4|webm|mov)$/i, '.vtt')
  try {
    cues = parseVtt(await ctx.fetchAsset(vttName))
  } catch {
    line.textContent = ''
  }

  const showCueAt = (t: number) => {
    const cue = cues.find((c) => t >= c.start && t <= c.end)
    line.textContent = cue?.text ?? ''
  }
  const onTime = () => showCueAt(video.currentTime)
  video.addEventListener('timeupdate', onTime)

  // Sections come from the cue boundaries when the file carries no chapter
  // list of its own: each cue is a step the reader may want to replay.
  if (cues.length > 0) {
    const label = document.createElement('span')
    label.style.cssText = 'font-size:12px;opacity:.6;width:100%'
    label.textContent = `${cues.length} section${cues.length === 1 ? '' : 's'} — click to replay`
    sections.appendChild(label)
    cues.forEach((cue, index) => {
      const button = document.createElement('button')
      button.textContent = String(index + 1)
      button.title = cue.text
      button.style.cssText = `min-width:30px;min-height:28px;border-radius:8px;cursor:pointer;
        border:1px solid ${dark ? '#2a2f3a' : '#c9ced8'};
        background:${dark ? '#161a21' : '#fff'};color:inherit;font:inherit;font-size:12px`
      button.addEventListener('click', () => {
        video.currentTime = cue.start + 0.01
        void video.play()
      })
      sections.appendChild(button)
    })
  }

  return () => {
    video.removeEventListener('timeupdate', onTime)
    video.pause()
    video.removeAttribute('src')
    video.load()
    URL.revokeObjectURL(objectUrl)
  }
}
