/**
 * One task's scored criteria, read live out of `experiments.state` and
 * `experiments.history`.
 *
 * The file this opens is a **pointer**, not a payload:
 *
 *     {"format": "experiments.criteria", "task": "doc-10/run_round.py",
 *      "chunks": "chunks.json"}
 *
 * Everything else is fetched when the widget mounts. Criteria come from
 * `/api/sql`; the chunk *text* comes from a sibling `chunks.json` through
 * `ctx.fetchAsset`, because the tables key every answer by a 12-hex chunk id and
 * store no text.
 *
 * Two things about the environment this runs in, both of which shaped the code:
 *
 * *   **The frame has an opaque origin.** No cookie, no session, no same-origin
 *     `/api/*`. It reaches `/api/sql` as a cross-origin request that the
 *     endpoint answers with `Access-Control-Allow-Origin: *`, and it uses GET
 *     with query parameters so the browser treats it as a simple request and
 *     skips the preflight entirely. The base URL is **not** hard-coded: it is
 *     read off `location.ancestorOrigins` / `document.referrer`, which do work
 *     in there, so the request goes back to whichever origin served the dex page
 *     — `.local`, a tailnet name, a LAN address or localhost. See `apiBases`.
 * *   **A file may still carry its own data.** `dashboard.export` writes a
 *     self-contained file with `criteria` in it. If that key is present this
 *     widget never touches the network, which is what keeps it working when the
 *     API is not installed and what lets its tests run offline.
 *
 * The cards show the metrics each run *recorded*, verbatim. Nothing is
 * recomputed from the verdicts: `experiments.history` holds no ground truth —
 * no gold answer per chunk anywhere in the schema — so a derived "accuracy"
 * would be a number with no referent. The threshold filters the listing and
 * nothing else; the criterion order is the stored `hits`, descending.
 */

export type Ctx = {
  path: string
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  fetchAssetUrl: (path: string) => Promise<string>
}

export type Pointer = {
  format?: string
  task?: string
  /**
   * The chunk text: either the *name* of a sibling to read it from (a pointer,
   * defaulting to `chunks.json`) or the map itself (what `dashboard.export`
   * embeds). Both shapes are on disk, so both are handled.
   */
  chunks?: string | Record<string, ChunkText | string>
  /**
   * Where `/api/sql` lives, e.g. `http://macbook-pro-4.local:4318`. Omit it and
   * the widget uses the origin that served the dex page, then dex's own port.
   * Set it only to force one — it then suppresses both.
   */
  api?: string
  /** Fraction of P(true) a chunk must reach to be listed. Defaults to 0.30. */
  threshold?: number
  /** Present in a self-contained export; when it is, nothing is fetched. */
  criteria?: Criterion[]
}

export type Criterion = {
  name: string
  kind: string
  model: string
  question?: { type?: string; instructions?: string; criteria?: string[] }
  metrics?: Record<string, unknown>
  hits?: number | null
  recorded_at?: string
  note?: string | null
  /** Only in a self-contained export; otherwise fetched on selection. */
  answers?: Answer[]
}

export type Answer = {
  chunk: string
  verdict?: unknown
  score?: number | null
  confidence?: number | null
  probabilities?: Record<string, number> | null
}

type ChunkText = { text: string; n?: number; page?: number }

/**
 * Last resort only: dex's API port, for when the page that framed this widget
 * cannot be identified at all.
 *
 * It used to be the *only* base, and that was the bug behind "cannot reach
 * http://127.0.0.1:4317/api/sql (Load failed)". It is right exactly when the
 * browser sits on the same machine as dex *and* the browser is willing to let
 * the page's origin call loopback on another port. dex is routinely opened under
 * another name — `web/vite.config.ts` lists `.ts.net`, `.local`,
 * `macbook-pro-4` and `mac` in `allowedHosts` precisely because it is — and from
 * `http://macbook-pro-4.local:4318` this is a different host and a different
 * port. A browser that declines that request reports a bare "Load failed" with
 * no status, which is indistinguishable from the server being down.
 */
export const FALLBACK_API = 'http://127.0.0.1:4317'
export const DEFAULT_THRESHOLD = 0.3
export const DEFAULT_CHUNKS = 'chunks.json'

