import { useEffect, useMemo, useState } from 'react'
import { listPackages } from '../api'
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
  onOpen,
  openPath,
}: {
  project: string
  /** Tag changes seen on the event stream since load, by package slug. */
  liveTags?: Record<string, string[]>
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

  useEffect(() => {
    let live = true
    setPackages(null)
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
      (packages ?? []).map((entry) =>
        liveTags && liveTags[entry.slug] ? { ...entry, tags: liveTags[entry.slug] } : entry,
      ),
    [packages, liveTags],
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

  if (packages.length === 0) {
    return (
      <div className="empty" style={{ padding: 16 }}>
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

  return (
    <div className="library">
      <div className="library-search">
        <input
          value={filter}
          placeholder={`Find among ${packages.length} packages…`}
          onChange={(e) => setFilter(e.target.value)}
        />
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
          {shown.map((entry) => (
            <button
              key={entry.slug}
              className={`asset-row ${openPath === entry.path ? 'open' : ''}`}
              onClick={() =>
                onOpen({ kind: 'package', path: entry.path, title: entry.displayName })
              }
            >
              <span className="asset-icon">📦</span>
              {/* Name and tags share a column: a tag list beside the name
                  squeezes it until it wraps mid-word. */}
              <span className="asset-main">
                <span className="asset-name">{entry.displayName}</span>
                {entry.tags.length > 0 && (
                  <span className="asset-tags">
                    {entry.tags.map((tag) => (
                      <span key={tag} className="row-tag">{split(tag).value}</span>
                    ))}
                  </span>
                )}
              </span>
              <span className="asset-size">
                {entry.files} file{entry.files === 1 ? '' : 's'}
              </span>
            </button>
          ))}
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
