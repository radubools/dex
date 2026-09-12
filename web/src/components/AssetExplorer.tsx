import { useCallback, useEffect, useState } from 'react'
import { readAsset } from '../api'
import type { AssetEntry, AssetKind } from '../types'

/** Assets are grouped by what they are for, not by filename order. */
const GROUPS: { label: string; kinds: AssetKind[]; match?: RegExp }[] = [
  // Narration belongs with the animation it was timed against.
  { label: 'Animations', kinds: ['animation', 'video', 'audio', 'captions'] },
  // The manim source belongs with what it renders, not with the solutions.
  { label: 'Animations', kinds: ['code'], match: /^animation/ },
  { label: 'Explanation', kinds: ['markdown'] },
  { label: 'Tests', kinds: ['code'], match: /^test_|_test\.py$/ },
  { label: 'Solutions', kinds: ['code'] },
  { label: 'Manifest', kinds: ['manifest'] },
  { label: 'Other', kinds: ['image', 'file'] },
]

const ICONS: Record<AssetKind, string> = {
  animation: '🎞', video: '🎬', markdown: '📘', code: '🐍',
  manifest: '📋', image: '🖼', audio: '🔊', captions: '💬', file: '📄',
}

/**
 * The generated package for one task. Refreshes whenever the watcher reports a
 * new asset, so files appear as the agent writes them.
 */
/** Internal metadata dex writes beside an animation, not something to open. */
export function AssetExplorer({
  slug,
  assets,
  onOpen,
  openPath,
}: {
  slug: string
  /** Paths seen on the event stream; used purely as a refresh trigger. */
  assets: string[]
  onOpen: (path: string) => void
  openPath?: string
}) {
  const [entries, setEntries] = useState<AssetEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    // Every file beneath the task directory, not just the ones sitting directly
    // in it: a task is free to organise its output into subdirectories, and a
    // flat listing showed nothing at all for one that did.
    readAsset(slug, '**/*')
      .then((asset) => {
        setError(null)
        setEntries(
          asset.kind === 'dir'
            ? asset.entries.filter((e) => !e.dir && !e.name.endsWith('.sections.json'))
            : [],
        )
      })
      .catch((err: Error) => {
        // Before the agent's first write the directory may not exist yet.
        setEntries([])
        setError(err.message.startsWith('404') ? null : err.message)
      })
  }, [slug])

  useEffect(refresh, [refresh, assets.length])

  if (error) return <p className="error">{error}</p>
  if (entries.length === 0) return <p className="muted small">No files generated yet.</p>

  // Each file lands in the first group that claims it; groups sharing a label
  // (animations and their source) are merged.
  const claimed = new Set<string>()
  const groups: { label: string; members: AssetEntry[] }[] = []
  for (const group of GROUPS) {
    const members = entries.filter((entry) => {
      if (claimed.has(entry.name) || !group.kinds.includes(entry.kind)) return false
      if (group.match && !group.match.test(entry.name)) return false
      claimed.add(entry.name)
      return true
    })
    if (members.length === 0) continue
    const existing = groups.find((g) => g.label === group.label)
    if (existing) existing.members.push(...members)
    else groups.push({ label: group.label, members })
  }

  return (
    <div className="explorer">
      {groups.map((group) => (
        <div className="explorer-group" key={group.label}>
          <div className="explorer-label">{group.label}</div>
          {group.members.map((entry) => {
            // Globbed names are relative to the assets root already.
            const path = entry.name
            const label = entry.name.startsWith(`${slug}/`)
              ? entry.name.slice(slug.length + 1)
              : entry.name
            return (
              <button
                key={entry.name}
                className={`asset-row ${openPath === path ? 'open' : ''}`}
                onClick={() => onOpen(path)}
              >
                <span className="asset-icon">{ICONS[entry.kind] ?? '📄'}</span>
                <span className="asset-name">{label}</span>
                <span className="asset-size">{formatBytes(entry.bytes)}</span>
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