/**
 * The origin of the dex page this widget is framed inside, or '' if unknowable.
 *
 * The frame is `sandbox="allow-scripts"` with no `allow-same-origin`, so its own
 * origin is the string `"null"`. Two things are still readable from in there,
 * both measured in `widgets/criteria-dashboard/test/sandbox.spec.ts`:
 * `location.ancestorOrigins[0]` and `document.referrer` each give the parent's
 * origin. Either one turns a hard-coded host into the origin that actually
 * served the page — where `/api` is answered regardless, because the dev server
 * proxies it to 4317 and a production build is served by the Python server that
 * owns it.
 *
 * A bare relative `/api/sql` also works, as it happens: a `srcdoc` frame
 * inherits the parent's base URI, so it resolves. It is deliberately **not** used
 * — base-URI inheritance through `srcdoc` is the sort of detail that varies
 * between engines, and when it varies the symptom is a bare "Load failed" with
 * no status, which is the failure this function exists to prevent.
 */
export function parentOrigin(): string {
  try {
    const ancestors = (location as { ancestorOrigins?: DOMStringList }).ancestorOrigins
    const first = ancestors && ancestors.length ? ancestors[0] : ''
    if (first && first !== 'null') return first.replace(/\/+$/, '')
  } catch {
    /* ancestorOrigins is not in every engine */
  }
  try {
    if (document.referrer) return new URL(document.referrer).origin
  } catch {
    /* referrer suppressed or unparseable */
  }
  return ''
}

/**
 * Every base to try for `/api/sql`, in order, without duplicates.
 *
 * An explicit `api` in the opened file wins outright and is used alone: if
 * someone named a host, quietly calling a different one is worse than failing.
 */
export function apiBases(explicit?: string): string[] {
  if (explicit) return [explicit.replace(/\/+$/, '')]
  return [...new Set([parentOrigin(), FALLBACK_API].filter(Boolean))]
}

/** The five the cards show, in order, with the label each gets. */
export const CARDS: [string, string][] = [
  ['hits', 'hits'],
  ['lift', 'lift'],
  ['support', 'support'],
  ['mean_confidence', 'mean confidence'],
  ['J', "Youden's J"],
]

// ---------------------------------------------------------------------------
// The queries. Mirrored in dex-sql-api/test_sql.py, which runs them against the
// real tables -- so a change on either side fails a test rather than the view.
// ---------------------------------------------------------------------------

export const CRITERIA_SQL = `
SELECT DISTINCT ON (s.id)
       s.name, s.kind,
       coalesce(h.detail->>'model',
                CASE s.kind WHEN 'jev_criterion' THEN 'jev' ELSE 'laya' END) AS model,
       coalesce(h.detail->'question', s.body->'question', '{}'::jsonb)       AS question,
       h.metrics,
       (h.metrics->>'hits')::float AS hits,
       h.recorded_at, h.note
  FROM experiments.state   s
  JOIN experiments.history h ON h.state_id = s.id
 WHERE h.task = $1
   AND s.kind IN ('laya_criterion', 'jev_criterion')
 ORDER BY s.id,
          (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,
          h.recorded_at DESC, h.id DESC`

/**
 * One criterion's per-chunk answers, normalised out of **three** stored shapes.
 *
 * `detail.answers` is written by each round script and there are three forms in
 * the live schema today:
 *
 * | shape | example | tasks | criteria |
 * |---|---|---|---|
 * | object | `{"verdict": 0.81, "confidence": 0.81, "probabilities": {…}}` | 149 | 7,616 |
 * | number | `0.99` | 1 | 243 |
 * | array | `[0.32, 0.68]` — verdict, then confidence | 2 | 188 |
 *
 * Reading `a.value->>'verdict'` handles only the first. On the other two it
 * returns NULL for every chunk, so the criterion has answers, the listing filters
 * them all out, and the pane goes empty with no error anywhere — which is exactly
 * what the largest package in the project (243 criteria) did. Normalise first,
 * then cast.
 *
 * `latest` picks the entry *before* the expansion: a `DISTINCT ON (s.id)` over
 * the lateral join collapses a whole document to one chunk and returns a
 * plausible single row.
 */
export const ANSWERS_SQL = `
WITH latest AS (
  SELECT h.id
    FROM experiments.state   s
    JOIN experiments.history h ON h.state_id = s.id
   WHERE h.task = $1 AND s.name = $2 AND s.kind = $3
   ORDER BY (h.detail->'answers' IS NOT NULL AND h.detail->'answers' <> '{}'::jsonb) DESC,
            h.recorded_at DESC, h.id DESC
   LIMIT 1
), expanded AS (
  SELECT a.key   AS chunk,
         a.value AS raw,
         CASE jsonb_typeof(a.value)
              WHEN 'object' THEN a.value->>'verdict'
              WHEN 'array'  THEN a.value->>0
              ELSE a.value #>> '{}'
         END AS verdict_text,
         CASE jsonb_typeof(a.value)
              WHEN 'object' THEN a.value->>'confidence'
              WHEN 'array'  THEN a.value->>1
         END AS confidence_text
    FROM experiments.history h
    JOIN latest l ON l.id = h.id
    CROSS JOIN LATERAL jsonb_each(coalesce(h.detail->'answers', '{}'::jsonb)) AS a
)
SELECT chunk,
       verdict_text AS verdict,
       CASE WHEN verdict_text ~ '^-?[0-9]+(\\.[0-9]+)?\$'
            THEN verdict_text::float END AS score,
       CASE WHEN confidence_text ~ '^-?[0-9]+(\\.[0-9]+)?\$'
            THEN confidence_text::float END AS confidence,
       CASE WHEN jsonb_typeof(raw) = 'object' THEN raw->'probabilities' END AS probabilities
  FROM expanded
 ORDER BY score DESC NULLS LAST, confidence DESC NULLS LAST, chunk`

