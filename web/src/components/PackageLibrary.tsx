import { useEffect, useMemo, useState } from 'react'
import { listIdeas, listPackages } from '../api'
import type { PackageEntry } from '../types'
import type { ViewerTarget } from './Viewer'

/**
 * A tag is written `group:value` in the manifest, so the Library learns its
 * own vocabulary: what groups exist, and what values are in them, come from
 * the tags themselves. A tag with no colon is its own anonymous group.
 */
function split(tag: string): { group: string; value: string } {
  const at = tag.indexOf(':')
  return at === -1
    ? { group: '', value: tag }
    : { group: tag.slice(0, at), value: tag.slice(at + 1) }
}

/**
 * A package's tags, each said once. A plain tag goes when a grouped one says
 * the same (`story` beside `type:story`, which write_story writes on every
 * story), so it isn't a second filter for the same thing. Manifests keep theirs.
 */
function distinct(tags: string[]): string[] {
  const grouped = new Set(tags.filter((t) => t.includes(':')).map((t) => split(t).value))
  return [...new Set(tags)].filter((t) => t.includes(':') || !grouped.has(t))
}

/** What a row shows: only values, so two groups with one value read as one. */
const rowTags = (tags: string[]) => [...new Set(tags.map((t) => split(t).value))]

/** How long ago, in the fewest words: the Library is sorted by this. */
function ago(seconds: number): string {
  const minutes = Math.floor((Date.now() / 1000 - seconds) / 60)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h ago`
  return `${Math.floor(minutes / (60 * 24))}d ago`
}

/** The folder for items with no value in the group. Can't be a tag's value. */
const UNTAGGED = '\0untagged'

/** Arrange by what doesn't use `group:value`. A colon can't be in a group's name. */
const NO_GROUP = ':none'

const arrangeKey = (project: string) => `dex.library.arrange.${project}`

/** A viewer's convenience, so it lives in the browser, and may not be there. */
function remembered(project: string): string {
  try {
    return localStorage.getItem(arrangeKey(project)) ?? ''
  } catch {
    return ''
  }
}

function remember(project: string, group: string) {
  try {
    if (group) localStorage.setItem(arrangeKey(project), group)
    else localStorage.removeItem(arrangeKey(project))
  } catch {
    // Not kept, then: it still works until the page closes.
  }
}

/** A folder per value, in natural order so `2c` comes before `10c`; items with none last. */
function foldersOf(entries: PackageEntry[], valuesOf: (e: PackageEntry) => string[], group: string) {
  const counts = new Map<string, number>()
  let untagged = 0
  for (const entry of entries) {
    const values = valuesOf(entry)
    if (values.length === 0) untagged += 1
    for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  const folders = [...counts]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([value, count]) => ({ value, name: value, count }))
  return untagged ? [...folders, { value: UNTAGGED, name: `No ${group}`, count: untagged }] : folders
}

type Group = { name: string; tags: { tag: string; value: string; count: number }[] }

/**
 * Every package the project has generated, searchable by name and filterable
 * by the tags in each package's manifest. Picking one opens its explanation in
 * the side panel; the rest of its files are a click away from the task that
 * made it.
 */
export function PackageLibrary({
  project,
  liveTags,
  liveSizes,
  onOpen,
  openPath,
}: {
  project: string
  /** Tag changes seen on the event stream since load, by package slug. */
  liveTags?: Record<string, string[]>
  /** The rollup's size tags for packages being written now. They replace what was listed. */
  liveSizes?: Record<string, string[]>
  onOpen: (target: ViewerTarget) => void
  openPath?: string
}) {
  const [packages, setPackages] = useState<PackageEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  // Closed by default: the filters are worth a row of chrome only once someone
  // is filtering, and the list is what the reader came for.
  const [showFilters, setShowFilters] = useState(false)
  // Ideation's log and proposals are a side of their own, not assets.
  const [side, setSide] = useState<'assets' | 'ideas'>('assets')
  const [ideas, setIdeas] = useState<{ log: string | null; ideas: PackageEntry[] }>({ log: null, ideas: [] })
  const [ideaFilter, setIdeaFilter] = useState('')
  /** The tag group the list is arranged by, kept per project; '' is All. */
  const [arrange, setArrange] = useState(() => remembered(project))
  /** The value open under it, or null for its folders. */
  const [folder, setFolder] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setPackages(null)
    setSide('assets')
    setIdeaFilter('')
    setIdeas({ log: null, ideas: [] })
    listIdeas(project).then((got) => live && setIdeas(got)).catch(() => {})
    // A tag chosen in one project means nothing in the next.
    setPicked([])
    setShowFilters(false)
    listPackages(project)
      .then((got) => live && setPackages(got.packages))
      .catch((err: Error) => live && setError(err.message))
    return () => { live = false }
  }, [project])

  // The snapshot is what the server had when this opened; the stream is what
  // has happened since. A task tagging packages right now shows up here.
  const entries = useMemo(
    () =>
      (packages ?? []).map((entry) => {
        const listed = liveTags?.[entry.slug] ? [...liveTags[entry.slug], ...(entry.earnedTags ?? [])] : entry.tags
        const sizes = liveSizes?.[entry.slug]
        return {
          ...entry,
          tags: distinct(sizes ? [...listed.filter((t) => !/^(code|asset)-lines:/.test(t)), ...sizes] : listed),
        }
      }),
    [packages, liveTags, liveSizes],
  )

  const groups: Group[] = useMemo(() => {
    const counts = new Map<string, number>()
    for (const entry of entries) {
      for (const tag of entry.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1)
    }
    const byGroup = new Map<string, Group>()
    for (const [tag, count] of counts) {
      const { group, value } = split(tag)
      if (!byGroup.has(group)) byGroup.set(group, { name: group, tags: [] })
      byGroup.get(group)!.tags.push({ tag, value, count })
    }
    for (const group of byGroup.values()) {
      group.tags.sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    }
    // Smallest vocabulary first: a group with three values divides the library
    // more usefully than one with thirty, and it keeps the pill rows stable as
    // packages are tagged. Un-grouped tags last, having no heading to sit under.
    return [...byGroup.values()].sort(
      (a, b) =>
        Number(a.name === '') - Number(b.name === '') ||
        a.tags.length - b.tags.length ||
        a.name.localeCompare(b.name),
    )
  }, [entries])

  const toggle = (tag: string) =>
    setPicked((current) =>
      current.includes(tag) ? current.filter((t) => t !== tag) : [...current, tag],
    )

  if (error) return <p className="error" style={{ padding: 16 }}>{error}</p>
  if (packages === null) return <p className="muted" style={{ padding: 16 }}>Loading library…</p>

  const hasIdeas = ideas.log !== null || ideas.ideas.length > 0
  const sides = hasIdeas && (
    <nav className="tabs library-sides">
      <button className={side === 'assets' ? 'on' : ''} onClick={() => setSide('assets')}>Assets</button>
      <button className={side === 'ideas' ? 'on' : ''} onClick={() => setSide('ideas')}>
        Ideas{ideas.ideas.length > 0 && <span className="tag-count">{ideas.ideas.length}</span>}
      </button>
    </nav>
  )

  if (side === 'ideas') {
    const words = ideaFilter.trim().toLowerCase().split(/\s+/).filter(Boolean)
    // Every word has to match, in the name or a tag, so `catalog 2c` narrows.
    const shownIdeas = ideas.ideas.filter((entry) => {
      const text = [entry.displayName, entry.slug, ...entry.tags].join(' ').toLowerCase()
      return words.every((word) => text.includes(word))
    })
    return (
      <div className="library">
        {sides}
        <div className="library-search">
          <input
            value={ideaFilter}
            placeholder={`Find among ${ideas.ideas.length} ideas…`}
            aria-label="Find ideas"
            onChange={(e) => setIdeaFilter(e.target.value)}
          />
        </div>
        <div className="explorer">
          <div className="explorer-group">
            {ideas.log && words.length === 0 && (
              <button
                className={`asset-row ${openPath === ideas.log ? 'open' : ''}`}
                onClick={() => onOpen({ kind: 'asset', path: ideas.log! })}
              >
                <span className="asset-icon">📓</span>
                <span className="asset-main"><span className="asset-name">Ideas log</span></span>
              </button>
            )}
            {shownIdeas.map((entry) => (
              <button
                key={entry.slug}
                className={`asset-row ${openPath === entry.path ? 'open' : ''}`}
                onClick={() =>
                  onOpen({ kind: 'package', path: entry.path, title: entry.displayName, taskId: entry.taskId ?? undefined })
                }
              >
                <span className="asset-icon">💡</span>
                <span className="asset-main">
                  <span className="asset-name">{entry.displayName}</span>
                  <span className="asset-tags">
                    {rowTags(distinct(entry.tags)).map((value) => <span key={value} className="row-tag">{value}</span>)}
                  </span>
                </span>
                {entry.lastActivity ? <span className="asset-size">{ago(entry.lastActivity)}</span> : null}
              </button>
            ))}
            {words.length > 0 && shownIdeas.length === 0 && (
              <p className="muted small">No ideas match “{ideaFilter}”.</p>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (packages.length === 0) {
    return (
      <div className="empty" style={{ padding: 16 }}>
        {sides}
        <h2>Nothing generated yet</h2>
        <p className="muted">
          Ask for something in the chat and it will appear here once the task
          finishes.
        </p>
      </div>
    )
  }

  const needle = filter.trim().toLowerCase()
  // Picking two values from one group means "either" — two difficulties is a
  // range, not a contradiction. Picking across groups means "both". So the
  // rule is OR within a group, AND across groups, and which tags share a group
  // is decided by the tags, not by this file.
  const wanted = new Map<string, string[]>()
  for (const tag of picked) {
    const { group } = split(tag)
    wanted.set(group, [...(wanted.get(group) ?? []), tag])
  }
  const shown = entries.filter((p) => {
    if (
      needle &&
      !p.displayName.toLowerCase().includes(needle) &&
      !p.slug.includes(needle.replace(/\s+/g, '-'))
    ) {
      return false
    }
    for (const [, tags] of wanted) {
      if (!tags.some((tag) => p.tags.includes(tag))) return false
    }
    return true
  })

  // The saved group may be gone from this project: then it is All.
  const arranged = arrange === NO_GROUP || groups.some((g) => g.name === arrange && g.name) ? arrange : ''
  const bare = arranged === NO_GROUP
  const groupName = bare ? 'tag' : arranged
  const valueIn = (entry: PackageEntry) =>
    bare
      ? entry.tags.filter((t) => !t.includes(':'))
      : entry.tags.filter((t) => t.startsWith(`${arranged}:`)).map((t) => split(t).value)
  // No group keeps only what stays out of the convention: a plain tag, or no tag at all.
  const pool = bare ? shown.filter((p) => p.tags.length === 0 || valueIn(p).length > 0) : shown
  const folders = arranged && folder === null ? foldersOf(pool, valueIn, groupName) : null
  const listed = arranged && folder !== null
    ? pool.filter((p) => (folder === UNTAGGED ? valueIn(p).length === 0 : valueIn(p).includes(folder)))
    : pool

  return (
    <div className="library">
      {sides}
      <div className="library-search">
        <input
          value={filter}
          placeholder={`Find among ${packages.length} packages…`}
          onChange={(e) => setFilter(e.target.value)}
        />
        {groups.length > 0 && (
          <label className="library-arrange">
            Arrange
            <select
              value={arranged}
              onChange={(e) => {
                setArrange(e.target.value)
                setFolder(null)
                remember(project, e.target.value)
              }}
            >
              <option value="">All</option>
              <option value={NO_GROUP}>No group</option>
              {groups.filter((g) => g.name).map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
            </select>
          </label>
        )}
        {groups.length > 0 && (
          <button
            type="button"
            className={`filter-toggle ${picked.length > 0 ? 'on' : ''}`}
            aria-expanded={showFilters}
            onClick={() => setShowFilters((open) => !open)}
          >
            Filters
            {/* A filter narrowing the list from behind a closed drawer would be
                invisible state, so the count shows either way. */}
            {picked.length > 0 && <span className="tag-count">{picked.length}</span>}
            <span className="chevron">{showFilters ? '▴' : '▾'}</span>
          </button>
        )}
        {picked.length > 0 && (
          <button type="button" className="filter-clear" onClick={() => setPicked([])}>
            Clear
          </button>
        )}
      </div>
      {groups.length > 0 && showFilters && (
        <div className="tag-groups">
          {groups.map((group) => (
            <div className="tag-group" key={group.name || 'ungrouped'}>
              {group.name && <span className="tag-group-name">{group.name}</span>}
              <div className="tag-pills">
                {group.tags.map(({ tag, value, count }) => (
                  <button
                    key={tag}
                    type="button"
                    className={`tag-pill ${picked.includes(tag) ? 'on' : ''}`}
                    aria-pressed={picked.includes(tag)}
                    onClick={() => toggle(tag)}
                  >
                    {value}
                    <span className="tag-count">{count}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="explorer">
        <div className="explorer-group">
          {folder !== null && (
            <button className="asset-row folder-back" onClick={() => setFolder(null)}>
              <span className="asset-icon">‹</span>
              <span className="asset-main">
                <span className="asset-name">{bare ? 'No group' : arranged} / {folder === UNTAGGED ? `no ${groupName}` : folder}</span>
              </span>
            </button>
          )}
          {folders?.map(({ value, name, count }) => (
            <button key={value} className="asset-row folder" onClick={() => setFolder(value)}>
              <span className="asset-icon">📁</span>
              <span className="asset-main"><span className="asset-name">{name}</span></span>
              <span className="asset-size">{count} item{count === 1 ? '' : 's'}</span>
            </button>
          ))}
          {!folders && listed.map((entry) => (
            <button
              key={entry.slug}
              className={`asset-row ${openPath === entry.path ? 'open' : ''}`}
              onClick={() =>
                onOpen({
                  kind: 'package',
                  path: entry.path,
                  title: entry.displayName,
                  taskId: entry.taskId ?? undefined,
                })
              }
            >
              <span className="asset-icon">📦</span>
              {/* Name and tags share a column: a tag list beside the name
                  squeezes it until it wraps mid-word. */}
              <span className="asset-main">
                <span className="asset-name">{entry.displayName}</span>
                {entry.tags.length > 0 && (
                  <span className="asset-tags">
                    {rowTags(entry.tags).map((value) => (
                      <span key={value} className="row-tag">{value}</span>
                    ))}
                  </span>
                )}
              </span>
              <span className="asset-size">
                {entry.lastActivity ? `${ago(entry.lastActivity)} · ` : ''}
                {entry.files} file{entry.files === 1 ? '' : 's'}
              </span>
            </button>
          ))}
          {shown.length > 0 && pool.length === 0 && (
            <p className="muted small">Every item here uses the group:value convention.</p>
          )}
          {shown.length === 0 && (
            <p className="muted small">
              Nothing matches{needle && ` “${filter}”`}
              {needle && picked.length > 0 && ' with'}
              {picked.length > 0 && ` ${picked.map((t) => split(t).value).join(' + ')}`}.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
