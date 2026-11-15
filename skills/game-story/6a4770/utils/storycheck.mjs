// Validates a story with the game's own engine (@games/engine/story
// validateStory), so a story is valid here exactly when the game would accept
// it: schema 1 or 2, every field, expressions, branches, reads.
// The game supplies what it handles: its actions (src/actions/index.js
// ACTIONS), the highlight ids it makes itself (data points p0, p1, …) and its
// `$` reads (src/reads.js READS, when it has them), and the stories its
// catalog plays, which a [[story/…]] link in the story's text must name.
//   node storycheck.mjs <story.json> <game_root> [<known-assets.json>]   -> {ok, errors, warnings}
// Python: story.validate_story(…) calls this for schema 2 stories (and for schema 1 when a game is given).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [storyPath, root, knownPath] = process.argv.slice(2);
if (!storyPath || !root) {
  console.error('usage: node storycheck.mjs <story.json> <game_root> [<known-assets.json>]');
  process.exit(2);
}
const gameRoot = resolve(root);
const req = createRequire(join(gameRoot, 'package.json'));
const { validateStory, catalogStories } = await import(pathToFileURL(req.resolve('@games/engine/story')).href);
const src = (rel) => import(pathToFileURL(join(gameRoot, 'src', rel)).href);
const { ACTIONS } = await src('actions/index.js');
let reads = null, entityPatterns = [];
try {
  const m = await src('reads.js');
  reads = m.READS?.map((r) => r.path) ?? null;
  // Optional: the ids the game's actions put on the stage (ball, q, centroid-1, …), as regex sources.
  entityPatterns = (m.ENTITIES || []).map((e) => new RegExp(typeof e === 'string' ? e : e.pattern));
} catch { reads = null; }
const story = JSON.parse(readFileSync(storyPath, 'utf8'));
const knownAssets = knownPath ? JSON.parse(readFileSync(knownPath, 'utf8')) : null;
// The stories a [[story/…]] link may name (engine design 25): every one the
// game's catalog plays (installed or still a placeholder), the stories it has
// installed, and the project's own story packages (in knownAssets).
let stories = null;
try {
  const assetsDir = join(gameRoot, 'public', 'assets');
  const index = JSON.parse(readFileSync(join(assetsDir, 'index.json'), 'utf8'));
  const names = new Set(Object.keys(index.assets || {}).filter((k) => k.startsWith('story/')));
  for (const k of knownAssets || []) if (String(k).startsWith('story/')) names.add(k);
  for (const [key, entry] of Object.entries(index.assets || {})) {
    if (entry?.kind !== 'catalog') continue;
    for (const f of (entry.files || []).filter((x) => x.endsWith('.json') && x !== 'manifest.json')) {
      try { for (const n of catalogStories(JSON.parse(readFileSync(join(assetsDir, key, f), 'utf8')))) names.add(n); } catch { /* not a catalog file */ }
    }
  }
  stories = [...names];
} catch { stories = null; }
const out = validateStory(story, { knownAssets, actions: Object.keys(ACTIONS), entities: (id) => /^p\d+$/.test(id) || entityPatterns.some((re) => re.test(id)), reads, stories });
console.log(JSON.stringify({ ok: out.ok, errors: out.errors, warnings: out.warnings || [], reads: !!reads }));
