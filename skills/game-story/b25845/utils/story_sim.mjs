// Plays a story through a game's own action handlers, headless: a stub stage and bench stand in for the renderer, so a story's numbers can be checked against the code the game runs.
//
// Built for Model Workshop's layout (src/actions/index.js exporting ACTIONS,
// src/dataset.js, src/config.js with ALGORITHM; the engine's
// src/core/rng.js). The stub is in story mode, like lesson.js's context:
// ctx.refresh calls only the model's refresh, never a sandbox's.
//
//   import { loadGame, makeCtx, act, snapshot, play } from './story_sim.mjs';
//   const game = await loadGame('/path/to/games/model-workshop');
//   const trace = await play(game, storyDict, { extra: (ctx) => ({ k: ctx.state.knn?.k }) });
//
//   node story_sim.mjs <game_root> <story.json> [engine_root]   -> JSON trace on stdout
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Imports a game's handlers: { root, ACTIONS, Dataset, ALGORITHM, makeRng, gridCells, import(rel) }. */
export async function loadGame(gameRoot, { engineRoot } = {}) {
  const root = resolve(gameRoot);
  const engine = resolve(engineRoot ?? resolve(root, '..', 'engine'));
  const imp = (rel) => import(pathToFileURL(resolve(root, rel)).href);
  const [{ ACTIONS }, { Dataset }, config, { makeRng }] = await Promise.all([
    imp('src/actions/index.js'),
    imp('src/dataset.js'),
    imp('src/config.js'),
    import(pathToFileURL(resolve(engine, 'src/core/rng.js')).href),
  ]);
  let gridCells = null, runLive = null;
  try { ({ gridCells } = await imp('src/ml/knn.js')); } catch { gridCells = null; }
  try { ({ runLive } = await imp('src/actions/general.js')); } catch { runLive = null; }
  return { root, ACTIONS, Dataset, ALGORITHM: config.ALGORITHM ?? {}, makeRng, gridCells, runLive, import: imp };
}

// Just enough DOM for the bench's panels (tables, steps, notes).
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.own = ''; this.children = []; this.dataset = {}; this.isConnected = true; this.parentNode = null; }
  append(...cs) { for (const c of cs) { c.parentNode = this; this.children.push(c); } }
  after(e) { const p = this.parentNode; if (p) { e.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, e); } }
  remove() { const p = this.parentNode; if (p) p.children = p.children.filter((c) => c !== this); }
  set innerHTML(_) { this.children = []; this.own = ''; }
  set textContent(t) { this.children = []; this.own = String(t); }
  get textContent() { return this.own + this.children.map((c) => c.textContent).join(''); }
}
const doc = { createElement: (t) => new El(t) };

