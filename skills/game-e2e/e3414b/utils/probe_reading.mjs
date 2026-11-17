#!/usr/bin/env node
// probe_reading.mjs — open a story's reading overlay at chosen beats and measure it: display-formula widths vs the card, scroll, figures. Data only, no verdicts.
//
// Companion to bookcheck.mjs for the "does it fit" question bookcheck does not
// answer: plays the main route through the engine's in-page e2e driver, and at
// the named beats clicks the card's book button and records, for every
// `.eng-math` in the opened passage, its rendered width against the reading
// scroll box's clientWidth — so an over-wide display formula is caught as
// data (`overfullMaths`) rather than seen and missed. `math` mode also
// screenshots the widest formula and tests whether the box scrolls
// horizontally (MathJax overflow does NOT extend scrollWidth, so an unscroll-
// able over-wide formula is unreachable — clipped); `figure` mode scrolls to
// the first figure and screenshots it. Writes <out>/probe-reading-<size>.json
// and shots under <out>/shots-<size>/, and prints the records.
//
//   node probe_reading.mjs --story <package dir> [--game <root>] [--size phone] \
//     beat:mode ... [--url http://localhost:5176] [--out DIR]
//   beats are matched by the beat id in the driver's shot name (e.g. `bayes`,
//   `slope`); modes: math | figure. The dev server at --url must be running
//   with the story's reading package installed.

import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

function argFlags(argv) {
  const o = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) o[a.slice(2)] = argv[++i];
    else o._.push(a);
  }
  return o;
}

const o = argFlags(process.argv.slice(2));
if (!o.story || !o._.length) {
  console.error('usage: probe_reading.mjs --story <pkg dir> [--game ROOT] [--size desktop|phone] beat:mode ... [--url U] [--out DIR] [--playwright path]');
  process.exit(2);
}
const pkg = resolve(o.story);
const vp = o.size || 'phone';
const wants = o._;
if (wants.some((w) => !/:/.test(w))) {
  console.error('every target must be beat:mode (modes: math | figure)');
  process.exit(2);
}
const story = JSON.parse(readFileSync(join(pkg, 'story.json'), 'utf8'));
const tests = existsSync(join(pkg, 'e2e.json')) ? JSON.parse(readFileSync(join(pkg, 'e2e.json'), 'utf8')) : {};

const VIEWPORTS = {
  desktop: { viewport: { width: 1280, height: 800 } },
  phone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};
if (!(vp in VIEWPORTS)) { console.error(`size must be desktop or phone, got ${vp}`); process.exit(2); }
const targets = {};
for (const w of wants) {
  const [beat, mode] = w.split(':');
  (targets[beat] ||= []).push(mode || 'math');
}

// engine src story/e2e.js beside the game (the games share one engine), and a
// playwright the script can import — same discovery as bookcheck.mjs.
async function engineUrl() {
  for (const base of o.game ? [resolve(o.game, '..'), resolve(o.game)] : []) {
    const f = join(base, 'engine', 'src', 'story', 'e2e.js');
    if (existsSync(f)) return '/@fs' + f;
  }
  throw new Error('pass --game <root> so engine/src/story/e2e.js can be found beside it');
}
async function loadPlaywright() {
  if (o.playwright) return import(pathToFileURL(resolve(o.playwright)).href);
  try { return await import('playwright'); } catch { /* not resolvable from here */ }
  if (o.game) {
    const game = resolve(o.game);
    for (const p of [join(game, '..', 'node_modules', 'playwright', 'index.mjs'),
                     join(game, '..', 'engine', 'node_modules', 'playwright', 'index.mjs')]) {
      if (existsSync(p)) return import(pathToFileURL(p).href);
    }
  }
  throw new Error('playwright not importable: pass --playwright <path to playwright/index.mjs>');
}
const ENGINE = await engineUrl();
const { chromium } = await loadPlaywright();
const OUT = resolve(o.out || join(process.cwd(), `probe-reading-${story.id}`));
const PAGE = `${o.url || 'http://localhost:5176'}/?e2e`;

