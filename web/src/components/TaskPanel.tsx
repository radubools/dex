import { useEffect, useRef, useState } from 'react'
import {
  answerQuestion, cancelTask, getTaskMessages, rerunTask,
  resolveApproval, resumeTask, sendTaskMessage,
} from '../api'
import { progressOf, type FeedItem, type TaskView } from '../store'
import type { SourceAnchor, Task, TaskMessage, TaskState } from '../types'

/** States in which the agent has not finished with this task. */
const WORKING_STATES = new Set<TaskState>(['queued', 'running', 'awaiting_input', 'paused'])
import { Attachments, namesOf, type Attached } from './Attachments'
import { ErrorNote } from './ErrorNote'
import { Prose } from './Prose'
import { AssetExplorer } from './AssetExplorer'
import type { ViewerTarget } from './Viewer'

type Tab = 'activity' | 'files'

/** Everything about one running or finished task. */
export function TaskPanel({
  view,
  onOpen,
  openPath,
  onClose,
  onBack,
  onStarted,
  onQueued,
}: {
  view: TaskView
  onOpen: (target: ViewerTarget) => void
  openPath?: string
  onClose: () => void
  /** Pops one level of the mobile navigation stack. */
  onBack: () => void
  /** Called with the new task when this one is resumed or re-run. */
  onStarted: (task: Task) => void
  /** Refreshed queue of follow-up notes for this task. */
  onQueued: (taskId: string, messages: TaskMessage[]) => void
}) {
  const [tab, setTab] = useState<Tab>('activity')
  const [busy, setBusy] = useState(false)
  const feedEnd = useRef<HTMLDivElement>(null)
  const { task } = view
  const { pct, label } = progressOf(view)
  // Anything terminal has nothing left to stop. A
  // paused task is still live: dex will start it again by itself.
  const live = !['succeeded', 'failed', 'cancelled'].includes(task.state)

  // Opening a task (or the Activity tab) jumps to the end; after that, follow
  // the feed only while the reader is already near the bottom, so scrolling up
  // to read something is not yanked back.
  const anchored = useRef(false)
  useEffect(() => {
    anchored.current = false
  }, [task.id, tab])

  useEffect(() => {
    const end = feedEnd.current
    const scroller = end?.parentElement?.parentElement
    if (!end || !scroller) return
    // The scroller is moved directly rather than through scrollIntoView, which
    // scrolls every scrollable ancestor it needs to: with a streaming feed that
    // reached past the panel it would scroll the app shell itself, sliding the
    // panes out from under their own fixed children.
    if (!anchored.current) {
      scroller.scrollTop = scroller.scrollHeight
      anchored.current = true
      return
    }
    const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 220
    if (nearBottom) scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
  }, [view.feed, tab, task.id])

  return (
    <section className="task-panel">
      <header className="panel-head">
        <button className="back-btn" onClick={onBack} aria-label="Back">‹</button>
        <div className="panel-title">
          <span className={`state-dot ${task.state}`} />
          <span className="panel-name">{task.title}</span>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close task">✕</button>
      </header>

      <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)}>
        <div className={`progress-fill ${task.state}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="panel-meta">
        <span className={`badge ${task.state}`}>{task.state.replace('_', ' ')}</span>
        <span className="muted small">{label}</span>
        {task.turns != null && <span className="muted small">{task.turns} turns</span>}
        {task.costUsd ? (
          <span className="muted small" title={task.costIsEstimate ? 'Running estimate at list price' : 'Reported by the agent'}>
            ${task.costUsd.toFixed(3)}
            {task.costIsEstimate ? '~' : ''}
          </span>
        ) : null}
        {task.model && <span className="muted small">{task.model.replace('claude-', '')}</span>}
        {task.attempt > 1 && <span className="muted small">attempt {task.attempt}</span>}
        {live && (
          <button className="ghost-btn small" onClick={() => void cancelTask(task.id)}>Stop</button>
        )}
        {/* Resume picks the package back up where it stopped; re-run starts a
            fresh one and leaves this output alone. */}
        {task.canResume && (
          <button
            className="ghost-btn small accent"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              resumeTask(task.id).then(onStarted).finally(() => setBusy(false))
            }}
          >
            Resume
          </button>
        )}
        {task.canRerun && (
          <button
            className="ghost-btn small"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              rerunTask(task.id).then(onStarted).finally(() => setBusy(false))
            }}
          >
            Re-run
          </button>
        )}
      </div>

      {task.state === 'paused' && (
        <p className="muted small paused-note">
          Paused to give a chat priority. It carries on by itself, continuing the
          same agent session.
        </p>
      )}

      <nav className="tabs">
        <button className={tab === 'activity' ? 'on' : ''} onClick={() => setTab('activity')}>
          Activity
        </button>
        <button className={tab === 'files' ? 'on' : ''} onClick={() => setTab('files')}>
          Files{view.assets.length ? ` (${view.assets.length})` : ''}
        </button>
      </nav>

      <div className="panel-body">
        {tab === 'files' ? (
          <>
            {/* What went in, before what came out. A task planned from a
                survey covers one part of the operator's own material, and
                until this was here the only record of which part was a
                sentence buried in the brief. */}
            {task.anchor && (
              <TaskInput
                anchor={task.anchor}
                project={task.project}
                onOpen={onOpen}
              />
            )}
            <AssetExplorer
              // The directory it writes into, relative to the assets root: the
              // project, then the output slug — which is the parent's for a
              // resumed or re-run attempt, not this task's own slug. Without the
              // project the lookup 404s for every task outside the default one.
              slug={task.project ? `${task.project}/${task.outputSlug}` : task.outputSlug}
              assets={view.assets}
              openPath={openPath}
              onOpen={(path) => onOpen({ kind: 'asset', path })}
            />
          </>
        ) : (
          <div className="feed">
            {view.feed.length === 0 && (
              <p className="muted small">
                {task.state === 'paused'
                  ? 'Stopped before it finished. It goes again on its own when there is room.'
                  : 'Waiting for the agent to start…'}
              </p>
            )}
            {view.feed.map((item) => (
              <FeedRow key={item.id} item={item} taskId={task.id} onOpen={onOpen} />
            ))}
            {task.error && <ErrorNote text={firstLine(task.error)} detail={task.error} />}

            {/* The same "still working" marker the thread shows, at the end of
                the activity so it sits under the newest entry. */}
            {WORKING_STATES.has(task.state) && (
              <div className="chat-status">
                <span className="pulse" />
                {task.state === 'awaiting_input'
                  ? 'waiting for your answer…'
                  : task.activity || `${task.state}…`}
              </div>
            )}

            {view.queued.length > 0 && (
              <div className="queued-notes">
                <div className="card-kind">
                  Queued · {live ? 'sent when this task stops' : 'sending…'}
                </div>
                {view.queued.map((m) => (
                  <div key={m.id} className="queued-note">{m.body}</div>
                ))}
              </div>
            )}
            <div ref={feedEnd} />
          </div>
        )}
      </div>

      {tab === 'activity' && (
        <TaskComposer
          taskId={task.id}
          live={live}
          // The task's own project, not whichever one the sidebar is showing:
          // a panel stays open while the picker moves.
          project={task.project ?? undefined}
          onQueued={(messages) => onQueued(task.id, messages)}
        />
      )}
    </section>
  )
}

/**
 * Chat inside a task. What you type is queued rather than sent: interrupting a
 * run mid-flight would discard work in progress, so notes are held and become
 * the brief for the task's next attempt once it stops.
 */
function TaskComposer({
  taskId,
  live,
  project,
  onQueued,
}: {
  taskId: string
  live: boolean
  /** Whose `datasets/` directory an attachment goes into. */
  project?: string
  onQueued: (messages: TaskMessage[]) => void
}) {
  const [draft, setDraft] = useState('')
  const [attached, setAttached] = useState<Attached[]>([])
  const [busy, setBusy] = useState(false)

  const submit = () => {
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true)
    setDraft('')
    const sending = attached
    setAttached([])
    sendTaskMessage(taskId, text, namesOf(sending))
      .then(() => getTaskMessages(taskId).then(onQueued))
      .catch(() => {
        // Put the message back as it was, attachments included: the files are
        // already on the server, so retrying costs nothing but the click.
        setDraft(text)
        setAttached(sending)
      })
      .finally(() => setBusy(false))
  }

  return (
    <form
      className="task-composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <textarea
        rows={1}
        value={draft}
        enterKeyHint="send"
        placeholder={live ? 'Queue a note for when this finishes…' : 'Ask for a change…'}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <Attachments
        attached={attached}
        onChange={setAttached}
        project={project}
        disabled={busy}
      />
      <button type="submit" className="send-btn" disabled={!draft.trim() || busy}>
        {live ? 'Queue' : 'Send'}
      </button>
    </form>
  )
}

function FeedRow({
  item,
  taskId,
  onOpen,
}: {
  item: FeedItem
  taskId: string
  onOpen: (target: ViewerTarget) => void
}) {
  switch (item.kind) {
    case 'text':
      return (
        <div className={`feed-text ${item.streaming ? 'streaming' : ''}`}>
          <Prose text={item.text} />
        </div>
      )

    case 'thinking':
      // Collapsed by default; the summary shows enough to decide whether to open it.
      return (
        <details className="feed-thinking">
          <summary>
            Thinking
            <span className="thinking-peek">{item.text.slice(-70).replace(/\s+/g, ' ')}</span>
          </summary>
          <Prose text={item.text} />
        </details>
      )

    case 'tool':
      return (
        <div className={`feed-tool ${item.status}`}>
          <span className="tool-dot" />
          <span className="tool-title">{item.title}</span>
          {item.status === 'error' && item.output && (
            <pre className="tool-out">{item.output.slice(0, 800)}</pre>
          )}
        </div>
      )

    case 'diff':
      // A diff indicator, not the diff itself — tapping opens it in the viewer.
      return (
        <button
          className="feed-diff"
          onClick={() => onOpen({ kind: 'diff', path: item.path, patch: item.patch })}
        >
          <span className="diff-icon">±</span>
          <span className="diff-path">{item.path}</span>
          <span className="counts">
            <span className="add">+{item.additions}</span> <span className="del">−{item.deletions}</span>
          </span>
        </button>
      )

    case 'question':
      return <QuestionRow item={item} taskId={taskId} />

    case 'approval':
      return (
        <div className={`feed-card approval ${item.decision ?? 'pending'}`}>
          <div className="card-kind">
            {item.auto ? 'Auto-approved' : item.decision ? 'Reviewed' : 'Needs approval'}
          </div>
          <div className="card-title">{item.title}</div>
          {item.decision ? (
            <div className="muted small">
              {item.auto
                ? 'Allowed automatically (auto-approve is on)'
                : item.decision === 'allow' ? 'Allowed' : 'Denied'}
            </div>
          ) : (
            <div className="actions">
              <button className="primary" onClick={() => void resolveApproval(taskId, item.id, 'allow')}>
                Allow
              </button>
              <button className="danger" onClick={() => void resolveApproval(taskId, item.id, 'deny')}>
                Deny
              </button>
            </div>
          )}
        </div>
      )

    case 'result':
      return (
        <div className="feed-result">
          {item.ok ? 'Finished' : 'Ended with an error'} · {item.turns} turns
          {item.costUsd ? ` · $${item.costUsd.toFixed(4)}` : ''}
          {item.summary && <Prose text={item.summary} />}
        </div>
      )

    case 'note':
      return (
        <div className="feed-note">
          <div className="bubble user">{item.body}</div>
          <span className="muted small">{item.delivered ? 'sent' : 'queued'}</span>
        </div>
      )

    case 'error':
      return <ErrorNote text={firstLine(item.message)} detail={item.message} />
  }
}

function QuestionRow({
  item,
  taskId,
}: {
  item: Extract<FeedItem, { kind: 'question' }>
  taskId: string
}) {
  const [draft, setDraft] = useState('')
  if (item.answer !== undefined) {
    return (
      <div className="feed-card question answered">
        {/* Said by whom matters: an automatic answer is dex acting on a
            standing setting, not a decision the operator made just now. */}
        <div className="card-kind">{item.auto ? 'Asked — answered automatically' : 'Asked'}</div>
        <div className="card-title">
          <Prose text={item.question} />
        </div>
        <div className="muted small">
          {item.auto ? 'Utility sharing is on, so dex said: ' : 'You said: '}
          <Prose text={item.answer} inline />
        </div>
      </div>
    )
  }

  const send = (answer: string) => {
    if (answer.trim()) void answerQuestion(taskId, item.id, answer.trim())
  }

  return (
    <div className="feed-card question">
      <div className="card-kind">dex is asking</div>
      <div className="card-title">
        <Prose text={item.question} />
      </div>
      <div className="options">
        {item.options.map((option) => (
          // The label is rendered; `option` itself is what goes back, so the
          // agent receives the string it offered rather than stripped markup.
          <button key={option} className="option" onClick={() => send(option)}>
            <Prose text={option} inline />
          </button>
        ))}
      </div>
      <div className="freeform">
        <input
          value={draft}
          placeholder="or type an answer"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(draft)}
        />
        <button disabled={!draft.trim()} onClick={() => send(draft)}>Reply</button>
      </div>
    </div>
  )
}


/** The headline of a failure: its first line, which names what went wrong. */
function firstLine(message: string): string {
  const line = message.trim().split('\n')[0]
  return line.length > 200 ? `${line.slice(0, 200)}…` : line
}


/**
 * The material this task was cut out of, above the files it produced.
 *
 * A URL is a link rather than a pane: dex serves the operator's own uploads
 * and will not proxy somebody else's site to put it in a frame, so the honest
 * thing is to hand the address to the browser. An uploaded document opens in
 * the preview pane at the anchored page, which is the whole reason the survey
 * records a page at all.
 */
function TaskInput({
  anchor,
  project,
  onOpen,
}: {
  anchor: SourceAnchor
  project: string | null
  onOpen: (target: ViewerTarget) => void
}) {
  const external = Boolean(anchor.url) && !anchor.page && !anchor.line
  return (
    <div className="task-input">
      <div className="card-kind">From your sources</div>
      {external ? (
        <a className="input-row" href={anchor.url} target="_blank" rel="noreferrer">
          <span className="input-icon" aria-hidden="true">🔗</span>
          <span className="input-name">{anchor.label || anchor.url}</span>
          <span className="input-where">opens in a new tab</span>
        </a>
      ) : (
        <button
          className="input-row"
          onClick={() =>
            onOpen({
              kind: 'source',
              project: project ?? '',
              name: anchor.source,
              anchor,
            })
          }
        >
          <span className="input-icon" aria-hidden="true">📄</span>
          <span className="input-name">{anchor.source}</span>
          <span className="input-where">{anchor.label}</span>
        </button>
      )}
      {/* The line between what was given and what was made. */}
      <div className="files-divider"><span>Produced by this task</span></div>
    </div>
  )
}
