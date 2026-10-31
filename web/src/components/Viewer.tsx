import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github-dark.css'
import { assetUrl, decideTodo, getTask, readAsset } from '../api'
import type { AssetResponse, SourceAnchor, Task } from '../types'
import { resolveWidget, resolveWidgets, type WidgetInfo } from '../api'
import { AnimationPlayer } from './AnimationPlayer'
import { VideoPlayer } from './VideoPlayer'
import { Diff } from './Diff'
import { WidgetHost } from './WidgetHost'
import { useAssetVersion } from '../assetChanges'
import { SourceView } from './SourceView'

// mermaid + marked are ~600kB; only load them when markdown is opened.
const Markdown = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))

/** Rendered by the media view rather than fetched as text. */
const IMAGE = new Set(['gif', 'png', 'jpg', 'jpeg', 'webp', 'svg', 'avif', 'mp4', 'webm', 'mov'])
/** Playable by the browser unaided. `.mid` is not — it needs a synthesiser. */
const AUDIO = new Set(['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aac'])

export type ViewerTarget =
  | { kind: 'asset'; path: string }
  | { kind: 'diff'; path: string; patch: string }
  /** A whole generated package: its assets, then its explanation inline. */
  | {
      kind: 'package'
      path: string
      title: string
      /** The newest task that wrote it, whose activity the Activity tab shows. */
      taskId?: string
    }
  /**
   * A file the operator attached, in `datasets/<project>/`. Addressed by
   * project and name because it is not in the assets tree and has no path.
   * The anchor, when a survey produced one, says where in it to land.
   */
  | { kind: 'source'; project: string; name: string; anchor?: SourceAnchor }

/** What the title bar shows, and what the pane is labelled for a reader. */
const labelOf = (t: ViewerTarget) =>
  t.kind === 'source'
    ? t.anchor ? `${t.name} \u00b7 ${t.anchor.label}` : t.name
    : t.kind === 'diff' ? `diff \u00b7 ${t.path}` : t.path

/** Renders whatever the operator opened: an asset, or a diff from the feed. */
export function Viewer({
  target,
  onClose,
  onBack,
  onOpen,
  activity,
}: {
  target: ViewerTarget
  onClose: () => void
  /** Pops one level of the mobile navigation stack. */
  onBack?: () => void
  /** Opens something from inside the current view, e.g. a package's animation. */
  onOpen?: (target: ViewerTarget) => void
  /**
   * A package's Activity tab: the thread of the task that last wrote it, with
   * its note box, so the package can be talked about where it is being
   * looked at. Drawn by the app, which owns the tasks and their event stream.
   */
  activity?: (taskId: string) => ReactNode
}) {
  const label = labelOf(target)
  const proposedBy = target.kind === 'package' ? target.taskId : undefined
  const taskId = target.kind === 'package' && activity ? target.taskId : undefined
  // Files first, every time a different package opens: the Library is browsed
  // for what things look like, and the conversation is the second question.
  const [tab, setTab] = useState<'files' | 'activity'>('files')
  const opened = target.kind === 'package' ? target.path : null
  useEffect(() => setTab('files'), [opened])
  const showActivity = Boolean(taskId) && tab === 'activity'
  return (
    <section className={`viewer${taskId ? ' with-tabs' : ''}`} role="dialog" aria-label={label}>
      <header className="viewer-bar">
        <button className="back-btn" onClick={onBack ?? onClose} aria-label="Back">‹</button>
        <span className="viewer-path" title={label}>{label}</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
      </header>
      {taskId && (
        <nav className="tabs">
          <button className={tab === 'files' ? 'on' : ''} onClick={() => setTab('files')}>
            Files
          </button>
          <button className={tab === 'activity' ? 'on' : ''} onClick={() => setTab('activity')}>
            Activity
          </button>
        </nav>
      )}
      {showActivity ? (
        <div className="viewer-activity">{activity!(taskId!)}</div>
      ) : (
      <div className="viewer-body">
        {proposedBy && <TodoDecision taskId={proposedBy} />}
        {target.kind === 'diff' ? (
          <Diff patch={target.patch} />
        ) : target.kind === 'package' ? (
          <PackageView path={target.path} title={target.title} onOpen={onOpen} />
        ) : target.kind === 'source' ? (
          <SourceView
            project={target.project}
            name={target.name}
            anchor={target.anchor}
          />
        ) : (
          <AssetView path={target.path} />
        )}
      </div>
      )}
    </section>
  )
}