/**
 * Every task in the database whose name refers to `$1`, most criteria first.
 *
 * `$1` is a regular expression built by `taskPattern`, not a plain prefix,
 * because `history.task` is written by hand by each round script and names its
 * package in three different shapes: bare (`doc-5`), with a step after a slash
 * (`doc-5/r4`, `doc-10/run_round.py`), and with the project in front
 * (`construction/doc-61/rescore.py`). Eight more use `#` as the separator
 * (`construction/doc-31#v5`). A `LIKE 'pkg/%'` prefix reaches 148 of the 156
 * tasks on disk today; this reaches all 156.
 */
export const TASKS_SQL = `
SELECT h.task,
       count(DISTINCT s.id) AS criteria,
       count(*)             AS entries,
       max(h.recorded_at)   AS last_run
  FROM experiments.history h
  JOIN experiments.state   s ON s.id = h.state_id
 WHERE s.kind IN ('laya_criterion', 'jev_criterion')
   AND h.task ~ $1
 GROUP BY h.task
 ORDER BY criteria DESC, entries DESC, h.task`

/** The package directory a file sits in: `construction/doc-5/chunks.json` -> `doc-5`. */
export function packageOf(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length >= 2 ? parts[parts.length - 2] : ''
}

/**
 * A Postgres regex matching every `history.task` that names `pkg`.
 *
 * Bounded at both ends — start of string or `/` before, end of string or `/`
 * or `#` after — so `doc-1` does not also match `doc-13`. Metacharacters are
 * escaped: a package name may contain `.`, and an unescaped one would.
 */
export function taskPattern(pkg: string): string {
  const escaped = pkg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return `(^|/)${escaped}($|[/#])`
}

/**
 * Parse the opened file. Three shapes are allowed, and the file says which.
 *
 * - **A pointer** — has `task`. Everything else is queried.
 * - **An export** — has `criteria`. Nothing is queried.
 * - **A chunk store** — `chunks.json` itself, which has neither. The task is
 *   then derived from the directory the file sits in, which is why this returns
 *   the store rather than refusing: `chunks.json` is the file that exists in
 *   every package with anything to show, so it is the one worth clicking, and
 *   asking someone to add a second file beside it to enable a view of the first
 *   is a chore with no payoff.
 */
export function parsePointer(text: string): Pointer {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch (err) {
    throw new Error(`not a criteria file: ${(err as Error).message}`)
  }
  const doc = raw as Pointer
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    throw new Error('not a criteria file: the top level is not an object')
  }
  return doc
}

/** Chunk ids are hex digests — this is what tells a chunk map from any other object. */
const CHUNK_ID = /^[0-9a-f]{8,}$/

/**
 * True if the opened file is a chunk store rather than a pointer or an export.
 *
 * The bare-map case has to be recognised by its **keys**, not by its values.
 * "every value is a string" also describes `{"format": "experiments.criteria"}`,
 * so a pointer missing its `task` was read as a chunk map and then failed with
 * "cannot tell which package" instead of saying what the file actually lacked.
 * Chunk ids are hex digests, and nothing else in these files looks like one.
 */
export function isChunkStore(doc: Pointer): boolean {
  if (doc.task || Array.isArray(doc.criteria)) return false
  const inner = (doc as { chunks?: unknown }).chunks
  if (inner && typeof inner === 'object') return true
  const keys = Object.keys(doc as Record<string, unknown>)
  return keys.length > 0 && keys.every((k) => CHUNK_ID.test(k))
}

/**
 * Run one read through `/api/sql`.
 *
 * GET, not POST: a GET with no custom header is a CORS "simple request", so the
 * browser sends it straight out of an opaque-origin frame with no preflight.
 * A POST with `content-type: application/json` would need one.
 */
