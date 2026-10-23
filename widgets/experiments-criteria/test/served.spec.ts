import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

/**
 * Does dex serve the bundle we just built?
 *
 * This is the check whose absence cost the most. `dex.widgets.available()` keys
 * every built widget by its **bare name** across every skill version on disk,
 * last one wins, and `all_skills()` walks versions in lexicographic order. Eight
 * superseded versions of `postgres-experiments-state` each shipped a widget
 * called `criteria-dashboard`, so `e7e83f` beat the pinned `e6e6af` and the
 * project was served a bundle five revisions old — with the scrolling list that
 * had supposedly been replaced, and a re-mount bug that had supposedly been
 * fixed. Every test passed the whole time, because every test loaded the bundle
 * by path and never asked dex what it would actually hand the browser.
 *
 * So this one asks dex, through its own resolver, and compares.
 */

const REPO = fileURLToPath(new URL('../../../', import.meta.url))
const PYTHON = join(REPO, '.venv/bin/python3')
const NAME = 'experiments-criteria'
const TRIGGER = 'change-order-risk-criteria-doc-5/chunks.json'

/** What `dex.widgets.resolve()` would return for a real file in this project. */
function resolved(): { name: string; version: string; candidates: string[] } {
  const out = execFileSync(
    PYTHON,
    [
      '-c',
      `import sys, json; sys.path.insert(0, 'src')
from pathlib import Path
from dex import widgets
w = widgets.resolve(Path('assets'), Path('.'), 'construction', ${JSON.stringify(TRIGGER)})
cands = sorted(x.skill_version for x in widgets.available(Path('.'))
               if x.name == ${JSON.stringify(NAME)} and x.built)
print(json.dumps({'name': w and w.name, 'version': w and w.skill_version, 'candidates': cands}))`,
    ],
    { cwd: REPO, encoding: 'utf8' },
  )
  return JSON.parse(out.trim().split('\n').pop()!)
}

test('dex resolves this project’s trigger file to this widget', () => {
  const r = resolved()
  expect(r.name, `resolve(${TRIGGER}) returned ${JSON.stringify(r)}`).toBe(NAME)
})

test('exactly one version on disk carries this widget name', () => {
  const r = resolved()
  // More than one and the winner is decided by lexicographic version order,
  // which has nothing to do with which is current. That is the whole failure.
  expect(
    r.candidates,
    `several versions ship a widget called "${NAME}" — dex will serve ` +
      `whichever sorts last (${r.candidates.join(', ')}), not the enabled one. ` +
      'Delete the superseded skills/postgres-experiments-state/<hash>/ directories.',
  ).toHaveLength(1)
})

test('the bundle dex would serve is the one built from this source', () => {
  const r = resolved()
  const served = readFileSync(
    join(REPO, `skills/postgres-experiments-state/${r.version}/widgets/${NAME}/dist/index.js`),
    'utf8',
  )
  // The bundle under test: the working draft if there is one, else the version
  // the project has enabled. This directory holds only tests -- the bundle
  // itself lives inside the skill, which is where `build.mjs` writes it.
  const pinned = JSON.parse(
    readFileSync(join(REPO, 'assets/construction/skills.json'), 'utf8'),
  ).skills['postgres-experiments-state'] as string
  const candidate = ['draft', pinned]
    .map((v) => join(REPO, `skills/postgres-experiments-state/${v}/widgets/${NAME}/dist/index.js`))
    .find((f) => existsSync(f))
  expect(candidate, 'nothing built to compare against').toBeTruthy()
  const built = readFileSync(candidate!, 'utf8')
  expect(served.length, 'dex would serve a different build than the one under test').toBe(
    built.length,
  )
  expect(served).toBe(built)

  // And it is the current markup, not a revision that predates the redesign.
  expect(served).toContain('cd-controls')
  expect(served).not.toContain('cd-item')
})
