import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getFeed, reviewTopic } from '../api'
import { AnimationPlayer } from './AnimationPlayer'
import { VideoPlayer } from './VideoPlayer'
import type { FeedResponse, Topic } from '../types'
import type { ViewerTarget } from './Viewer'

/** The pause after the links appear, before the next reel. */
const HOLD_S = 5
/** Used only when a topic has no animation to time the reel from. */
const FALLBACK_PLAY_S = 8

//: How many reels ahead of the one on screen are worth downloading early.
//: Every reel is mounted at once, so left to itself the browser fetched all
//: hundred and thirty-four and the one being watched queued behind the rest.
//: Small enough to stay well inside the six connections a browser gives one
//: host, large enough that advancing does not wait for a download.
const PREFETCH_AHEAD = 2
//: And one behind, because the feed goes back as well as forward.
const PREFETCH_BEHIND = 1

/**
 * A vertical reel of everything the project has produced, ordered by spaced
 * repetition — most overdue first, then never seen.
 *
 * Scrolling is native scroll-snap, so a swipe on a phone behaves the way a
 * swipe should without a gesture library.
 */
export function Feed({
  project,
  onOpen,
}: {
  project?: string
  onOpen: (target: ViewerTarget) => void
}) {
  const [data, setData] = useState<FeedResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const scroller = useRef<HTMLDivElement>(null)
  const panels = useRef<(HTMLElement | null)[]>([])

  useEffect(() => {
    getFeed(project)
      .then(setData)
      .catch((err: Error) => setError(err.message))
  }, [project])

  const topics = data?.topics ?? []
  const current = topics[index]

  const goTo = useCallback((next: number) => {
    const el = scroller.current
    const panel = panels.current[next]
    if (!el || !panel) return
    // Scrolling the container rather than calling scrollIntoView on the panel:
    // `scroll-snap-stop: always` refuses programmatic jumps past a snap point.
    el.scrollTo({ top: panel.offsetTop - el.offsetTop, behavior: 'smooth' })
  }, [])

  // Which reel is on screen. An IntersectionObserver rather than a scroll
  // listener: it reports the settled panel directly, survives momentum
  // scrolling on iOS, and does not depend on scroll events being delivered.
  useEffect(() => {
    const el = scroller.current
    if (!el || topics.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          const at = panels.current.indexOf(entry.target as HTMLElement)
          if (at >= 0) setIndex((prev) => (prev === at ? prev : at))
        }
      },
      { root: el, threshold: 0.6 },
    )
    for (const panel of panels.current) if (panel) observer.observe(panel)
    return () => observer.disconnect()
  }, [topics.length])

  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown' || e.key === 'PageDown') goTo(index + 1)
      else if (e.key === 'ArrowUp' || e.key === 'PageUp') goTo(index - 1)
      else if (e.key === ' ') {
        e.preventDefault()
        setPlaying((p) => !p)
      }
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [index, goTo])

  const rate = (rating: 'again' | 'good' | 'easy') => {
    if (!current) return
    void reviewTopic(current.slug, rating, data?.project)
    goTo(index + 1)
  }

  if (error) {
    return (
      <section className="review-feed">
        <p className="error" style={{ padding: 16 }}>{error}</p>
      </section>
    )
  }

  return (
    <section className="review-feed" aria-label="Review feed">
      <div className="feed-status">
        <span className="muted small">
          {data?.project}
          {topics.length ? ` · ${index + 1}/${topics.length}` : ''}
        </span>
        <button className="ghost-btn small" onClick={() => setPlaying((p) => !p)}>
          {playing ? 'Pause' : 'Play'}
        </button>
      </div>

      {data && topics.length === 0 && (
        <p className="muted" style={{ padding: 16 }}>
          Nothing to review yet — finish a task and its package will appear here.
        </p>
      )}

      <div className="reels" ref={scroller}>
        {topics.map((topic, i) => (
          <Reel
            key={topic.slug}
            ref={(el) => { panels.current[i] = el }}
            topic={topic}
            active={i === index}
            playing={playing && i === index}
            preload={
              i >= index - PREFETCH_BEHIND && i <= index + PREFETCH_AHEAD ? 'auto' : 'none'
            }
            onOpen={onOpen}
            onAdvance={() => {
              // Auto-advance counts as having seen it.
              void reviewTopic(topic.slug, 'good', data?.project)
              goTo(i + 1)
            }}
            onRate={rate}
          />
        ))}
      </div>
    </section>
  )
}

