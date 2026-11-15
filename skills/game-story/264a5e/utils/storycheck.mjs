// Validates a story with the game's own engine (@games/engine/story
// validateStory), so a story is valid here exactly when the game would accept
// it: schema 1 or 2, every field, expressions, branches, reads.
// The game supplies what it handles: its actions (src/actions/index.js
// ACTIONS), the highlight ids it makes itself (data points p0, p1, …) and its
// `$` reads (src/reads.js READS, when it has them).
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
const { validateStory } = await import(pathToFileURL(req.resolve('@games/engine/story')).href);
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
const out = validateStory(story, { knownAssets, actions: Object.keys(ACTIONS), entities: (id) => /^p\d+$/.test(id) || entityPatterns.some((re) => re.test(id)), reads });
console.log(JSON.stringify({ ok: out.ok, errors: out.errors, warnings: out.warnings || [], reads: !!reads }));
