// Plays a story's beats through a Model Workshop-style game's own action handlers on a stub stage and bench; prints a per-beat JSON trace.
//
// The game must have the Model Workshop layout: src/actions/index.js (ACTIONS),
// src/dataset.js (Dataset), src/config.js (ALGORITHM), src/actions/general.js
// (LOSS_LABELS), and the engine's makeRng from '@games/engine/core'. The
// context mirrors lesson.js makeContext: the station's seeded RNG
// (ALGORITHM[algorithm].index * 97 + 13), ctx.refresh and the loss meter.
// Each beat runs `remove`, then `spawn`, then `act`, then the live refresh
// lesson.runAction does after every action (general.js runLive: live tables,
// values and explanations follow the model), then a snapshot. The bench keys
// its items as the engine's Bench does (`meter:<id>`, `chart:<id>`, …) and
// hides them the same way.
//
// The lab machine: when the game has lab machines (src/actions/index.js
// exports machineFor, run by the engine's LabMachine from '@games/engine/lab'),
// every beat's `act` goes through the station's machine, as in the game. A
// refused act does not run; its snapshot says so in `lab.refused`. A
// `wait: {action}` must be something the player can do in the state the lab
// is then in (the action, or a tool, button or slider that runs it), else
// `lab.waitRefused`; the player's move is then taken (to its one outcome,
// when it has one). Each snapshot carries `lab.state` after the beat.
//   node storysim.mjs <story.json> <game_root>      -> trace on stdout
//   node storysim.mjs --machine <algorithm> <game_root>  -> the lab machine as Markdown
// Python: story.simulate(story_or_package, game_root), story.check_lab(…),
// story.lab_machine(algorithm, game_root).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const argv = process.argv.slice(2);
const machineOnly = argv[0] === '--machine';
const [storyPath, gameRoot] = machineOnly ? argv.slice(1) : argv;
if (!storyPath || !gameRoot) {
  console.error('usage: node storysim.mjs <story.json> <game_root> | --machine <algorithm> <game_root>');
  process.exit(2);
}
const src = (rel) => import(pathToFileURL(join(gameRoot, 'src', rel)).href);
const actionsIndex = await src('actions/index.js');
const { ACTIONS } = actionsIndex;
const { Dataset } = await src('dataset.js');
const { ALGORITHM } = await src('config.js');
const general = await src('actions/general.js');
const { LOSS_LABELS } = general;
// The game's live refresh after each action; a game without it has nothing to refresh.
const runLive = general.runLive || (() => {});
const enginePath = createRequire(join(gameRoot, 'package.json')).resolve('@games/engine/core');
const { makeRng } = await import(pathToFileURL(enginePath).href);
// The engine's lab machines, when the game has them.
let lab = null;
if (typeof actionsIndex.machineFor === 'function') {
  const labPath = createRequire(join(gameRoot, 'package.json')).resolve('@games/engine/lab');
  lab = await import(pathToFileURL(labPath).href);
}
if (machineOnly) {
  const algorithm = storyPath;
  const out = lab
    ? { algorithm, own: !!actionsIndex.MACHINES?.[algorithm], markdown: lab.describeMachine(actionsIndex.machineFor(algorithm)) }
    : { algorithm, own: false, markdown: '' };
  console.log(JSON.stringify(out));
  process.exit(0);
}