export async function ask(
  api: string | string[],
  sql: string,
  params: unknown[] = [],
  limit = 20000,
): Promise<Record<string, unknown>[]> {
  const given = (Array.isArray(api) ? api : [api]).map((b) => b.replace(/\/+$/, ''))
  // A base that already answered goes first, so the second query of a session
  // does not re-probe one that failed the first time and pay its timeout again.
  const bases = reached && given.includes(reached)
    ? [reached, ...given.filter((b) => b !== reached)]
    : given
  const query =
    `/api/sql?sql=${encodeURIComponent(sql)}` +
    `&params=${encodeURIComponent(JSON.stringify(params))}&limit=${limit}`

  const unreachable: string[] = []
  for (const base of bases) {
    let response: Response
    try {
      response = await fetch(base + query)
    } catch (err) {
      // A refused connection, a blocked cross-origin request and a CORS failure
      // are one indistinguishable TypeError with no status, so the only useful
      // thing to do is note it and try the next base.
      unreachable.push(`${base} (${(err as Error).message})`)
      continue
    }
    // Reached the server. Anything it says is the answer, including a refusal --
    // falling through to another base here would hide a bad query behind a
    // connection error.
    reached = base
    const body = (await response.json().catch(() => ({}))) as {
      rows?: Record<string, unknown>[]
      error?: string
    }
    if (!response.ok) throw new Error(body.error ?? `/api/sql returned ${response.status}`)
    return body.rows ?? []
  }

  throw new Error(
    `cannot reach /api/sql. Tried: ${unreachable.join('; ')}. ` +
      'Is dex running, and is the /api/sql router installed? ' +
      'If dex is open under a different name, put {"api": "<origin>"} in the file.',
  )
}

/**
 * The base that last answered, so the rest of a session does not re-probe a base
 * that already failed. Exported for the tests to read; never write it.
 */
export let reached = ''

/** A metric value as a short string, or `—` when the run recorded none. */
export function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—'
    return Number.isInteger(value) ? String(value) : value.toFixed(value < 1 ? 4 : 3)
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

/**
 * Criteria in the order the list shows them: stored `hits` descending, then by
 * name.
 *
 * A criterion whose run recorded no `hits` sorts after every one that did and
 * shows no number, which for most tasks is most of them -- `hits` is a metric
 * the sweep chose to record, not something this view derives.
 */
export function ordered(criteria: Criterion[]): Criterion[] {
  return [...criteria].sort(
    (a, b) => (hitsOf(b) ?? -1) - (hitsOf(a) ?? -1) || a.name.localeCompare(b.name),
  )
}

/**
 * The `hits` the run recorded, or null if it recorded none.
 *
 * Two places hold it, because the two sources put it in different spots: the
 * live query lifts it into a column of its own, and `dashboard.export` leaves it
 * inside `metrics` where the run wrote it. Reading only the column sorts an
 * export alphabetically and shows a dash on every row — which looks exactly
 * like a task that never recorded the metric, so nothing about it draws the eye.
 */