/** Drawn, so it matches the weight of the text beside it at any size. */
function Chevron({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg
      width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round"
    >
      <path d={direction === 'left' ? 'M10 3 5 8l5 5' : 'M6 3l5 5-5 5'} />
    </svg>
  )
}


type ReelProps = {
  topic: Topic
  active: boolean
  playing: boolean
  /** What this reel's video may fetch before anyone looks at it. */
  preload: 'none' | 'metadata' | 'auto'
  onOpen: (target: ViewerTarget) => void
  onAdvance: () => void
  onRate: (rating: 'again' | 'good' | 'easy') => void
}

const Reel = function Reel({ ref, topic, active, playing, preload, onOpen, onAdvance, onRate }: ReelProps & {
  ref: (el: HTMLElement | null) => void
}) {
  // Every approach the package animates, played one after another. Older
  // feed data has no `clips`, so fall back to the plain paths.
  const clips = topic.clips?.length
    ? topic.clips
    : topic.animations.map((path) => ({ path, name: '', duration: null }))
  const [clip, setClip] = useState(0)
  const clipRef = useRef(clip)
  clipRef.current = clip
  const current = clips[Math.min(clip, clips.length - 1)]

  // The reel runs for exactly as long as its animations do, added up. This
  // was a fixed eight seconds for videos, because nothing measured them: a
  // minute-long animation was cut off after eight.
  const playFor =
    clips.reduce((total, c) => total + (c.duration ?? FALLBACK_PLAY_S), 0) || FALLBACK_PLAY_S
  const [phase, setPhase] = useState<'playing' | 'holding'>('playing')
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (!active) {
      setPhase('playing')
      setProgress(0)
      setClip(0)
    }
  }, [active])

  // One timeline per reel: play each animation, reveal the links, hold, move
  // on. The clock drives the progress bar; what actually ends the reel is the
  // last clip reporting that it finished, because the measured length is the
  // sum of a video's sections and that can run a second or two short of the
  // file — three clips of drift is enough to cut the last approach off.
  const finished = useRef(false)
  useEffect(() => {
    if (!active || !playing) return
    const total = (playFor + HOLD_S) * 1000
    const started = Date.now()
    const tick = window.setInterval(() => {
      const elapsed = Date.now() - started
      setProgress(Math.min(1, elapsed / total))
      setPhase(elapsed > playFor * 1000 ? 'holding' : 'playing')
      // The safety net, not the schedule: a video that never fires `ended` —
      // one that failed to load, say — must not strand the feed. The margin
      // keeps it clear of the clips finishing normally.
      if (elapsed >= total * 1.3 && !finished.current) {
        finished.current = true
        window.clearInterval(tick)
        onAdvance()
      }
    }, 100)
    return () => window.clearInterval(tick)
  }, [active, playing, playFor, onAdvance])

  useEffect(() => {
    if (!active) finished.current = false
  }, [active])

  // Stepping through the approaches by hand. Bounded rather than wrapping:
  // the last clip ends the reel, so wrapping round to the first would make
  // the feed impossible to leave.
  const goToClip = useCallback(
    (next: number) => setClip(Math.max(0, Math.min(clips.length - 1, next))),
    [clips.length],
  )

  // Left and right for approaches, alongside up and down for reels.
  useEffect(() => {
    if (!active) return
    const keys = (e: KeyboardEvent) => {
      // Left and right belong to whatever is being typed into, if anything.
      // Nothing in a reel takes text today; this is so that stays true.
      const target = e.target as HTMLElement | null
      if (target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName ?? '')) {
        return
      }
      // The video's own arrow-key seeking gives way to stepping through the
      // approaches: this is a slideshow, and the scrubber is still there for
      // anyone who wants to move within one clip.
      if (e.key === 'ArrowRight') goToClip(clipRef.current + 1)
      else if (e.key === 'ArrowLeft') goToClip(clipRef.current - 1)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [active, goToClip])

  // The end of the last clip is the end of the reel: hold on the links for a
  // moment so they can be read, then move on.
  const onClipEnded = () => {
    if (clip < clips.length - 1) {
      setClip((n) => n + 1)
      return
    }
    setPhase('holding')
    if (finished.current) return
    window.setTimeout(() => {
      if (finished.current) return
      finished.current = true
      onAdvance()
    }, HOLD_S * 1000)
  }

  const approaches = topic.manifest?.approaches ?? []

  return (
    <article className="reel" ref={ref}>
      <div className="reel-progress"><span style={{ width: `${progress * 100}%` }} /></div>

      <h2 className="reel-title">
        {topic.title}
        {clips.length > 1 && (
          // Which approach is on screen, and a way to move between them.
          // Without the count a reel that plays three animations looks like
          // one that restarted twice.
          <span className="reel-clips">
            <button
              className="clip-step"
              onClick={() => goToClip(clip - 1)}
              disabled={clip === 0}
              title="Previous approach"
              aria-label="Previous approach"
            >
              <Chevron direction="left" />
            </button>
            <span className="reel-clip">
              {clip + 1}/{clips.length}
              {current.name && ` · ${current.name.replace(/_/g, ' ')}`}
            </span>
            <button
              className="clip-step"
              onClick={() => goToClip(clip + 1)}
              disabled={clip === clips.length - 1}
              title="Next approach"
              aria-label="Next approach"
            >
              <Chevron direction="right" />
            </button>
          </span>
        )}
      </h2>

      <div className="reel-media">
        {current ? (
          // Remounting on activation restarts the animation from frame one.
          // A narrated animation is a video; the older silent ones are GIFs.
          /\.(mp4|webm|mov)$/i.test(current.path) ? (
            <VideoPlayer
              // Stable across activation, unlike the GIF below: remounting
              // discarded a video this reel had already downloaded and fetched
              // the same two megabytes again. The player rewinds itself when
              // it becomes the active one.
              key={topic.slug}
              path={current.path}
              // The clip's own end is what moves the playlist on, rather than
              // the measured length: the sum of a video's sections can differ
              // from the file by a second or two, and stopping early would cut
              // the tail off every approach.
              onEnded={onClipEnded}
              // `playing` is already "this reel, and the feed is running".
              // Every reel is mounted at once, so an unconditional autoplay
              // would start a hundred and thirty-four videos together.
              autoPlay={playing}
              active={active}
              preload={preload}
            />
          ) : (
            <AnimationPlayer key={`${topic.slug}-${active}-${clip}`} path={current.path} compact />
          )
        ) : (
          <div className="reel-placeholder">No animation for this topic</div>
        )}
      </div>

      <div className="reel-body">
        {topic.summary && topic.summary !== topic.title && (
          <p className="reel-summary">{topic.summary}</p>
        )}
        <div className="reel-meta">
          <span className="mono">{topic.slug}</span>
          {topic.due && <span className="chip warn">due</span>}
          {topic.seenCount > 0 && <span className="chip">seen {topic.seenCount}×</span>}
        </div>

        {approaches.length > 0 && (
          <ul className="reel-approaches">
            {approaches.map((a) => (
              <li key={a.name}>
                <span>{a.name.replace(/_/g, ' ')}</span>
                <span className="mono">{a.time} · {a.space}</span>
              </li>
            ))}
          </ul>
        )}

        <div className={`reel-links ${phase === 'holding' ? 'shown' : ''}`}>
          {topic.explanation && (
            <button onClick={() => onOpen({ kind: 'asset', path: topic.explanation! })}>
              📘 Explanation
            </button>
          )}
          {topic.solutions && (
            <button onClick={() => onOpen({ kind: 'asset', path: topic.solutions! })}>
              🐍 Solutions
            </button>
          )}
          {topic.tests && (
            <button onClick={() => onOpen({ kind: 'asset', path: topic.tests! })}>
              ✅ Tests
            </button>
          )}
          {topic.animations.slice(1).map((path) => (
            <button key={path} onClick={() => onOpen({ kind: 'asset', path })}>
              🎞 {path.split('/').pop()}
            </button>
          ))}
        </div>

        <div className="reel-rate">
          <button onClick={() => onRate('again')}>Again</button>
          <button onClick={() => onRate('good')}>Good</button>
          <button onClick={() => onRate('easy')}>Easy</button>
        </div>
      </div>
    </article>
  )
}
