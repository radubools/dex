// Plays a story's beats through Model Workshop's own action handlers, with a
// stub stage and bench, and prints what each beat leaves on screen as JSON:
// the points, the model's parameters and predictions, meters, charts, bench
// panels as text rows, labels and the rings on the floor. Actions are awaited
// and tweens finish at once, as if the player waited for every animation.
//   node playback.mjs <game_root> story.json
// playback.py's simulate() runs it.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [root, storyFile] = process.argv.slice(2);
const game = (...p) => import(pathToFileURL(join(root, 'src', ...p)).href);
const { ACTIONS } = await game('actions', 'index.js');
const { Dataset } = await game('dataset.js');
const { ALGORITHM } = await game('config.js');
const { LOSS_LABELS } = await game('actions', 'general.js');
// The engine the game itself resolves, for ctx.rng's seed.
const { makeRng } = await import(pathToFileURL(createRequire(join(root, 'package.json')).resolve('@games/engine/core')).href);

// ---- a DOM just big enough for the bench panels
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this._text = ''; this.children = []; this.dataset = {}; this.isConnected = true; this.parentNode = null; }
  append(...cs) { for (const c of cs) { c.parentNode = this; this.children.push(c); } }
  after(e) { const p = this.parentNode; if (p) { e.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, e); } }
  set innerHTML(_) { this.children = []; this._text = ''; }
  set textContent(t) { this.children = []; this._text = String(t); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
}
const doc = { createElement: (t) => new El(t) };

// Panel contents as text rows: a table gives its rows, steps their lines.
function panelText(el) {
  const rows = [];
  const walk = (e) => {
    if (e.tag === 'tr') { rows.push(e.children.map((c) => c.textContent).join(' | ')); return; }
    if (['bench-title', 'step', 'step new', 'bench-foot-line'].includes(e.className) || e.className.startsWith('bench-note')) { rows.push(e.textContent); return; }
    e.children.forEach(walk);
  };
  if (el.className.startsWith('bench-note')) return [`[${el.className.split(' ')[1]}] ${el.textContent}`];
  walk(el);
  return rows;
}

// ---- the stage
function makeStage() {
  const entities = new Map();
  const labels = new Map();
  const ent = (id, asset, o = {}) => {
    const at = o.at || [0, 0];
    const e = {
      id, asset, alive: true, data: {}, clip: o.clip, color: o.color, alpha: o.alpha ?? 1, scale: o.scale ?? 1,
      pos: [at[0], o.y ?? 0, at[1]],
      get at() { return [this.pos[0], this.pos[2]]; },
      setPos(x, y, z) { this.pos = [x, y, z]; },
      setScale(s) { this.scale = s; }, setRotation() {}, setVisible(v) { this.visible = v; },
      setColor(c) { this.color = c; }, setAlpha(a) { this.alpha = a; },
      async scaleTo(s) { this.scale = s; }, async moveTo(p) { this.pos = [...p]; }, async fadeTo(a) { this.alpha = a; },
      async play(c) { this.clip = c; }, async turnTo() {},
    };
    entities.set(id, e);
    return e;
  };
  const st = {
    entities, labels, time: 0, origin: { x: 0, z: 0 },
    spawn(id, asset, o) { return ent(id, asset, o); },
    get: (id) => entities.get(id), has: (id) => entities.has(id),
    remove(id) { const e = entities.get(id); if (e) e.alive = false; entities.delete(id); labels.delete(id); },
    removePrefix(p) { for (const id of [...entities.keys()]) if (id.startsWith(p)) st.remove(id); for (const k of [...labels.keys()]) if (k.startsWith(p)) labels.delete(k); },
    all: (p) => [...entities.values()].filter((e) => e.id.startsWith(p)),
    label(id, text) { if (text === null || text === undefined) labels.delete(id); else labels.set(id, String(text)); },
    segment(id, a, b, o = {}) { const e = entities.get(id) || ent(id, o.asset || 'environment/line-segment', { at: [a[0], a[2]], y: a[1] }); e.from = a; e.to = b; e.color = o.color ?? e.color; return e; },
    async tween(dur, fn) { fn(1); },
    async wait() {},
    finishTweens() {},
    toWorld: (x, y, z) => ({ x, y, z }),
  };
  return st;
}