export function hitsOf(criterion: Criterion): number | null {
  for (const value of [criterion.hits, criterion.metrics?.hits]) {
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

/**
 * The answers worth listing at `threshold`, strongest first.
 *
 * The filter is on the **verdict** — P(true) — not on `confidence`. For a yes/no
 * they are not the same thing and the difference is the whole point: a chunk at
 * P(true) 0.03 has confidence 0.97 and is confidently *not* an instance of the
 * criterion, so listing it as "applicable" would be backwards. A choice or score
 * verdict is a label with no probability of its own, and those fall back to
 * confidence.
 */
export function applicable(answers: Answer[], threshold: number): Answer[] {
  return answers
    .filter((a) => strength(a) >= threshold)
    .sort((a, b) => strength(b) - strength(a) || a.chunk.localeCompare(b.chunk))
}

/**
 * How strongly one answer says "this chunk is an instance", as a 0..1 number.
 *
 * `score` first (the live query casts a numeric verdict into it), then a
 * `verdict` that is already a number (what `dashboard.export` writes), and only
 * then `confidence`.
 *
 * The order is load-bearing, and getting it wrong is silent. Falling through to
 * `confidence` for a yes/no inverts the whole listing: P(true) 0.03 carries
 * confidence 0.97, so every chunk the criterion confidently *rejects* would rank
 * at the top of a list headed "applicable". Confidence is the fallback only for
 * a choice or score verdict, which is a label and has no probability of its own.
 */
export function strength(a: Answer): number {
  if (typeof a.score === 'number' && Number.isFinite(a.score)) return a.score
  if (typeof a.verdict === 'number' && Number.isFinite(a.verdict)) return a.verdict
  if (typeof a.confidence === 'number' && Number.isFinite(a.confidence)) return a.confidence
  return -1
}

const CSS = `
.cd { font: 14px/1.5 system-ui, sans-serif; display: grid; grid-template-columns: 260px 1fr;
  gap: 0 18px; align-items: start; }
.cd-head { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px;
  padding-bottom: 10px; border-bottom: 1px solid var(--cd-line); margin-bottom: 12px; }
.cd-task { font-size: 16px; font-weight: 650; margin: 0; font-family: ui-monospace, monospace; }
.cd-chip { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--cd-line);
  text-transform: uppercase; letter-spacing: .05em; }
.cd-chip.laya { border-color: var(--cd-laya); color: var(--cd-laya); }
.cd-chip.jev { border-color: var(--cd-jev); color: var(--cd-jev); }
.cd-picker { font: inherit; font-size: 12px; font-family: ui-monospace, monospace;
  background: transparent; color: inherit; border: 1px solid var(--cd-line);
  border-radius: 6px; padding: 2px 4px; max-width: 100%; }
.cd-note { grid-column: 1 / -1; font-size: 12.5px; padding: 8px 10px; border-radius: 6px;
  border: 1px solid var(--cd-warn-line); background: var(--cd-warn-bg); margin-bottom: 10px; }
.cd-side { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.cd-filter { width: 100%; box-sizing: border-box; padding: 5px 8px; border-radius: 6px;
  border: 1px solid var(--cd-line); background: transparent; color: inherit; font: inherit; }
.cd-list { list-style: none; margin: 0; padding: 0; max-height: 620px; overflow-y: auto;
  border: 1px solid var(--cd-line); border-radius: 8px; }
.cd-item { display: flex; justify-content: space-between; gap: 8px; align-items: baseline;
  padding: 6px 9px; cursor: pointer; border-bottom: 1px solid var(--cd-line-soft); }
.cd-item:last-child { border-bottom: 0; }
.cd-item:hover { background: var(--cd-hover); }
.cd-item[aria-selected=true] { background: var(--cd-sel); font-weight: 650; }
.cd-item[hidden] { display: none; }
.cd-name { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }
.cd-hits { font-family: ui-monospace, monospace; font-size: 11px; opacity: .6; flex: none; }
.cd-main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.cd-q { margin: 0; font-size: 15px; }
.cd-meta { font-size: 11.5px; opacity: .6; font-family: ui-monospace, monospace; }
.cd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }
.cd-card { border: 1px solid var(--cd-line); border-radius: 8px; padding: 8px 10px; }
.cd-card-v { font-size: 19px; font-weight: 650; font-variant-numeric: tabular-nums; }
.cd-card-k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }
.cd-rest { font-size: 11.5px; opacity: .7; font-family: ui-monospace, monospace; word-break: break-all; }
.cd-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 13px; }
.cd-bar input[type=range] { flex: 1 1 160px; }
.cd-count { font-variant-numeric: tabular-nums; }
.cd-chunks { list-style: none; margin: 0; padding: 0; max-height: 520px; overflow-y: auto;
  border: 1px solid var(--cd-line); border-radius: 8px; }
.cd-chunk { display: grid; grid-template-columns: 74px 1fr; gap: 0 10px; padding: 9px 11px;
  border-bottom: 1px solid var(--cd-line-soft); }
.cd-chunk:last-child { border-bottom: 0; }
.cd-score { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 650;
  font-variant-numeric: tabular-nums; }
.cd-conf { font-family: ui-monospace, monospace; font-size: 10.5px; opacity: .55; }
.cd-id { font-family: ui-monospace, monospace; font-size: 10px; opacity: .4; }
.cd-text { margin: 0; white-space: pre-wrap; font-size: 13px; max-height: 8.4em; overflow: hidden;
  position: relative; }
.cd-text.open { max-height: none; }
.cd-more { font-size: 11px; opacity: .6; cursor: pointer; background: none; border: 0;
  color: inherit; padding: 2px 0; text-align: left; }
.cd-empty { padding: 14px; opacity: .6; font-size: 13px; }
.cd-err { color: var(--cd-alarm); font-family: ui-monospace, monospace; font-size: 12px;
  white-space: pre-wrap; }
`

const THEMES: Record<string, string> = {
  dark: `--cd-line:#2a2f3a; --cd-line-soft:#20242c; --cd-hover:#1b1f27; --cd-sel:#243043;
         --cd-laya:#7dd3fc; --cd-jev:#c4b5fd; --cd-alarm:#e0736b;
         --cd-warn-line:#5a3b1c; --cd-warn-bg:#2a1f12;`,
  light: `--cd-line:#d9dde4; --cd-line-soft:#ecEEF2; --cd-hover:#f3f5f8; --cd-sel:#e3ecfa;
          --cd-laya:#0369a1; --cd-jev:#6d28d9; --cd-alarm:#b3372c;
          --cd-warn-line:#e3c99a; --cd-warn-bg:#fdf6e8;`,
}

const el = (tag: string, cls?: string, text?: string) => {
  const node = document.createElement(tag)
  if (cls) node.className = cls
  if (text !== undefined) node.textContent = text
  return node
}

export async function mount(host: HTMLElement, ctx: Ctx): Promise<() => void> {
  const pointer = parsePointer(ctx.text)
  // Not one base but an ordered list: the origin that served the dex page
  // first, dex's own port as a fallback. `pointer.api` overrides both.
  const api = apiBases(pointer.api)
  const threshold0 = typeof pointer.threshold === 'number' ? pointer.threshold : DEFAULT_THRESHOLD
  const dark = ctx.theme !== 'light'

  const root = el('div', 'cd') as HTMLDivElement
  root.setAttribute('style', THEMES[dark ? 'dark' : 'light'])
  const style = el('style')
  style.textContent = CSS
  host.replaceChildren(style, root)

  // --- the chunk text, from a sibling ------------------------------------
  // The host resolves this against the opened file's directory and refuses to
  // leave it, so a pointer cannot name its way out of the package.
  const chunks: Record<string, ChunkText> = {}
  let chunkProblem = ''

  /** Fold a `{id: text}` or `{id: {text}}` map in, numbering by `meta.order`. */
  const absorb = (inner: unknown, order: string[] = []) => {
    for (const [id, value] of Object.entries((inner ?? {}) as Record<string, unknown>)) {
      chunks[id] =
        typeof value === 'string' ? { text: value } : ((value as ChunkText) ?? { text: '' })
    }
    order.forEach((id, index) => {
      if (chunks[id] && chunks[id].n === undefined) chunks[id].n = index + 1
    })
  }

  const store = isChunkStore(pointer)
  if (store) {
    // The opened file *is* the chunk store -- `chunks.json` itself. Its text is
    // already in `ctx.text`, so there is no sibling to fetch and nothing to
    // resolve: the cheapest case is also the one worth clicking.
    const raw = pointer as unknown as { chunks?: unknown; meta?: { order?: string[] } }
    absorb(raw.chunks ?? raw, raw.meta?.order ?? [])
  } else if (pointer.chunks && typeof pointer.chunks === 'object') {
    // A self-contained export carries the map itself under this key.
    absorb(pointer.chunks)
  } else {
    const chunkFile = (pointer.chunks as string | undefined) ?? DEFAULT_CHUNKS
    try {
      const raw = JSON.parse(await ctx.fetchAsset(chunkFile))
      absorb(raw?.chunks ?? raw, raw?.meta?.order ?? [])
    } catch (err) {
      chunkProblem = `no chunk text (${chunkFile}: ${(err as Error).message}) — showing ids only`
    }
  }

  // --- which task ---------------------------------------------------------
  // A pointer names one. A chunk store names none, so it is looked up from the
  // directory the file sits in -- which works because `history.task` is written
  // per package, and every package that has rows also has a `chunks.json`.
  const offline = Array.isArray(pointer.criteria)
  let task = pointer.task
  let siblings: { task: string; criteria: number }[] = []
  if (!task && !offline) {
    if (!store) {
      throw new Error(
        'not a criteria file: needs a "task" to query, or a "criteria" array, ' +
          'or the chunk map itself',
      )
    }
    const pkg = packageOf(ctx.path)
    if (!pkg) throw new Error(`cannot tell which package ${ctx.path} belongs to`)
    const rows = await ask(api, TASKS_SQL, [taskPattern(pkg)], 200)
    siblings = rows.map((r) => ({ task: String(r.task), criteria: Number(r.criteria) }))
    if (!siblings.length) {
      throw new Error(
        `no Laya or Jev criterion in experiments.history names the package "${pkg}". ` +
          'Nothing has been scored here yet, or the run recorded a different task name.',
      )
    }
    task = siblings[0].task
  }

  // --- the criteria -------------------------------------------------------
  let criteria: Criterion[]
  if (offline) {
    criteria = pointer.criteria as Criterion[]
  } else {
    const rows = await ask(api, CRITERIA_SQL, [task])
    criteria = rows.map((r) => ({
      name: String(r.name),
      kind: String(r.kind),
      model: String(r.model ?? ''),
      question: (r.question ?? {}) as Criterion['question'],
      metrics: (r.metrics ?? {}) as Record<string, unknown>,
      hits: typeof r.hits === 'number' ? r.hits : null,
      recorded_at: r.recorded_at ? String(r.recorded_at) : undefined,
      note: r.note ? String(r.note) : null,
    }))
  }
  if (!criteria.length) {
    throw new Error(`no criterion in experiments.state was scored by task "${task ?? ''}"`)
  }
  const sorted = ordered(criteria)

  // --- chrome -------------------------------------------------------------
  const head = el('div', 'cd-head')
  head.append(el('h1', 'cd-task', task ?? ctx.path.split('/').pop() ?? 'criteria'))
  head.append(el('span', 'cd-chip', `${sorted.length} criteria`))
  for (const model of [...new Set(sorted.map((c) => c.model))].sort()) {
    head.append(el('span', `cd-chip ${model.startsWith('jev') ? 'jev' : 'laya'}`, model))
  }
  head.append(el('span', 'cd-chip', offline ? 'from the file' : 'live'))

  // A package can hold more than one task -- a Laya round and a Jev replay of
  // it, say -- and 30 of the 81 with rows do. The most-scored one is shown, and
  // this switches between them by re-mounting on a pointer naming the other,
  // which needs no second query for the chunk text: it is passed along.
  let picker: HTMLSelectElement | null = null
  if (siblings.length > 1) {
    picker = document.createElement('select')
    picker.className = 'cd-picker'
    for (const sibling of siblings) {
      const option = document.createElement('option')
      option.value = sibling.task
      option.textContent = `${sibling.task}  (${sibling.criteria})`
      option.selected = sibling.task === task
      picker.append(option)
    }
    head.append(picker)
  }
  root.append(head)
  if (chunkProblem) root.append(el('div', 'cd-note', chunkProblem))

  const side = el('div', 'cd-side')
  const filter = document.createElement('input')
  filter.className = 'cd-filter'
  filter.type = 'search'
  filter.placeholder = `filter ${sorted.length} names…`
  const list = el('ul', 'cd-list')
  const items: { node: HTMLElement; criterion: Criterion }[] = []
  for (const criterion of sorted) {
    const item = el('li', 'cd-item') as HTMLLIElement
    item.setAttribute('role', 'option')
    item.setAttribute('aria-selected', 'false')
    item.dataset.name = criterion.name
    item.append(el('span', 'cd-name', criterion.name))
    const hits = hitsOf(criterion)
    item.append(el('span', 'cd-hits', hits === null ? '—' : String(hits)))
    list.append(item)
    items.push({ node: item, criterion })
  }
  side.append(filter, list)

  const main = el('div', 'cd-main')
  root.append(side, main)

  // --- the detail pane ----------------------------------------------------
  let token = 0
  const cache = new Map<string, Answer[]>()

  async function select(criterion: Criterion): Promise<void> {
    const mine = ++token
    for (const item of items) {
      item.node.setAttribute('aria-selected', String(item.criterion === criterion))
    }
    main.replaceChildren()

    const question = criterion.question ?? {}
    main.append(el('p', 'cd-q', question.instructions ?? '(no question recorded)'))
    main.append(
      el(
        'div',
        'cd-meta',
        [
          criterion.name,
          criterion.model,
          question.type ?? '',
          criterion.recorded_at ? `recorded ${criterion.recorded_at}` : '',
        ]
          .filter(Boolean)
          .join(' · '),
      ),
    )

    const metrics = criterion.metrics ?? {}
    const cards = el('div', 'cd-cards')
    for (const [key, label] of CARDS) {
      const card = el('div', 'cd-card')
      card.dataset.metric = key
      card.append(el('div', 'cd-card-v', show(metrics[key])))
      card.append(el('div', 'cd-card-k', label))
      cards.append(card)
    }
    main.append(cards)

    // Every other metric the run recorded, so nothing is hidden just because
    // it did not get a card.
    const rest = Object.keys(metrics)
      .filter((k) => !CARDS.some(([c]) => c === k))
      .sort()
    if (rest.length) {
      main.append(
        el('div', 'cd-rest', `also recorded: ${rest.map((k) => `${k}=${show(metrics[k])}`).join('  ')}`),
      )
    }

    const bar = el('div', 'cd-bar')
    const slider = document.createElement('input')
    slider.type = 'range'
    slider.min = '0'
    slider.max = '100'
    slider.step = '1'
    slider.value = String(Math.round(threshold0 * 100))
    const count = el('span', 'cd-count')
    const label = el('span', undefined, 'verdict ≥')
    const pct = el('span', 'cd-count', `${slider.value}%`)
    bar.append(label, pct, slider, count)
    main.append(bar)

    const holder = el('ul', 'cd-chunks')
    main.append(holder)

    let answers = criterion.answers ?? cache.get(criterion.name)
    if (!answers) {
      holder.append(el('li', 'cd-empty', 'loading chunks…'))
      try {
        const rows = await ask(api, ANSWERS_SQL, [task, criterion.name, criterion.kind])
        answers = rows.map((r) => ({
          chunk: String(r.chunk),
          verdict: r.verdict,
          score: typeof r.score === 'number' ? r.score : null,
          confidence: typeof r.confidence === 'number' ? r.confidence : null,
          probabilities: (r.probabilities ?? null) as Answer['probabilities'],
        }))
      } catch (err) {
        if (mine !== token) return
        holder.replaceChildren()
        main.append(el('div', 'cd-err', (err as Error).message))
        return
      }
      cache.set(criterion.name, answers)
    }
    if (mine !== token) return

    const draw = () => {
      const threshold = Number(slider.value) / 100
      pct.textContent = `${slider.value}%`
      const shown = applicable(answers!, threshold)
      count.textContent = `${shown.length} of ${answers!.length} chunks`
      holder.replaceChildren()
      if (!shown.length) {
        holder.append(
          el('li', 'cd-empty', `no chunk reaches ${slider.value}% — drag the slider down`),
        )
        return
      }
      for (const answer of shown) {
        const row = el('li', 'cd-chunk')
        row.dataset.chunk = answer.chunk
        const gutter = el('div')
        const value = strength(answer)
        // The live query returns `verdict` as text and the number in `score`;
        // an export puts the number in `verdict`. Either counts as numeric —
        // testing `typeof verdict` alone shows "0.7621" where 76% belongs.
        const numeric =
          typeof answer.score === 'number' || typeof answer.verdict === 'number'
        gutter.append(
          el('div', 'cd-score', numeric ? `${(value * 100).toFixed(0)}%`
            : String(answer.verdict ?? '?')),
        )
        if (typeof answer.confidence === 'number') {
          gutter.append(el('div', 'cd-conf', `conf ${answer.confidence.toFixed(2)}`))
        }
        const chunk = chunks[answer.chunk]
        gutter.append(el('div', 'cd-id', chunk?.n ? `#${chunk.n}` : answer.chunk.slice(0, 6)))

        const body = el('div')
        const text = el('p', 'cd-text', chunk?.text ?? `(no text for ${answer.chunk})`)
        body.append(text)
        if ((chunk?.text ?? '').length > 420) {
          const more = el('button', 'cd-more', 'show all')
          more.addEventListener('click', () => {
            const open = text.classList.toggle('open')
            more.textContent = open ? 'show less' : 'show all'
          })
          body.append(more)
        }
        row.append(gutter, body)
        holder.append(row)
      }
    }
    slider.addEventListener('input', draw)
    draw()
  }

  const onClick = (event: Event) => {
    const item = (event.target as HTMLElement).closest('.cd-item') as HTMLElement | null
    if (!item) return
    const found = items.find((i) => i.node === item)
    if (found) void select(found.criterion)
  }
  list.addEventListener('click', onClick)

  const onFilter = () => {
    const needle = filter.value.trim().toLowerCase()
    for (const item of items) {
      item.node.hidden = needle.length > 0 && !item.criterion.name.toLowerCase().includes(needle)
    }
  }
  filter.addEventListener('input', onFilter)

  // Switching task re-mounts on an explicit pointer for the chosen one, carrying
  // the chunk map across so the text is not fetched or parsed a second time.
  // Re-entering `mount` rather than rebuilding in place keeps one code path for
  // "draw a task", which is the path the tests exercise.
  let remounted: (() => void) | null = null
  const onPick = () => {
    if (!picker) return
    void mount(host, {
      ...ctx,
      text: JSON.stringify({
        format: 'experiments.criteria',
        task: picker.value,
        chunks,
        api: reached || api[0],
        threshold: threshold0,
      }),
    }).then((cleanup) => {
      remounted = cleanup
    })
  }
  picker?.addEventListener('change', onPick)

  // The first criterion is the one with the most hits, which is the one worth
  // looking at -- an empty pane on open teaches nobody anything.
  await select(sorted[0])

  return () => {
    list.removeEventListener('click', onClick)
    filter.removeEventListener('input', onFilter)
    picker?.removeEventListener('change', onPick)
    // If a pick re-mounted, that render owns the host now and has to be torn
    // down too -- otherwise its listeners outlive the widget.
    remounted?.()
    host.replaceChildren()
  }
}
