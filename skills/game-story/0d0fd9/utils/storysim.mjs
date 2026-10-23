// Plays a story's beats through a Model Workshop-style game's own action handlers on a stub stage and bench; prints a per-beat JSON trace.
//
// The game must have the Model Workshop layout: src/actions/index.js (ACTIONS),
// src/dataset.js (Dataset), src/config.js (ALGORITHM), src/actions/general.js
// (LOSS_LABELS), and the engine's makeRng from '@games/engine/core'. The
// context mirrors lesson.js makeContext: the station's seeded RNG
// (ALGORITHM[algorithm].index * 97 + 13), ctx.refresh and the loss meter.
// Each beat runs `remove`, then `spawn`, then `act`, then a snapshot.
//   node storysim.mjs <story.json> <game_root>      -> trace on stdout
// Python: story.simulate(story_or_package, game_root).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [storyPath, gameRoot] = process.argv.slice(2);
if (!storyPath || !gameRoot) {
  console.error('usage: node storysim.mjs <story.json> <game_root>');
  process.exit(2);
}
const src = (rel) => import(pathToFileURL(join(gameRoot, 'src', rel)).href);
const { ACTIONS } = await src('actions/index.js');
const { Dataset } = await src('dataset.js');
const { ALGORITHM } = await src('config.js');
const { LOSS_LABELS } = await src('actions/general.js');
const enginePath = createRequire(join(gameRoot, 'package.json')).resolve('@games/engine/core');
const { makeRng } = await import(pathToFileURL(enginePath).href);

// A DOM just big enough for the bench's panels (tables, steps, notes).
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.own = ''; this.children = []; this.dataset = {}; this.isConnected = true; this.parentNode = null; }
  append(...cs) { for (const c of cs) { c.parentNode = this; this.children.push(c); } }
  after(e) { const p = this.parentNode; if (p) { e.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, e); } }
  set innerHTML(_) { this.children = []; this.own = ''; }
  set textContent(t) { this.children = []; this.own = String(t); }
  get textContent() { return this.own + this.children.map((c) => c.textContent).join(''); }
}
const doc = { createElement: (t) => new El(t) };

// A panel as text lines: a table gives its rows, steps their lines, a note "[tone] text".
function panelLines(el) {
  if (el.className.startsWith('bench-note')) return [`[${el.className.split(' ')[1]}] ${el.textContent}`];
  const out = [];
  const walk = (e) => {
    if (e.tag === 'tr') { out.push(e.children.map((c) => c.textContent).join(' | ')); return; }
    if (['bench-title', 'step', 'step new', 'bench-foot-line'].includes(e.className)) { out.push(e.textContent); return; }
    e.children.forEach(walk);
  };
  walk(el);
  return out;
}

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
      setColor(c) { this.color = c; }, setAlpha(a) { this.alpha = a; }, setVisible() {},
      // Glow strength and colour (nn.js lights neurons and wires with it).
      setGlow(g, c) { this.glow = g; if (c) this.glowColor = c; },
      async scaleTo(s) { this.scale = s; }, async moveTo(p) { this.pos = [...p]; }, async fadeTo(a) { this.alpha = a; },
      async play(c) { this.clip = c; }, async turnTo() {},
    };
    entities.set(id, e);
    return e;
  };
  const st = {
    entities, labels, origin: { x: 0, z: 0 },
    spawn: (id, asset, o) => make(id, asset, o),
    get: (id) => entities.get(id), has: (id) => entities.has(id),
    remove(id) { const e = entities.get(id); if (e) e.alive = false; entities.delete(id); labels.delete(id); },
    removePrefix(p) { for (const id of [...entities.keys()]) if (id.startsWith(p)) st.remove(id); for (const k of [...labels.keys()]) if (k.startsWith(p)) labels.delete(k); },
    all: (p) => [...entities.values()].filter((e) => e.id.startsWith(p)),
    label(id, text) { if (text === null || text === undefined) labels.delete(id); else labels.set(id, String(text)); },
    segment(id, a, b, o = {}) { const e = entities.get(id) || make(id, o.asset || 'environment/line-segment', { at: [a[0], a[2]], y: a[1] }); e.from = a; e.to = b; return e; },
    // The engine's animated segment move (boost, regression, tree, kmeans, cluster, nn redraw with it): lands at once.
    async segmentTo(id, a, b, _dur, o = {}) { st.segment(id, a, b, o); },
    async tween(_d, fn) { fn(1); }, async wait() {}, finishTweens() {},
  };
  return st;
}