/**
 * How a package's files are grouped on its page.
 *
 * The last entry matches everything left over, and that matters more than the
 * ones above it: this was an allowlist of five patterns, so a package with a
 * `.mid`, a `.wav` and a `player.html` showed "16 files" in the list and then
 * displayed six. The rest were not hidden on purpose — nothing claimed them, so
 * they were dropped. A project produces what it produces; the viewer does not
 * get to decide which of it exists.
 */
const ASSET_GROUPS: { label: string; icon: string; test: (name: string) => boolean }[] = [
  { label: 'Animations', icon: '🎞', test: (n) => /\.(gif|mp4|webm|mov)$/i.test(n) },
  { label: 'Audio', icon: '🔊', test: (n) => /\.(m4a|wav|mp3|ogg|flac|aac|vtt)$/i.test(n) },
  { label: 'Score', icon: '🎼', test: (n) => /\.(mid|midi|abc|ly)$/i.test(n) },
  { label: 'Pages', icon: '🌐', test: (n) => /\.html?$/i.test(n) },
  { label: 'Images', icon: '🖼', test: (n) => /\.(png|jpe?g|svg|webp|avif)$/i.test(n) },
  { label: 'Code', icon: '🐍', test: (n) => /\.(py|js|mjs|ts|tsx|css)$/i.test(n) },
  { label: 'Data', icon: '📋', test: (n) => /\.(json|csv|tsv|ya?ml|toml)$/i.test(n) },
  { label: 'Documents', icon: '📘', test: (n) => /\.(md|markdown|txt|rst)$/i.test(n) },
  { label: 'Other', icon: '📄', test: () => true },
]

/** Approve or reject a proposed todo beside its spec, where it is read. */
function TodoDecision({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null)
  useEffect(() => {
    let live = true
    setTask(null)
    getTask(taskId).then((got) => live && setTask(got)).catch(() => {})
    return () => { live = false }
  }, [taskId])
  if (!task || !(task.canApprove || task.canReject)) return null
  const decide = (decision: 'approved' | 'rejected') =>
    decideTodo(taskId, decision).then(([changed]) => setTask(changed))
  return (
    <p className="todo-decision">
      <span className="badge">{task.approval === 'rejected' ? 'rejected' : 'needs approval'}</span>
      {task.canApprove && <button className="ghost-btn small accent" onClick={() => void decide('approved')}>✓ Approve</button>}
      {task.canReject && <button className="ghost-btn small" onClick={() => void decide('rejected')}>✕ Reject</button>}
    </p>
  )
}

/**
 * A package as one page: its name, what it produced, and the explanation read
 * inline rather than hidden behind another click.
 */
function PackageView({
  path,
  title,
  onOpen,
}: {
  path: string
  title: string
  onOpen?: (target: ViewerTarget) => void
}) {
  const [files, setFiles] = useState<string[] | null>(null)
  const [explanation, setExplanation] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // How many files the slideshow shows; when it shows any, the single
  // "feature" preview below would only repeat one of them.
  const [slides, setSlides] = useState(0)

  useEffect(() => {
    let live = true
    setFiles(null)
    setExplanation(null)
    setError(null)
    readAsset(path, '**/*')
      .then(async (listing) => {
        if (!live || listing.kind !== 'dir') return
        const names = listing.entries.map((e) => e.name)
        setFiles(names)
        // A todo's spec, or a project's log of ideas, is what its directory is for.
        const doc = names.find((n) => /\/(explanation|SPEC|IDEAS)\.md$/.test(n))
        if (!doc) return
        const asset = await readAsset(doc)
        if (live && asset.kind === 'file') setExplanation(asset.content)
      })
      .catch((err: Error) => live && setError(err.message))
    return () => { live = false }
  }, [path])

  if (error) return <p className="error">{error}</p>
  if (files === null) return <p className="muted">Loading…</p>

  const shortName = (name: string) => name.slice(path.length + 1)
  const claimed = new Set<string>()
  // What to show when there is no explanation: the one asset worth reading,
  // never the source code, and only when it is unambiguous. A package's own
  // deliverable counts — a rendered page, a score sidecar a widget opens — not
  // just the formats dex happened to know about first.
  const previewable = files.filter((n) =>
    /\.(json|md|markdown|gif|png|jpe?g|svg|html?|mid|midi)$/i.test(n),
  )
  const feature = previewable.length === 1 ? previewable[0] : null

  return (
    <div className="package-view">
      <h1 className="package-title">{title}</h1>

      <WidgetSlideshow paths={files} label={shortName} onCount={setSlides} />

      {ASSET_GROUPS.map((group) => {
        const members = files.filter((n) => !claimed.has(n) && group.test(n))
        members.forEach((n) => claimed.add(n))
        if (members.length === 0) return null
        return (
          <div className="package-group" key={group.label}>
            <div className="explorer-label">{group.label}</div>
            <div className="package-links">
              {members.map((name) => (
                <button
                  key={name}
                  className="package-link"
                  onClick={() => onOpen?.({ kind: 'asset', path: name })}
                >
                  <span aria-hidden="true">{group.icon}</span> {shortName(name)}
                </button>
              ))}
            </div>
          </div>
        )
      })}

      {explanation ? (
        <Suspense fallback={<p className="muted">Rendering diagrams…</p>}>
          <Markdown source={explanation} />
        </Suspense>
      ) : feature && slides === 0 ? (
        // No prose to read, so show the thing itself — a pose package is a
        // single JSON file, and its point is the 3D figure, not the text.
        <AssetView path={feature} />
      ) : slides === 0 ? (
        <p className="muted small">Nothing to preview in this package.</p>
      ) : null}
    </div>
  )
}

