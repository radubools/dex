// plot_check.py's helper: compiles plot.function formulas with the engine's own compiler.
// node plot_check.mjs <engine plot index.js> '<json [{fn, at: [{var: value}]}]>'
import { pathToFileURL } from 'node:url';

const [, , enginePlot, json] = process.argv;
const { compileMath, mathError } = await import(pathToFileURL(enginePlot).href);
const out = JSON.parse(json).map(({ fn, at = [] }) => {
  const error = mathError(fn);
  if (error) return { fn, error: String(error) };
  const f = compileMath(fn);
  return { fn, vars: f.vars, values: at.map((env) => { const v = f(env); return Number.isFinite(v) ? v : String(v); }) };
});
console.log(JSON.stringify(out));
