// Runs Model Workshop's own dataset generators and fits and prints the numbers
// as JSON, so workshop_math.py can be checked against the game.
//   node workshop_numbers.mjs <game_root> '<json request list>'
// A request: {dataset, n, noise, seed, classes?} or {points: [[x, z, cls, y], ...]},
// plus optional fit: 'line' | 'plane' | 'poly' (with degree).
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [root, body] = process.argv.slice(2);
const ml = (f) => import(pathToFileURL(join(root, 'src', 'ml', f)).href);
const { makeDataset } = await ml('datasets.js');
const { leastSquares, leastSquares2, polyFit } = await ml('linear.js');

const out = JSON.parse(body || '[]').map((r) => {
  const pts = r.points
    ? r.points.map(([x, z, cls, y], i) => ({ id: `p${i}`, x, z, cls, y }))
    : makeDataset(r.dataset, { n: r.n, noise: r.noise, seed: r.seed, classes: r.classes ?? 2 });
  const res = { points: pts.map((p) => [p.x, p.z, p.y]) };
  if (r.fit === 'line') res.fit = leastSquares(pts);
  if (r.fit === 'plane') res.fit = leastSquares2(pts);
  if (r.fit === 'poly') res.fit = polyFit(pts, r.degree);
  return res;
});
console.log(JSON.stringify(out));
