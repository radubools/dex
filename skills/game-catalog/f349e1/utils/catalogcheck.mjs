// Checks and lays out a story catalog with the game's own engine
// (@games/engine/story validateCatalog, layoutCatalog, layoutSection), so a
// catalog is valid here exactly when the game would accept it, and the
// preview shows the layout the game draws.
//   node catalogcheck.mjs <catalog.json> <game_root> [<known-assets.json>]
//     -> {ok, errors, warnings, stats, layout: {master, sections: {id: …}}}
// Python: story_catalog.validate_catalog(…, game_root) and preview_catalog call this.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [catalogPath, root, knownPath] = process.argv.slice(2);
if (!catalogPath || !root) {
  console.error('usage: node catalogcheck.mjs <catalog.json> <game_root> [<known-assets.json>]');
  process.exit(2);
}
const req = createRequire(join(resolve(root), 'package.json'));
const { validateCatalog, layoutCatalog, layoutSection } = await import(pathToFileURL(req.resolve('@games/engine/story')).href);
const cat = JSON.parse(readFileSync(catalogPath, 'utf8'));
const known = knownPath ? new Set(JSON.parse(readFileSync(knownPath, 'utf8'))) : null;
const out = validateCatalog(cat, { known });
const plain = (lay) => ({ pos: Object.fromEntries([...lay.pos].map(([id, p]) => [id, [p.x, p.y]])), edges: lay.edges, width: lay.width, height: lay.height });
let layout = null;
if (out.ok) {
  layout = { master: plain(layoutCatalog(cat)), sections: Object.fromEntries(cat.sections.map((s) => [s.id, plain(layoutSection(s))])) };
}
console.log(JSON.stringify({ ok: out.ok, errors: out.errors, warnings: out.warnings, stats: out.stats, layout }));