export function AssetView({ path, onReady }: { path: string; onReady?: () => void }) {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const [state, setState] = useState<
    { s: 'loading' } | { s: 'error'; message: string } | { s: 'ok'; asset: AssetResponse }
  >({ s: 'loading' })

  // Bumps when a task rewrites this file, at most once every ten seconds, so a
  // file being saved over and over does not reload its viewer as fast as it is
  // written (see assetChanges.ts).
  const version = useAssetVersion(path)
  useEffect(() => {
    if (IMAGE.has(ext)) return
    let live = true
    // Only the first load shows "Loading…"; a refresh swaps the content in.
    if (version === 0) setState({ s: 'loading' })
    readAsset(path)
      .then((asset) => live && setState({ s: 'ok', asset }))
      .catch((err: Error) => live && setState({ s: 'error', message: err.message }))
    return () => { live = false }
  }, [path, ext, version])

  const content =
    state.s === 'ok' && 'content' in state.asset ? (state.asset.content as string) : ''
  // Which widget, if any, this project maps this filename to. Asked on every
  // open rather than cached: a widget built a moment ago has to work without a
  // reload, and the answer is one small request.
  const [widget, setWidget] = useState<WidgetInfo | null>(null)
  const [widgetFailed, setWidgetFailed] = useState(false)
  const [resolved, setResolved] = useState(false)
  useEffect(() => {
    let live = true
    setWidget(null)
    setWidgetFailed(false)
    setResolved(false)
    resolveWidget(path)
      .then((r) => live && setWidget(r.widget))
      // A resolution failure is not an error the reader needs: it just means
      // the built-in viewer handles this file, which is the common case.
      .catch(() => live && setWidget(null))
      .finally(() => live && setResolved(true))
    return () => {
      live = false
    }
  }, [path])

  // What a loading placeholder waits for. A widget says so itself (onReady on
  // the host); a built-in viewer is ready once its content is here.
  const builtIn = resolved && (!widget || widgetFailed)
  const contentHere = IMAGE.has(ext) || AUDIO.has(ext) || state.s !== 'loading'
  useEffect(() => {
    if (builtIn && contentHere) onReady?.()
  }, [builtIn, contentHere, onReady])

  // Media first, and above the loading gate: the fetch is deliberately skipped
  // for these (the browser loads them from a URL), so `state` never leaves
  // 'loading' and anything placed after that check would be unreachable.
  //
  // A widget still wins over the built-in player. It did not, because `IMAGE`
  // claimed `.mp4` here and returned before the widget branch below ever ran —
  // so a project rule naming a video silently did nothing. The widget gets
  // empty `text` and fetches its own bytes through `ctx.fetchAssetUrl`.
  if (widget && !widgetFailed && (IMAGE.has(ext) || AUDIO.has(ext))) {
    return (
      <WidgetHost
        widget={widget}
        path={path}
        text=""
        version={version}
        onFallback={() => setWidgetFailed(true)}
        onReady={onReady}
      />
    )
  }
  if (IMAGE.has(ext)) return <ImageView path={path} version={version} />
  if (AUDIO.has(ext)) return <AudioView path={path} />

  if (state.s === 'loading') return <p className="muted">Loading…</p>
  if (state.s === 'error') return <p className="error">{state.message}</p>
  if (state.asset.kind === 'dir') {
    return <ul className="dir-list">{state.asset.entries.map((e) => <li key={e.name}>{e.name}</li>)}</ul>
  }

  // A project rule wins over the built-in choice for everything else too.
  // Binary reaches a widget as well, with empty `text`: that is what lets a
  // `.mid` viewer exist, rather than forcing a rule onto a text sidecar the way
  // the music project had to.
  if (widget && !widgetFailed) {
    return (
      <WidgetHost
        widget={widget}
        path={path}
        text={state.asset.kind === 'file' ? state.asset.content : ''}
        version={version}
        onFallback={() => setWidgetFailed(true)}
        onReady={onReady}
      />
    )
  }

  // Binary with nothing to render it — a `.mid` and no widget. A link beats
  // "415: .mid is binary — use /api/assets/raw", which is what this showed.
  if (state.asset.kind === 'binary') return <BinaryView path={path} ext={ext} />

  if (ext === 'md' || ext === 'markdown') {
    return (
      <Suspense fallback={<p className="muted">Rendering diagrams…</p>}>
        <Markdown source={state.asset.content} />
      </Suspense>
    )
  }
  // A pose used to be special-cased here — dex knew the landmark list, the
  // bones between them, and sniffed for them with `isPose`. That was one
  // project's subject matter compiled into everybody's viewer. It is a widget
  // in the `yoga-pose-viewer` skill now, and a project's own rules decide what
  // opens a file.
  if (ext === 'json') return <Json content={state.asset.content} />
  return <Code content={state.asset.content} ext={ext} />
}