/** A bench panel as text lines: a note as "[tone] text", a table row per line, a step per line. */
export function panelLines(el) {
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

/** A stage stand-in: entities (id, asset, pos, color, alpha, scale, clip) and labels, no rendering. */
export function makeStage() {
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

/** A bench stand-in with the engine bench's keys: meters, charts, sliders, tools, plaque, and hide(id) removing id, meter:id, slider:id, tool:id, chart:id. */
export function makeBench() {
  const root = new El('div');
  const b = {
    doc, root, sections: {}, items: new Map(), meters: {}, charts: {}, sliders: {}, tools: [], plaque: null, cls: 0,
    meter(id, o) { b.meters[id] = { label: o.label, value: o.value, shown: o.fmt ? o.fmt(o.value) : String(o.value) }; b.items.set(`meter:${id}`, true); },
    chart(id, o) { b.charts[id] = JSON.parse(JSON.stringify(o)); b.items.set(`chart:${id}`, true); },
    formula(t) { b.plaque = t; b.items.set('formula', true); },
    slider(p, o) { b.sliders[p] = o.value; b.items.set(`slider:${p}`, true); }, setSlider(p, v) { if (p in b.sliders) b.sliders[p] = v; },
    choice(p, o) { b.sliders[p] = o.value; b.items.set(`slider:${p}`, true); },
    addTool(t) { if (!b.tools.includes(t)) b.tools.push(t); b.items.set(`tool:${t}`, true); }, classPicker() {}, selectTool() {},
    hide(id) {
      for (const k of [id, `meter:${id}`, `slider:${id}`, `tool:${id}`, `chart:${id}`]) {
        if (!b.items.has(k)) continue;
        b.items.delete(k);
        if (k.startsWith('meter:')) delete b.meters[id];
        if (k.startsWith('chart:')) delete b.charts[id];
        if (k.startsWith('slider:')) delete b.sliders[id];
        if (k.startsWith('tool:')) b.tools = b.tools.filter((t) => t !== id);
        if (k === 'formula') b.plaque = null;
      }
    },
    changed() {}, clear() { b.items.clear(); b.meters = {}; b.charts = {}; b.sliders = {}; b.tools = []; b.plaque = null; },
  };
  return b;
}

/** A lesson context in story mode, as the game's lesson.js builds it, with the station's seeded RNG. */
export function makeCtx(game, algorithm) {
  const stage = makeStage();
  const bench = makeBench();
  const ctx = {
    algorithm, stage, data: new game.Dataset(stage), ui: bench, state: {}, params: {}, model: null,
    rng: game.makeRng(game.ALGORITHM[algorithm]?.index * 97 + 13 || 1),
    sfx() {}, toast() {}, paramLabel: (p) => p,
    async run(action, args = {}) { return act(ctx, action, args); },
    setModel(m) { ctx.model = m; ctx.updateLoss(); },
    resetModel() { const m = ctx.model; ctx.model = null; (m?.clear || m?.dispose)?.call(m, ctx); bench.hide('loss'); },
    updateLoss() {
      const kind = ctx.state.lossKind;
      if (!kind || !ctx.model?.loss) return;
      const v = ctx.model.loss(kind);
      if (Number.isFinite(v)) bench.meter('loss', { label: 'Loss', value: v });
    },
    refresh() { ctx.model?.refresh?.(ctx); ctx.updateLoss(); },
    openSandbox: async () => {},
  };
  ctx.game = game;
  return ctx;
}

/** Runs one action as a beat's `act` does, then the live panels (as Lesson.runAction); throws on an unknown action. */
export async function act(ctx, action, args = {}) {
  const fn = ctx.game.ACTIONS[action];
  if (!fn) throw new Error(`no handler for ${action}`);
  const out = await fn(ctx, args || {});
  ctx.game.runLive?.(ctx);
  return out;
}

/** The current model's class on a res × res floor grid (the game's gridCells), or [] without one. */
export function floorCells(ctx, res = 16) {
  const m = ctx.model;
  if (!ctx.game.gridCells) return [];
  return ctx.game.gridCells(res).map((c) => (m?.predict ? m.predict(c) : null));
}

/** What a beat leaves behind: points, model params, bench panels/meters/charts/plaque, labels, entity ids, floor cells. */
export function snapshot(ctx, { cells = 16 } = {}) {
  const m = ctx.model;
  const points = ctx.data.points.map((p) => {
    const o = { id: p.id, x: p.x, z: p.z, cls: p.cls };
    if (p.y !== undefined) o.y = p.y;
    if (p.trueCls !== undefined) o.trueCls = p.trueCls;
    if (p.copyOf !== undefined) o.copyOf = p.copyOf;
    if (p.role !== undefined) o.role = p.role;
    try { if (m?.predict) o.pred = m.predict(p); } catch { /* left out */ }
    try { if (m?.prob) o.prob = m.prob(p); } catch { /* left out */ }
    return o;
  });
  let params = null;
  try { params = m?.params?.() ?? null; } catch { params = null; }
  const panels = {};
  for (const [k, el] of ctx.ui.items) if (el instanceof El) panels[k] = panelLines(el);
  return {
    model: m?.name ?? null, points, params, panels, plaque: ctx.ui.plaque,
    meters: structuredClone(ctx.ui.meters), charts: structuredClone(ctx.ui.charts), sliders: { ...ctx.ui.sliders }, tools: [...ctx.ui.tools],
    labels: Object.fromEntries(ctx.stage.labels), entities: [...ctx.stage.entities.keys()],
    cells: cells ? floorCells(ctx, cells) : [],
  };
}

/** Plays every beat (remove, spawn, act) and returns one snapshot per beat, with `id`, `result` and `extra(ctx, beat)` merged in. */
export async function play(game, story, { extra, cells = 16 } = {}) {
  const ctx = makeCtx(game, story.algorithm);
  const trace = [];
  for (const beat of story.beats) {
    for (const id of beat.remove || []) ctx.stage.remove(id);
    for (const s of beat.spawn || []) ctx.stage.spawn(s.id, s.asset, { at: s.at, scale: s.scale, clip: s.clip, color: s.color });
    const out = beat.act ? await act(ctx, beat.act.action, beat.act.args || {}) : undefined;
    const snap = snapshot(ctx, { cells });
    snap.id = beat.id;
    snap.result = out === undefined ? null : JSON.parse(JSON.stringify(out, (_k, v) => (v === Infinity ? 'Infinity' : v)));
    if (extra) Object.assign(snap, extra(ctx, beat));
    trace.push(snap);
  }
  return trace;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const [gameRoot, storyPath, engineRoot] = process.argv.slice(2);
  if (!gameRoot || !storyPath) { console.error('usage: node story_sim.mjs <game_root> <story.json> [engine_root]'); process.exit(2); }
  const game = await loadGame(gameRoot, { engineRoot });
  console.log(JSON.stringify(await play(game, JSON.parse(readFileSync(storyPath, 'utf8')))));
}