function makeBench() {
  const root = new El('div');
  const b = {
    doc, root, sections: {}, items: new Map(), meters: {}, charts: {}, sliders: {}, tools: [], plaque: null, cls: 0,
    meter(id, o) { b.meters[id] = { label: o.label, value: o.value, shown: o.fmt ? o.fmt(o.value) : String(o.value) }; b.items.set(id, true); },
    chart(id, o) { b.charts[id] = JSON.parse(JSON.stringify(o)); b.items.set(`chart:${id}`, true); },
    formula(t) { b.plaque = t; },
    slider(p, o) { b.sliders[p] = o.value; }, setSlider(p, v) { b.sliders[p] = v; },
    addTool(t) { b.tools.push(t); }, classPicker() {}, choice() {},
    hide(id) { b.items.delete(id); delete b.meters[id]; delete b.charts[id]; if (id === 'formula') b.plaque = null; },
    changed() {}, clear() {},
  };
  return b;
}

function makeCtx(algorithm) {
  const stage = makeStage();
  const bench = makeBench();
  const ctx = {
    algorithm, stage, data: new Dataset(stage), ui: bench, state: {}, params: {}, model: null,
    rng: makeRng(ALGORITHM[algorithm]?.index * 97 + 13 || 1),
    sfx() {}, toast() {}, paramLabel: (p) => p,
    async run(action, args = {}) { return ACTIONS[action](ctx, args || {}); },
    setModel(m) { ctx.model = m; ctx.updateLoss(); },
    resetModel() { const m = ctx.model; ctx.model = null; (m?.clear || m?.dispose)?.call(m, ctx); bench.hide('loss'); },
    updateLoss() {
      const kind = ctx.state.lossKind;
      if (!kind || !ctx.model?.loss) return;
      const v = ctx.model.loss(kind);
      if (Number.isFinite(v)) bench.meter('loss', { label: LOSS_LABELS[kind] || 'Loss', value: v });
    },
    refresh() { ctx.model?.refresh?.(ctx); ctx.updateLoss(); },
    openSandbox: async () => {},
  };
  return ctx;
}

function snapshot(ctx) {
  const m = ctx.model;
  const points = ctx.data.points.map((p) => {
    const o = { id: p.id, x: p.x, z: p.z, cls: p.cls };
    for (const k of ['y', 'trueCls', 'copyOf', 'role']) if (p[k] !== undefined) o[k] = p[k];
    try { if (m?.predict) o.pred = m.predict(p); } catch { /* left out */ }
    try { if (m?.prob) o.prob = m.prob(p); } catch { /* left out */ }
    return o;
  });
  let params = null;
  try { params = m?.params?.() ?? null; } catch { params = null; }
  const panels = {};
  for (const [k, el] of ctx.ui.items) if (el instanceof El) panels[k] = panelLines(el);
  const entities = {};
  for (const e of ctx.stage.entities.values()) {
    const i = e.id.indexOf(':');
    if (i > 0) (entities[e.id.slice(0, i + 1)] ||= []).push(e.id.slice(i + 1));
  }
  const knn = ctx.state.knn;
  return {
    model: m?.name ?? null, points, params, panels, plaque: ctx.ui.plaque,
    // copies: the bench mutates these objects in place
    meters: structuredClone(ctx.ui.meters), charts: structuredClone(ctx.ui.charts), sliders: { ...ctx.ui.sliders },
    labels: Object.fromEntries(ctx.stage.labels), entities,
    knn: knn ? { k: knn.k, q: knn.q, metric: knn.metric, neighbors: knn.neighbors.map(({ p, d }) => ({ id: p.id, d, cls: p.cls })), regionCls: knn.regions ? knn.regionCls : null, result: knn.result ?? null } : null,
    scaled: ctx.state.scaled ?? null,
  };
}

const story = JSON.parse(readFileSync(storyPath, 'utf8'));
const ctx = makeCtx(story.algorithm);
const trace = [];
for (const beat of story.beats) {
  for (const id of beat.remove || []) ctx.stage.remove(id);
  for (const s of beat.spawn || []) ctx.stage.spawn(s.id, s.asset, { at: s.at, scale: s.scale, clip: s.clip, color: s.color });
  let out = null;
  if (beat.act) {
    const fn = ACTIONS[beat.act.action];
    if (!fn) throw new Error(`no handler for ${beat.act.action}`);
    out = await fn(ctx, beat.act.args || {});
  }
  const snap = snapshot(ctx);
  snap.id = beat.id;
  snap.result = out === undefined ? null : JSON.parse(JSON.stringify(out, (_k, v) => (v === Infinity ? 'Infinity' : v)));
  trace.push(snap);
}
console.log(JSON.stringify(trace));