function Code({ content, ext }: { content: string; ext: string }) {
  const html = (() => {
    try {
      return hljs.getLanguage(ext)
        ? hljs.highlight(content, { language: ext }).value
        : hljs.highlightAuto(content).value
    } catch {
      return null
    }
  })()

  return (
    <pre className="code-view">
      {html ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{content}</code>}
    </pre>
  )
}

/** manifest.json gets a readable summary above the raw source. */
function Json({ content }: { content: string }) {
  const parsed = (() => {
    try {
      return JSON.parse(content) as Record<string, unknown>
    } catch {
      return null
    }
  })()
  const approaches = Array.isArray(parsed?.approaches) ? (parsed!.approaches as Record<string, string>[]) : []
  const tests = parsed?.tests as { passed?: number; failed?: number } | undefined

  return (
    <>
      {approaches.length > 0 && (
        <table className="manifest">
          <thead><tr><th>approach</th><th>time</th><th>space</th></tr></thead>
          <tbody>
            {approaches.map((a) => (
              <tr key={a.name}><td>{a.name}</td><td>{a.time}</td><td>{a.space}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {tests && (
        <p className={tests.failed ? 'error' : 'pass'}>
          tests: {tests.passed ?? 0} passed{tests.failed ? `, ${tests.failed} failed` : ''}
        </p>
      )}
      <Code content={content} ext="json" />
    </>
  )
}

/** GIFs autoplay once loaded; replay re-requests the image from frame one. */
function AudioView({ path }: { path: string }) {
  return (
    <div className="audio-view">
      <audio controls preload="metadata" src={assetUrl(path)} />
      <p className="muted small">{path.split('/').pop()}</p>
    </div>
  )
}

/**
 * A file the browser cannot render and no widget claimed.
 *
 * Says what it is and offers it, rather than reporting the server's 415 at the
 * reader — which told them about an API they cannot call and nothing about
 * their file.
 */
function BinaryView({ path, ext }: { path: string; ext: string }) {
  const name = path.split('/').pop() ?? path
  return (
    <div className="binary-view">
      <p>
        <strong>{name}</strong>
      </p>
      <p className="muted small">
        A .{ext} file — nothing in the browser can show this one. A project
        widget could: see “Viewers for this project's files” in the guide.
      </p>
      <a className="ghost-btn small" href={assetUrl(path)} download={name}>
        Download
      </a>
    </div>
  )
}

function ImageView({ path, version = 0 }: { path: string; version?: number }) {
  // GIFs get the player (speed, checkpoints, replay); stills are just shown.
  if (path.toLowerCase().endsWith('.gif')) {
    return <AnimationPlayer path={path} />
  }
  // A narrated animation is a video: it seeks, so sections are looped natively.
  if (/\.(mp4|webm|mov)$/i.test(path)) {
    return <VideoPlayer path={path} />
  }
  return (
    <div className="gif-view">
      {/* The version busts the browser's cache when a task re-renders the image. */}
      <img src={`${assetUrl(path)}&v=${version}`} alt={path} />
    </div>
  )
}

/**
 * Every file in `paths` that a widget opens, one at a time in its widget, with
 * previous / next to step through them. Files with no widget (code, plain
 * images, markdown) are left to the list below, which already opens them in
 * the built-in viewers. Renders nothing when no file has a widget.
 */
export function WidgetSlideshow({
  paths,
  label = (path: string) => path.split('/').pop() ?? path,
  onCount,
}: {
  paths: string[]
  /** How a file is named in the bar. */
  label?: (path: string) => string
  onCount?: (count: number) => void
}) {
  const [slides, setSlides] = useState<{ path: string; widget: WidgetInfo }[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const key = paths.join('\n')
  const counted = useRef(onCount)
  counted.current = onCount
  // The slide whose viewer has said it is ready; until then the placeholder shows.
  const [readyPath, setReadyPath] = useState<string | null>(null)
  const currentRef = useRef(current)
  currentRef.current = current
  const markReady = useCallback(() => setReadyPath(currentRef.current), [])
  // A widget that never reports must not leave the placeholder up for good.
  useEffect(() => {
    if (!current) return
    const t = setTimeout(() => setReadyPath(current), 20_000)
    return () => clearTimeout(t)
  }, [current])

  useEffect(() => {
    let live = true
    if (paths.length === 0) {
      setSlides([])
      counted.current?.(0)
      return
    }
    resolveWidgets(paths)
      .then(({ widgets }) => {
        if (!live) return
        const found = paths.filter((p) => widgets[p]).map((p) => ({ path: p, widget: widgets[p] }))
        setSlides(found)
        counted.current?.(found.length)
        // Keep the file being looked at when new ones arrive mid-task.
        setCurrent((was) => (was && found.some((f) => f.path === was) ? was : found[0]?.path ?? null))
      })
      .catch(() => live && setSlides([]))
    return () => { live = false }
    // `key` stands for `paths`: a new array with the same files is no change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (slides.length === 0 || current === null) return null
  const index = Math.max(0, slides.findIndex((s) => s.path === current))
  const slide = slides[index]
  const shown = readyPath === slide.path
  const go = (step: number) => setCurrent(slides[(index + step + slides.length) % slides.length].path)
  const many = slides.length > 1

  return (
    <div
      className="slideshow"
      tabIndex={0}
      onKeyDown={(e) => {
        if (!many) return
        if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1) }
        if (e.key === 'ArrowRight') { e.preventDefault(); go(1) }
      }}
    >
      <div className="slideshow-bar">
        <button className="slideshow-step" onClick={() => go(-1)} disabled={!many} aria-label="Previous file">‹</button>
        <div className="slideshow-title">
          <span className="slideshow-name">{label(slide.path)}</span>
          <span className="muted small">
            {slide.widget.title} · {index + 1} / {slides.length}
          </span>
        </div>
        <button className="slideshow-step" onClick={() => go(1)} disabled={!many} aria-label="Next file">›</button>
      </div>
      <div className={`slideshow-stage ${shown ? '' : 'loading'}`}>
        {!shown && <SlidePlaceholder name={label(slide.path)} widget={slide.widget.title} />}
        {/* Keyed by path so each file gets a fresh viewer and widget frame. It
            loads underneath the placeholder and fades in when it is ready. */}
        <div className="slideshow-slide">
          <AssetView key={slide.path} path={slide.path} onReady={markReady} />
        </div>
      </div>
    </div>
  )
}

/** Shown while the next slide loads: a gentle shimmer, not a blank flash. */
function SlidePlaceholder({ name, widget }: { name: string; widget: string }) {
  return (
    <div className="slide-placeholder" role="status" aria-live="polite">
      <div className="slide-placeholder-cube" aria-hidden="true">
        <span /><span /><span />
      </div>
      <div className="slide-placeholder-text">
        <span>Loading {name}</span>
        <span className="muted small">{widget}</span>
      </div>
    </div>
  )
}
