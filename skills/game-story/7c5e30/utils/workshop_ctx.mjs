// An importable, headless Model Workshop lesson context: run the game's own action handlers one at a time to compute the numbers a story quotes.
//
// Unlike storysim.mjs (a CLI that plays a whole story), this is a module:
//   import { makeCtx, act, floorCells, gameModule } from '../utils/workshop_ctx.mjs';
//   const ctx = makeCtx('decision-tree');
//   await act(ctx, 'data.scatter', { dataset: 'diagonal', n: 60, seed: 4 });
//   await act(ctx, 'tree.grow', { depth: 8 });
//   const { predictTree } = await gameModule('src/ml/tree.js');
// The game is GAME_ROOT (default ~/games/model-workshop); its engine is the
// sibling `engine` directory. The context mirrors lesson.js makeContext in
// story mode: the station's seeded RNG, ctx.refresh calls only the model's
// refresh, and act() runs the handler then general.js runLive, as
// Lesson.runAction does. The stage and bench are stand-ins: nothing renders,
// tweens land at once.
import { pathToFileURL } from 'node:url';
import { homedir } from 'node:os';
import { resolve } from 'node:path';

const GAME = resolve(process.env.GAME_ROOT || resolve(homedir(), 'games', 'model-workshop'));
const ENGINE = resolve(GAME, '..', 'engine');

/** A module of the game by its path from the game root (e.g. 'src/ml/tree.js'). */
export const gameModule = (rel) => import(pathToFileURL(resolve(GAME, rel)).href);

const [{ ACTIONS }, { Dataset }, { ALGORITHM }, { makeRng }, general, knn] = await Promise.all([
  gameModule('src/actions/index.js'), gameModule('src/dataset.js'), gameModule('src/config.js'),
  import(pathToFileURL(resolve(ENGINE, 'src/core/rng.js')).href),
  gameModule('src/actions/general.js'), gameModule('src/ml/knn.js'),
]);

export { ACTIONS, makeRng };
/** The game's floor grid: res × res cell centres over the data floor (src/ml/knn.js gridCells). */
export const { gridCells } = knn;

class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.own = ''; this.children = []; this.dataset = {}; this.isConnected = true; this.parentNode = null; this.classList = { add() {}, remove() {}, toggle() {} }; }
  append(...cs) { for (const c of cs) { c.parentNode = this; this.children.push(c); } }
  after(e) { const p = this.parentNode; if (p) { e.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, e); } }
  remove() { const p = this.parentNode; if (p) p.children = p.children.filter((c) => c !== this); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  set innerHTML(_) { this.children = []; this.own = ''; }
  set textContent(t) { this.children = []; this.own = String(t); }
  get textContent() { return this.own + this.children.map((c) => c.textContent).join(''); }
}
const doc = { createElement: (t) => new El(t) };

function makeStage() {
  const entities = new Map();
  const labels = new Map();
  const make = (id, asset, o = {}) => {
    const at = o.at || [0, 0];
    const e = {
      id, asset, alive: true, data: {}, clip: o.clip, color: o.color, alpha: o.alpha ?? 1, scale: o.scale ?? 1,
      pos: [at[0], o.y ?? 0, at[1]],
      get at() { return [this.pos[0], this.pos[2]]; },
      setPos(x, y, z) { this.pos = [x, y, z]; }, setScale(s) { this.scale = s; }, setRotation() {},
      setColor(c) { this.color = c; }, setAlpha(a) { this.alpha = a; }, setVisible() {}, setGlow() {},
      async scaleTo(s) { this.scale = s; }, async moveTo(p) { this.pos = [...p]; }, async fadeTo(a) { this.alpha = a; },
      async turnTo() {}, async play(c) { this.clip = c; },
    };
    entities.set(id, e);
    return e;
  };
  const st = {
    entities, labels, origin: { x: 0, z: 0 }, time: 0,
    spawn: (id, asset, o) => make(id, asset, o),
    get: (id) => entities.get(id), has: (id) => entities.has(id),
    remove(id) { const e = entities.get(id); if (e) e.alive = false; entities.delete(id); labels.delete(id); },
    removePrefix(p) { for (const id of [...entities.keys()]) if (id.startsWith(p)) st.remove(id); for (const k of [...labels.keys()]) if (k.startsWith(p)) labels.delete(k); },
    all: (p) => [...entities.values()].filter((e) => e.id.startsWith(p)),
    label(id, text) { if (text === null || text === undefined) labels.delete(id); else labels.set(id, String(text)); },
    segment(id, a, b, o = {}) { const e = entities.get(id) || make(id, o.asset || 'environment/line-segment', { at: [a[0], a[2]], y: a[1] }); e.from = a; e.to = b; return e; },
    async segmentTo(id, a, b, _d, o) { return st.segment(id, a, b, o); },
    async tween(_d, fn) { fn(1); }, async wait() {}, finishTweens() {},
  };
  return st;
}

function makeBench() {
  const b = {
    doc, root: new El('div'), sections: {}, items: new Map(), meters: {}, charts: {}, sliders: {}, tools: [], plaque: null, cls: 0,
    meter(id, o) { b.meters[id] = { label: o.label, value: o.value, shown: o.fmt ? o.fmt(o.value) : String(o.value) }; b.items.set(`meter:${id}`, true); },
    chart(id, o) { b.charts[id] = JSON.parse(JSON.stringify(o)); b.items.set(`chart:${id}`, true); },
    formula(t) { b.plaque = t; }, slider(p, o) { b.sliders[p] = o.value; }, setSlider(p, v) { b.sliders[p] = v; },
    addTool(t) { b.tools.push(t); }, classPicker() {},
    hide(id) { for (const k of [id, `meter:${id}`, `chart:${id}`]) b.items.delete(k); delete b.meters[id]; delete b.charts[id]; },
    changed() {}, clear() {},
  };
  return b;
}

/** A story-mode lesson context for a station (stub stage and bench, the station's seeded RNG), as lesson.js builds it. */
export function makeCtx(algorithm) {
  const stage = makeStage();
  const bench = makeBench();
  const ctx = {
    algorithm, stage, data: new Dataset(stage), ui: bench, state: {}, params: {}, model: null,
    rng: makeRng(ALGORITHM[algorithm]?.index * 97 + 13 || 1),
    sfx() {}, toast() {}, paramLabel: (p) => p,
    async run(action, args = {}) { return act(ctx, action, args); },
    setModel(m) { ctx.model = m; }, updateLoss() {},
    resetModel() { const m = ctx.model; ctx.model = null; (m?.clear || m?.dispose)?.call(m, ctx); },
    refresh() { ctx.model?.refresh?.(ctx); },
    openSandbox: async () => {},
  };
  return ctx;
}

/** Run one action as Lesson.runAction does (the handler, then the live refresh) and return what it returned. */
export async function act(ctx, action, args = {}) {
  const fn = ACTIONS[action];
  if (!fn) throw new Error(`no handler for ${action}`);
  const out = await fn(ctx, args);
  general.runLive?.(ctx);
  return out;
}

/** The current model's class on the game's res × res floor grid (null cells without a model). */
export function floorCells(ctx, res = 16) {
  const m = ctx.model;
  return gridCells(res).map((c) => (m?.predict ? m.predict(c) : null));
}