mkdirSync(join(OUT, `shots-${vp}`), { recursive: true });
const gpu = process.platform === 'darwin' ? ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
const browser = await chromium.launch({ headless: true, args: gpu });
const ctx = await browser.newContext(VIEWPORTS[vp]);
const page = await ctx.newPage();
await page.exposeFunction('__shot2', async (name) => {
  const rel = `shots-${vp}/${name}.png`;
  await page.screenshot({ path: join(OUT, rel), scale: 'css' });
  return rel;
});
await page.goto(PAGE, { waitUntil: 'load' });
await page.waitForFunction(() => !!window.__storyE2E, null, { timeout: 90000 });
const speedSrc = await page.evaluate(async ([engineUrl]) => {
  const mod = await import(engineUrl);
  return String(mod.speedUpTime);
}, [ENGINE]);
const res = await page.evaluate(async ([s, t, speedSrc, targets, vp]) => {
  new Function(`(${speedSrc})(4)`)();
  const recs = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const shoot = async (name) => {
    if (name.endsWith('-more')) return 'x'; // the hints face: same beat, already probed
    const beat = name.split('-').slice(1).join('-');
    const modes = targets[beat] || [];
    if (!modes.length) return 'x';
    const book = document.querySelector('.eng-story-card .eng-story-book, .eng-story-book');
    if (!book || book.classList.contains('hidden')) { recs.push({ name, err: 'no book' }); return 'x'; }
    book.click();
    const ov = document.querySelector('.eng-reading');
    for (let i = 0; i < 240 && ov.classList.contains('hidden'); i++) await sleep(50);
    const box = ov.querySelector('.eng-reading-scroll');
    const done = () => {
      const tt = (ov.querySelector('.eng-reading-title')?.textContent || '').trim();
      const mathsOk = ![...box.querySelectorAll('.eng-math')].some((m) => m.classList.contains('pending') || m.classList.contains('fallback'));
      const imgsOk = [...box.querySelectorAll('img')].every((i) => i.complete);
      return tt && tt !== 'Loading…' && mathsOk && imgsOk;
    };
    for (let i = 0; i < 400 && !done(); i++) await sleep(50);
    const rec = { name, vp, title: (ov.querySelector('.eng-reading-title')?.textContent || '').trim(), modes };
    const maths = [...box.querySelectorAll('.eng-math')];
    const widths = maths.map((m) => Math.round(m.getBoundingClientRect().width));
    rec.boxClientW = box.clientWidth;
    rec.boxScrollW = box.scrollWidth;
    rec.mathCount = maths.length;
    rec.widestMath = widths.length ? Math.max(...widths) : 0;
    rec.overfullMaths = widths.filter((w) => w > box.clientWidth).map((w) => ({ w }));
    for (const mode of modes) {
      if (mode === 'math') {
        let anchor = null, aw = 0;
        for (const m of maths) {
          const w = m.getBoundingClientRect().width;
          if (w > aw) { aw = w; anchor = m; }
        }
        anchor = anchor || box.querySelector('p');
        box.scrollTop = anchor.offsetTop - 120;
        await sleep(120);
        rec.shotLeft = await window.__shot2(`read-${name}-math-left`);
        const before = box.scrollLeft;
        box.scrollLeft = 9999;
        await sleep(80);
        rec.hScrollPossible = box.scrollLeft > before;
        rec.maxScrollLeft = box.scrollLeft;
        rec.shotRight = await window.__shot2(`read-${name}-math-right`);
        box.scrollLeft = 0;
      }
      if (mode === 'figure') {
        const img = box.querySelector('img');
        if (img) {
          box.scrollTop = img.parentElement.offsetTop - 60;
          await sleep(300);
          rec.imgW = Math.round(img.getBoundingClientRect().width);
          rec.imgComplete = img.complete && img.naturalWidth > 0;
          rec.shotFigure = await window.__shot2(`read-${name}-figure`);
        } else rec.noFigure = true;
        box.scrollTop = 0;
      }
    }
    ov.querySelector('.eng-reading-back')?.click();
    for (let i = 0; i < 240 && !ov.classList.contains('hidden'); i++) await sleep(50);
    recs.push(rec);
    return 'x';
  };
  const r = await window.__storyE2E.run(s, t, { route: { name: 'main', choose: {} }, shoot });
  return { recs, ended: r.ended };
}, [story, tests, speedSrc, targets, vp]);
writeFileSync(join(OUT, `probe-reading-${vp}.json`), JSON.stringify({ story: story.id, engine: ENGINE, url: PAGE, ...res }, null, 2));
console.log(JSON.stringify(res.recs, null, 1));
await browser.close();
process.exit(res.recs.some((r) => r.err) ? 1 : 0);