// A DOM just big enough for the bench's panels (tables, steps, notes, charts):
// append/after/remove, classList, and querySelector(All) for simple selectors
// ("tbody tr", "td.mark", ".chart-title": tags and classes, descendant only).
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.own = ''; this.children = []; this.dataset = {}; this.style = {}; this.isConnected = true; this.parentNode = null; }
  append(...cs) { for (const c of cs) { if (typeof c !== 'object') { this.own += String(c); continue; } c.parentNode = this; this.children.push(c); } }
  appendChild(c) { this.append(c); return c; }
  after(e) { const p = this.parentNode; if (p) { e.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, e); } }
  remove() { const p = this.parentNode; if (p) p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; this.isConnected = false; }
  setAttribute(k, v) { this[`attr:${k}`] = String(v); } getAttribute(k) { return this[`attr:${k}`] ?? null; }
  get classList() {
    const el = this;
    const list = () => el.className.split(/\s+/).filter(Boolean);
    const set = (l) => { el.className = l.join(' '); };
    return {
      add: (...c) => set([...new Set([...list(), ...c])]),
      remove: (...c) => set(list().filter((x) => !c.includes(x))),
      contains: (c) => list().includes(c),
      toggle: (c, on) => { const has = list().includes(c); const want = on ?? !has; if (want !== has) (want ? this.classList.add(c) : this.classList.remove(c)); return want; },
    };
  }
  matches(part) {
    const [tag, ...classes] = part.split('.');
    const mine = this.className.split(/\s+/);
    return (!tag || tag === this.tag) && classes.every((c) => mine.includes(c));
  }
  querySelectorAll(sel) {
    const parts = sel.trim().split(/\s+/);
    const out = [];
    const walk = (e, i) => {
      for (const c of e.children) {
        const hit = c.matches(parts[i]);
        if (hit && i === parts.length - 1) out.push(c);
        walk(c, hit && i < parts.length - 1 ? i + 1 : i);
      }
    };
    walk(this, 0);
    return [...new Set(out)];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
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
      // Setters return the entity, as the engine's do, so calls chain (e.setPos(…).setScale(…)).
      setPos(x, y, z) { this.pos = [x, y, z]; return this; }, setScale(s) { this.scale = s; return this; },
      setRotation(r) { this.rotation = r; return this; },
      setColor(c) { this.color = c; return this; }, setAlpha(a) { this.alpha = a; return this; },
      setVisible(v) { this.visible = v; return this; },
      // Glow strength and colour (nn.js lights neurons and wires with it).
      setGlow(g, c) { this.glow = g; if (c) this.glowColor = c; return this; },
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
    // Puts an existing entity on the segment a→b (gd.js draws its gradient arrow with it).
    placeSegment(e, a, b) { if (e) { e.from = a; e.to = b; e.pos = [...a]; } return e; },
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
    // Items under the engine Bench's keys: meter:<id>, chart:<id>, slider:<p>, tool:<t>, formula.
    // `meters` and `charts` (by bare id) are what the snapshot reports.
    meter(id, o) {
      b.meters[id] = { label: o.label, value: o.value, shown: o.fmt ? o.fmt(o.value) : String(o.value) };
      if (!b.items.has(`meter:${id}`)) b.items.set(`meter:${id}`, { remove() { delete b.meters[id]; } });
    },
    chart(id, o) {
      b.charts[id] = JSON.parse(JSON.stringify(o));
      let box = b.items.get(`chart:${id}`);
      if (!box) {
        box = new El('div'); box.className = 'chart';
        const title = new El('div'); title.className = 'chart-title';
        box.append(title, new El('canvas'));
        box.remove = () => { delete b.charts[id]; };
        b.items.set(`chart:${id}`, box);
      }
      box.querySelector('.chart-title').textContent = o.title || '';
      return box;
    },
    formula(t) { b.plaque = t; b.items.set('formula', { remove() { b.plaque = null; } }); },
    slider(p, o) { b.sliders[p] = o.value; b.items.set(`slider:${p}`, { remove() { delete b.sliders[p]; } }); },
    setSlider(p, v) { b.sliders[p] = v; },
    addTool(t) { if (!b.tools.includes(t)) b.tools.push(t); b.items.set(`tool:${t}`, { remove() { b.tools = b.tools.filter((x) => x !== t); } }); },
    classPicker() {}, choice() {},
    // As the engine's Bench.hide: the key itself and its meter:/slider:/tool:/chart: forms.
    hide(id) {
      for (const k of [id, `meter:${id}`, `slider:${id}`, `tool:${id}`, `chart:${id}`]) {
        const e = b.items.get(k);
        if (!e) continue;
        e.remove?.();
        b.items.delete(k);
      }
      if (id === 'formula') b.plaque = null;
    },
    changed() {},
    clear() { for (const e of b.items.values()) e.remove?.(); b.items.clear(); b.meters = {}; b.charts = {}; b.plaque = null; },
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
    // As lesson.runAction: the action, then the live refresh.
    async run(action, args = {}) { const out = await ACTIONS[action](ctx, args || {}); runLive(ctx); return out; },
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
  for (const [k, el] of ctx.ui.items) if (el instanceof El && !k.startsWith('chart:')) panels[k] = panelLines(el);
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
const explore = argv.includes('--explore');
const json = (v) => (v === undefined ? null : JSON.parse(JSON.stringify(v, (_k, x) => (x === Infinity ? 'Infinity' : x === -Infinity ? '-Infinity' : x))));

// The engine's story runner and a DOM for its card, when the game has them:
// stories then play exactly as in the game (schema 1 and 2: acts and their
// results, values, branches, waits). Without them, schema 1 plays in a line.
const gameRequire = createRequire(join(gameRoot, 'package.json'));
let engineStory = null, happy = null;
try {
  engineStory = await import(pathToFileURL(gameRequire.resolve('@games/engine/story')).href);
  happy = await import(pathToFileURL(gameRequire.resolve('happy-dom')).href);
} catch { engineStory = null; }
// The game's `$` reads (Model Workshop: src/reads.js readPath(ctx, lesson, path)).
let readsModule = null;
try { readsModule = await src('reads.js'); } catch { readsModule = null; }
const fmtNum = general.fmt || ((v) => (typeof v === 'number' ? String(Math.round(v * 100) / 100) : String(v)));
// Numbers in text: the game's rule (lesson.js hooks.format), or `digits` decimals always.
const format = (v, digits) => (typeof v === 'number' && Number.isFinite(v) && digits !== undefined && digits !== null
  ? v.toFixed(digits).replace(/^-/, '−').replace(/^−(0\.?0*)$/, '$1') : v === null || v === undefined ? '–' : fmtNum(v));

// One playthrough. `pick` = { beat, option } answers that choice (its first
// showing) with that option; every other choice gets its first right option.
async function playthrough(pick = null) {
  const ctx = makeCtx(story.algorithm);
  ctx.results = new Map();
  let runs = 0;
  // One action, as lesson.runAction does it: the handler, then the live refresh.
  async function runOne(action, args) {
    const fn = ACTIONS[action];
    if (!fn) throw new Error(`no handler for ${action}`);
    const out = await fn(ctx, args || {});
    ctx.results.set(action, { value: out ?? null, n: ++runs });
    runLive(ctx); // lesson.runAction refreshes live panels after every action
    return out;
  }
  const def = lab ? actionsIndex.machineFor(story.algorithm) : null;
  const machine = lab ? new lab.LabMachine(def, { ctx, run: (action, args) => runOne(action, args), stop: () => ctx.stage.finishTweens?.() }) : null;
  // Sends one act as the lesson does: through the machine; returns its result, or null when refused.
  let beatInfo = {};
  async function act(action, args) {
    if (!machine) { const out = await runOne(action, args); beatInfo.results.push(json(out)); return out; }
    const before = runs;
    const r = await machine.send(action, args || {}, { source: 'story' });
    if (!r.ok) { (beatInfo.refused ||= []).push(`${action}: ${r.reason}`); beatInfo.results.push(null); return null; }
    const mine = ctx.results.get(action);
    const out = mine && mine.n > before ? mine.value : r.result ?? null;
    beatInfo.results.push(json(out));
    return out;
  }
  // The player's move for a wait on an action (or any of a list).
  function playerMove(names) {
    if (!machine) return;
    for (const want of [].concat(names)) {
      const move = lab.playerInput(def, machine.state, want);
      if (!move) continue;
      const after = lab.outcomes(move.t, machine.state);
      if (after.length === 1) machine.setState(after[0]);
      beatInfo.player = move.input;
      return;
    }
    beatInfo.waitRefused = `the player cannot ${[].concat(names).join(' or ')} when the ${def.id} lab is "${machine.state}"; there it accepts ${machine.inputs().join(', ') || 'nothing'}`;
  }
  const snap = (beat, extra) => {
    const out = snapshot(ctx);
    out.id = beat.id;
    if (machine) {
      const { results, refused, waitRefused, player } = beatInfo;
      out.lab = { machine: def.id, state: machine.state, ...(refused ? { refused: refused.join('; ') } : {}), ...(waitRefused ? { waitRefused } : {}), ...(player ? { player } : {}) };
      void results;
    }
    const rs = beatInfo.results;
    out.result = rs.length <= 1 ? (rs[0] ?? null) : rs;
    return Object.assign(out, extra);
  };

  if (!engineStory) {
    if (story.schema === 2) throw new Error('schema 2 needs the game\'s engine (@games/engine/story) and happy-dom');
    const trace = [];
    for (const beat of story.beats) {
      beatInfo = { results: [] };
      for (const id of beat.remove || []) ctx.stage.remove(id);
      for (const s of beat.spawn || []) ctx.stage.spawn(s.id, s.asset, { at: s.at, scale: s.scale, clip: s.clip, color: s.color });
      if (beat.act) await act(beat.act.action, beat.act.args);
      const w = beat.wait;
      if (w && typeof w === 'object' && w.action) playerMove(w.action);
      trace.push(snap(beat, {}));
    }
    return { trace, finished: true, path: story.beats.map((b) => b.id) };
  }

  const { StoryRunner, beatsToSteps, evalExpr } = engineStory;
  const win = new happy.Window();
  const document = win.document;
  const pending = new Set();
  const track = (p) => { pending.add(p); p.finally(() => pending.delete(p)); return p; };
  const lessonInfo = { level: story.level || 'easy', algorithm: story.algorithm, mode: 'story' };
  const read = (path) => (readsModule?.readPath ? readsModule.readPath(ctx, lessonInfo, path) : null);
  const hooks = {
    act: (action, args) => track(act(action, args)),
    read,
    format,
    spawn: (s) => ctx.stage.spawn(s.id, s.asset, { at: s.at, y: s.y, scale: s.scale, clip: s.clip, color: s.color }),
    remove: (id) => ctx.stage.remove(id),
    camera: () => {}, frame: () => true, position: () => null, highlight: () => {}, sfx: () => {}, music: () => {}, settle: () => {},
  };
  const camera = { vw: 800, vh: 600, zoom: 1, ppu: 40, flying: false, worldToScreen: () => ({ x: 0, y: 0, behind: false }), screenToWorld: () => ({ x: 0, y: 0 }), flyTo: () => Promise.resolve() };
  const runner = new StoryRunner({ camera, doc: document, touch: () => false, random: () => 0.5 });
  const steps = beatsToSteps(story, hooks);
  let finished = false;
  const done = runner.play(steps, { label: 'Step' }).then((how) => { finished = how === 'finished'; return how; });
  const settle = async () => {
    for (let k = 0; k < 50; k++) {
      await new Promise((r) => setTimeout(r, 0));
      if (!pending.size) { await new Promise((r) => setTimeout(r, 0)); if (!pending.size) return; }
      await Promise.allSettled([...pending]);
    }
  };
  const trace = [];
  const scope = () => ({ vars: runner.vars, read });
  let used = false;
  beatInfo = { results: [] };
  for (let guard = 0; guard < 600 && runner.active; guard++) {
    await settle();
    if (!runner.active) break;
    const s = runner.step;
    const beat = story.beats[runner.i];
    const shown = { say: runner.ui.text.textContent, title: runner.ui.title.textContent };
    const extra = { shown };
    const w = beat.wait;
    if (s.choice) {
      shown.prompt = runner.ui.prompt.textContent;
      shown.options = [...runner.ui.choices.children].map((b) => b.textContent);
      const opts = s.choice.options;
      let k = opts.findIndex((o) => o.correct);
      if (pick && !used && pick.beat === beat.id) { k = pick.option; used = true; extra.picked = k; }
      if (k < 0) k = 0;
      const button = document.createElement('button');
      runner.answer(s.choice, opts[k], button);
      shown.feedback = runner.ui.feedback.textContent;
      if (runner.doneAt === null) { const r = opts.findIndex((o) => o.correct); if (r >= 0) runner.answer(s.choice, opts[r], document.createElement('button')); }
      await settle();
    } else if (s.input) {
      shown.prompt = runner.ui.inputPrompt.textContent;
      const want = evalExpr(w.input.answer, scope());
      extra.answer = json(want);
      if (typeof want === 'number' && Number.isFinite(want)) {
        runner.ui.inputField.value = String(want);
        runner.submitInput();
        shown.feedback = runner.ui.inputFeedback.textContent;
      }
      if (runner.doneAt === null) extra.inputFailed = `the answer "${w.input.answer}" gives ${JSON.stringify(json(want))}, not a number the player could type`;
    } else if (w && typeof w === 'object' && 'until' in w) {
      const ok = !!evalExpr(w.until, scope());
      if (!ok) extra.untilUnmet = `"${w.until}" is false when the beat is shown (the player's task makes it true)`;
    } else if (w && typeof w === 'object' && w.action) playerMove(w.action);
    // A cue, title or prompt that shows a value that is not there.
    const texts = [shown.say, shown.title, shown.prompt, shown.feedback, ...(shown.options || [])].filter(Boolean);
    const missing = texts.filter((t) => t.includes('–'));
    if (story.schema === 2 && missing.length) extra.missing = missing;
    extra.vars = json(runner.vars);
    trace.push(snap(beat, extra));
    beatInfo = { results: [] };
    runner.next();
  }
  await settle();
  if (runner.active) runner.stop('stopped');
  await done;
  return { trace, finished, path: [...runner.path] };
}

if (!explore) {
  const { trace } = await playthrough();
  console.log(JSON.stringify(trace));
} else {
  // Every option with a goto, other than the one the main line takes, gets its own run.
  const runs = [{ pick: null, ...(await playthrough()) }];
  for (const beat of story.beats) {
    const opts = beat.wait?.choice?.options || [];
    const main = opts.findIndex((o) => o.correct);
    for (const [k, o] of opts.entries()) if (o.goto && k !== main) runs.push({ pick: { beat: beat.id, option: k }, ...(await playthrough({ beat: beat.id, option: k })) });
  }
  console.log(JSON.stringify({ runs }));
}
