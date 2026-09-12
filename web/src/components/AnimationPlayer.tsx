import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { animationUrl, getAnimationInfo } from '../api'
import type { AnimationInfo } from '../types'

/**
 * A GIF with playback controls.
 *
 * A GIF has no seek and plays at its baked-in frame delays, so both speed and
 * stepping are server-side derivations: the URL changes and the image reloads.
 */
export function AnimationPlayer({ path, compact = false }: { path: string; compact?: boolean }) {
  const [info, setInfo] = useState<AnimationInfo | null>(null)
  const [speed, setSpeed] = useState<number | null>(null)
  const [segment, setSegment] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  /** Controls live behind a button so the animation itself gets the space. */
  const [options, setOptions] = useState(false)
  const [full, setFull] = useState(false)
  /** The URL currently painted, which lags `src` while the next one loads. */
  const [shown, setShown] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let live = true
    setSegment(null)
    getAnimationInfo(path)
      .then((got) => {
        if (!live) return
        setInfo(got)
        setSpeed((current) => current ?? got.defaultSpeed)
      })
      .catch(() => {})
    return () => { live = false }
  }, [path])


  useEffect(() => {
    if (!full) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [full])

  const step = (delta: number) => {
    if (checkpoints.length === 0) return
    const next = (segment === null ? (delta > 0 ? 0 : checkpoints.length - 1) : segment + delta)
    setSegment(Math.max(0, Math.min(checkpoints.length - 1, next)))
    setNonce((n) => n + 1)
  }

  const checkpoints = info?.checkpoints ?? []
  const at = segment === null ? null : checkpoints.find((c) => c.index === segment) ?? null
  const src = `${animationUrl(path, speed ?? undefined, segment ?? undefined)}&n=${nonce}`

  // Slicing a checkpoint out of a large animation takes a few seconds the first
  // time. Swapping `src` directly would blank the image for that whole time, so
  // the previous frame stays up until the next one has actually decoded.
  useEffect(() => {
    let live = true
    setLoading(true)
    const image = new Image()
    const settle = () => {
      if (!live) return
      setShown(src)
      setLoading(false)
    }
    image.onload = settle
    image.onerror = settle
    image.src = src
    return () => { live = false }
  }, [src])

  // Warm the first checkpoint as soon as the animation is open. Slicing a large
  // GIF takes several seconds the first time, and without this that cost lands
  // on the first press of the step control.
  useEffect(() => {
    if (checkpoints.length === 0 || segment !== null) return
    const warm = window.setTimeout(() => {
      new Image().src = animationUrl(path, speed ?? undefined, 0)
    }, 800)
    return () => window.clearTimeout(warm)
  }, [checkpoints.length, path, speed, segment])

  // Warm the neighbouring checkpoints so stepping is instant after the first.
  useEffect(() => {
    if (segment === null || checkpoints.length === 0) return
    const warm = window.setTimeout(() => {
      for (const step of [segment - 1, segment + 1]) {
        if (step < 0 || step >= checkpoints.length) continue
        new Image().src = animationUrl(path, speed ?? undefined, step)
      }
    }, 400)
    return () => window.clearTimeout(warm)
  }, [segment, speed, path, checkpoints.length])

  const overlay = full && shown && createPortal(
    <div className="player-full" onClick={() => setFull(false)}>
      <img src={shown} alt={path} onClick={(e) => e.stopPropagation()} />
      <button className="player-options player-full-close" onClick={() => setFull(false)} aria-label="Close full screen">
        <span aria-hidden="true">✕</span>
      </button>
    </div>,
    document.body,
  )

  return (
    <div className={`player ${loading ? 'loading' : ''}`}>
      {overlay}
      <div className="player-frame">
        {shown ? <img src={shown} alt={path} /> : <div className="player-empty" />}
        {loading && <span className="player-spinner" aria-label="Loading" />}

        <div className="player-buttons">
          <button
            className="player-options"
            onClick={() => setFull(true)}
            title="Full screen"
            aria-label="Full screen"
          >
            <span aria-hidden="true">⤢</span>
          </button>
        </div>

        {/* Bottom right, out of the way of the picture. The label lives in
            `title`/`aria-label` so the button is just the icon. */}
        <button
          className={`player-options player-options-corner ${options ? 'on' : ''}`}
          onClick={() => setOptions((v) => !v)}
          aria-expanded={options}
          title="Playback options"
          aria-label="Playback options"
        >
          <span aria-hidden="true">⚙</span>
        </button>

      <div className={`player-controls ${options ? 'shown' : ''}`} hidden={!options}>
        <div className="segmented">
          {(info?.speeds ?? [0.5, 1, 2]).map((s) => (
            <button
              key={s}
              className={speed === s ? 'on' : ''}
              onClick={() => {
                setSpeed(s)
                setNonce((n) => n + 1)
              }}
            >
              {s}×
            </button>
          ))}
        </div>

        <button className="ghost-btn small" onClick={() => setNonce((n) => n + 1)}>
          Replay
        </button>

        {checkpoints.length > 0 && (
          <div className="steps">
            <button
              className="ghost-btn small"
              onClick={() => step(-1)}
              disabled={segment === 0}
              aria-label="Previous step"
            >
              ‹
            </button>
            <span className="step-label" title={at?.name}>
              {at ? `${at.index + 1}/${checkpoints.length} · ${at.name}` : 'whole animation'}
            </span>
            <button
              className="ghost-btn small"
              onClick={() => step(1)}
              disabled={segment !== null && segment >= checkpoints.length - 1}
              aria-label="Next step"
            >
              ›
            </button>
            {segment !== null && (
              <button className="ghost-btn small" onClick={() => { setSegment(null); setNonce((n) => n + 1) }}>
                All
              </button>
            )}
          </div>
        )}
      </div>
      </div>

      {!compact && info?.animation && (
        <p className="muted small">
          {info.animation.frames} frames · {info.animation.duration}s at 1×
          {checkpoints.length > 0 &&
            ` · ${checkpoints.length} ${info.named ? 'named steps' : 'slices'}`}
        </p>
      )}
    </div>
  )
}
