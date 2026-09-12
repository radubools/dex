import { useEffect, useMemo, useRef, useState } from 'react'
import { animationUrl, assetUrl, getAnimationInfo } from '../api'
import type { AnimationCheckpoint, AnimationInfo } from '../types'

/**
 * A narrated animation: the video, its sections, and a loop over one of them.
 *
 * Unlike the GIF player this needs no server-side re-encoding — a video seeks,
 * so a section is just a time range to jump to and, optionally, stay inside.
 */
export function VideoPlayer({
  path,
  autoPlay = false,
  active = false,
  preload = 'metadata',
  onEnded,
}: {
  path: string
  /**
   * Start playing as soon as it can. The feed needs this: a reel is a passive
   * carousel that advances on the animation's own length, so a video waiting
   * for a click means the reel counts down against a still frame and moves on.
   * Off by default — a viewer opened deliberately should not start talking,
   * and the feed mounts every reel at once.
   */
  autoPlay?: boolean
  /**
   * Whether this is the one being shown. Distinct from `autoPlay`: becoming
   * the current video restarts it from the beginning, whereas the feed being
   * un-paused resumes where it stopped. Collapsing the two made resume jump
   * back to zero.
   */
  active?: boolean
  /**
   * What the browser may fetch before anyone presses play. A single viewer
   * wants `metadata` — the first frame and the duration, so it looks like a
   * video rather than a black box. A list does not: the feed mounts a hundred
   * and thirty-four of these at once, and metadata for all of them saturates
   * the six connections a browser gives one host, leaving the one being
   * watched queued behind the hundred nobody is looking at.
   */
  preload?: 'none' | 'metadata' | 'auto'
  /** Fired when the clip reaches its end, so a playlist can move on. */
  onEnded?: () => void
}) {
  const video = useRef<HTMLVideoElement>(null)
  const [info, setInfo] = useState<AnimationInfo | null>(null)
  const [section, setSection] = useState<AnimationCheckpoint | null>(null)
  const [looping, setLooping] = useState(true)
  //: The section the playhead is in right now, highlighted as it plays.
  const [playingIndex, setPlayingIndex] = useState<number | null>(null)
  //: The list is long — nine or ten for a narrated animation — and it is
  //: reference, not the main thing on screen. Closed until asked for.
  const [showSections, setShowSections] = useState(false)
  const [options, setOptions] = useState(false)
  const [full, setFull] = useState(false)
  const [rate, setRate] = useState(1)
  const [caption, setCaption] = useState('')

  useEffect(() => {
    let live = true
    setSection(null)
    getAnimationInfo(path)
      .then((got) => live && setInfo(got))
      .catch(() => {})
    return () => { live = false }
  }, [path])

  useEffect(() => {
    const element = video.current
    if (element) element.playbackRate = rate
  }, [rate, info])

  // The browser paints captions over the bottom of the picture, which is where
  // a manim scene puts its own caption line — one hides the other. Take the
  // cues from the track but render them below the video instead.
  useEffect(() => {
    const element = video.current
    if (!element) return
    const track = element.textTracks[0]
    if (!track) return
    track.mode = 'hidden'
    const onChange = () => {
      const cue = track.activeCues?.[0] as VTTCue | undefined
      setCaption(cue ? cue.text : '')
    }
    track.addEventListener('cuechange', onChange)
    return () => track.removeEventListener('cuechange', onChange)
  }, [path, info])

  // Staying inside the chosen section is a matter of watching the clock: there
  // is no native "loop this range".
  useEffect(() => {
    const element = video.current
    if (!element || !section) return
    const onTime = () => {
      const end = section.end ?? 0
      if (element.currentTime >= end - 0.03) {
        if (looping) {
          element.currentTime = section.start ?? 0
          void element.play()
        } else {
          element.pause()
        }
      }
    }
    element.addEventListener('timeupdate', onTime)
    return () => element.removeEventListener('timeupdate', onTime)
  }, [section, looping])

  const play = (mark: AnimationCheckpoint | null) => {
    const element = video.current
    setSection(mark)
    if (!element) return
    element.currentTime = mark?.start ?? 0
    void element.play()
  }

  // Autoplay as an effect rather than the attribute: the attribute fires once
  // at mount, before the source is necessarily ready, and says nothing about
  // what to do when a reel stops being the active one.
  const wasActive = useRef(active)
  useEffect(() => {
    const element = video.current
    if (!element) return
    // Arriving on this video rewinds it; resuming after a pause does not. The
    // caller can therefore keep this component mounted, which is what lets a
    // prefetched video still be buffered when its turn comes.
    const arrived = active && !wasActive.current
    wasActive.current = active
    if (arrived) element.currentTime = 0
    if (!autoPlay) {
      element.pause()
      return
    }
    // Rejected when the browser has no user activation to spend. Left paused
    // rather than muted and forced through: these carry narration, and a
    // silent play is worse than an obvious one waiting to be started.
    void element.play().catch(() => {})
  }, [autoPlay, active, path])

  // Memoised: a fresh array each render would make the effect below rebind
  // its listener on every frame of playback.
  const checkpoints = useMemo(() => info?.checkpoints ?? [], [info])

  // Which section the playhead is inside, which is not the same as the one
  // chosen to loop: a video left to run passes through all of them without
  // any being selected. Updated from `timeupdate`, so it follows scrubbing
  // and the section chips alike.
  useEffect(() => {
    const element = video.current
    if (!element || !checkpoints.length) return
    const onTime = () => {
      const at = element.currentTime
      const mark = checkpoints.find(
        (c) => at >= (c.start ?? 0) && at < (c.end ?? Number.POSITIVE_INFINITY),
      )
      setPlayingIndex(mark ? mark.index : null)
    }
    onTime()
    element.addEventListener('timeupdate', onTime)
    return () => element.removeEventListener('timeupdate', onTime)
  }, [checkpoints])
  const captions = path.replace(/\.[^.]+$/, '.vtt')

  return (
    <div className="player">
      {full && (
        <div className="player-full" onClick={() => setFull(false)}>
          <video
            src={assetUrl(path)}
            controls
            autoPlay
            onClick={(e) => e.stopPropagation()}
            onTimeUpdate={(e) => {
              const track = e.currentTarget.textTracks[0]
              if (track) track.mode = 'hidden'
            }}
          >
            <track kind="captions" srcLang="en" label="English" src={assetUrl(captions)} default />
          </video>
          {caption && <p className="caption-bar caption-full">{caption}</p>}
          <button className="player-options player-full-close" onClick={() => setFull(false)} aria-label="Close full screen">
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      )}

      <div className="player-frame">
        <video
          ref={video}
          src={animationUrl(path)}
          controls
          playsInline
          preload={preload}
          onEnded={onEnded}
        >
          <track kind="captions" srcLang="en" label="English" src={assetUrl(captions)} default />
        </video>

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
            {[0.5, 1, 1.5, 2].map((r) => (
              <button key={r} className={rate === r ? 'on' : ''} onClick={() => setRate(r)}>
                {r}×
              </button>
            ))}
          </div>
          <label className="pose-toggle">
            <input type="checkbox" checked={looping} onChange={(e) => setLooping(e.target.checked)} />
            Loop section
          </label>
          <button className="ghost-btn small" onClick={() => play(null)}>
            Whole animation
          </button>
        </div>
      </div>

      {caption && <p className="caption-bar">{caption}</p>}

      {checkpoints.length > 0 && (
        <div className="sections-drawer">
          <button
            className="sections-toggle"
            onClick={() => setShowSections((open) => !open)}
            aria-expanded={showSections}
          >
            <span className="chevron">{showSections ? '\u25b4' : '\u25be'}</span>
            {checkpoints.length} sections
            {/* What is playing, while the list is closed: otherwise opening
                the drawer is the only way to see where you are. */}
            {!showSections && playingIndex !== null && (
              <span className="sections-now">
                {playingIndex + 1}. {checkpoints[playingIndex]?.name}
              </span>
            )}
          </button>
          {showSections && (
            <div className="sections">
              {checkpoints.map((mark) => (
                <button
                  key={mark.index}
                  className={
                    'section-chip' +
                    (section?.index === mark.index ? ' on' : '') +
                    (playingIndex === mark.index ? ' playing' : '')
                  }
                  onClick={() => play(mark)}
                  title={`${mark.start?.toFixed(1)}s – ${mark.end?.toFixed(1)}s`}
                >
                  {mark.index + 1}. {mark.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