// ---- the bench
function makeBench() {
  const root = new El('div');
  const b = {
    doc, root, sections: {}, items: new Map(), meters: {}, charts: {}, sliders: {}, tools: [], plaque: null, cls: 0,
    meter(id, o) { b.meters[id] = { label: o.label, value: o.value, shown: o.fmt ? o.fmt(o.value) : String(o.value) }; b.items.set(id, true); },
    chart(id, o) { b.charts[id] = o; b.items.set(`chart:${id}`, true); },
    formula(t) { b.plaque = t; },
    slider(p, o) { b.sliders[p] = o.value; }, setSlider(p, v) { b.sliders[p] = v; }, choice() {},
    addTool(t) { b.tools.push(t); },
    hide(id) { b.items.delete(id); delete b.meters[id]; delete b.charts[id]; if (id === 'formula') b.plaque = null; },
    changed() {}, clear() {},
  };
  return b;
}

function makeCtx(algorithm) {
  const stage = makeStage();
  const bench = makeBench();
  const data = new Dataset(stage);
  const done = [];
  const ctx = {
    algorithm, stage, data, ui: bench, state: {}, params: {}, model: null,
    rng: makeRng(ALGORITHM[algorithm]?.index * 97 + 13 || 1),
    sfx() {}, toast() {},
    paramLabel: (p) => p,
    async run(action, args = {}) { done.push(action); return ACTIONS[action](ctx, args || {}); },
    setModel(m) { ctx.model = m; ctx.updateLoss(); },
    resetModel() { const m = ctx.model; ctx.model = null; (m?.clear || m?.dispose)?.call(m, ctx); bench.hide('loss'); },
    updateLoss() {
      const kind = ctx.state.lossKind;
      if (!kind || !ctx.model?.loss) return;
      const v = ctx.model.loss(kind);
      if (!Number.isFinite(v)) return;
      bench.meter('loss', { label: LOSS_LABELS[kind] || 'Loss', value: v });
    },
    refresh() { ctx.model?.refresh?.(ctx); ctx.updateLoss(); },
    openSandbox: async () => {},
  };
  return ctx;
}

function snapshot(ctx) {
  const m = ctx.model;
  const pts = ctx.data.points.map((p) => {
    const o = { id: p.id, x: p.x, z: p.z, cls: p.cls };
    if (p.role) o.role = p.role;
    if (p.trueCls !== undefined) o.trueCls = p.trueCls;
    if (m?.predict) o.pred = m.predict(p);
    if (m?.prob) o.prob = m.prob(p);
    return o;
  });
  let params = null;
  try { params = m?.params?.() ?? null; } catch { params = null; }
  const panels = {};
  for (const [k, el] of ctx.ui.items) if (el instanceof El) panels[k] = panelText(el);
  const rings = {};
  for (const e of ctx.stage.entities.values()) {
    const pre = e.id.includes(':') ? e.id.slice(0, e.id.indexOf(':') + 1) : null;
    if (pre && ['miss:', 'flip:', 'warn:', 'split:', 'halo:', 'call:', 'explain:'].includes(pre)) (rings[pre] ||= []).push(e.id.slice(pre.length));
  }
  // Copies: later beats change these in place.
  return JSON.parse(JSON.stringify({
    model: m?.name ?? null, logit: ctx.state.logit?.m ?? null, threshold: ctx.state.logit?.t,
    points: pts, params, meters: ctx.ui.meters, charts: ctx.ui.charts, panels, plaque: ctx.ui.plaque,
    labels: Object.fromEntries(ctx.stage.labels), rings,
    entities: [...ctx.stage.entities.values()].map((e) => ({ id: e.id, asset: e.asset, pos: e.pos, alpha: e.alpha })),
  }));
}

const story = JSON.parse(readFileSync(storyFile, 'utf8'));
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
  snap.result = out && typeof out === 'object' ? JSON.parse(JSON.stringify(out)) : out;
  trace.push(snap);
}
console.log(JSON.stringify(trace));
