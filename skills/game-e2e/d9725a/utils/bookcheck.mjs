#!/usr/bin/env node
// The book button, checked live: plays a story in its running game (the
// engine's `window.__storyE2E` driver, story passed from this file, so an
// out-of-date installed story.json is not used) and at every settled beat of
// every route verifies the card's reading wiring against the story's own
// `reading` fields: the book button shows on exactly the beats a passage
// carries (a named passage holds until the next one names another, and
// `"reading": false` hides it from that beat), clicking it opens the reading
// at that passage (title and "N of M" position from the installed reading
// package), and Back returns to the card. Writes <out>/bookcheck.json with
// every observation and the list of problems, and screenshots of the checked
// beats (cards and open reading views) under <out>/shots/.
//
//   node bookcheck.mjs --story <package dir> --game <game root> --out <dir>
//     [--url http://localhost:5176] [--viewports desktop,phone]
//     [--time-scale 4] [--shots id,id,...] [--scroll {"id": "text", ...}]
//     [--playwright <index.mjs>]
//
// --scroll maps a beat id to a text: after opening that beat's reading, the
// pane (.eng-reading-scroll) is scrolled to the paragraph containing the text
// and an extra `<beat>-scrolled` shot is taken, so below-the-fold display
// blocks can be reviewed. The scroll position is restored before closing.
//
// Exits 0 when there are no problems, 1 when there are, 2 on a setup failure.
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';

function args(argv) {
  const o = { url: 'http://localhost:5176', viewports: 'desktop,phone', 'time-scale': '4' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    o[a.slice(2)] = argv[++i];
  }
  return o;
}

async function loadPlaywright(o) {
  if (o.playwright) return import(pathToFileURL(resolve(o.playwright)).href);
  try { return await import('playwright'); } catch { /* not resolvable from here */ }
  // The engine sits beside the game; the games workspace hoists node_modules.
  const game = resolve(o.game);
  for (const p of [join(game, '..', 'node_modules', 'playwright', 'index.mjs'),
                   join(game, '..', 'engine', 'node_modules', 'playwright', 'index.mjs')]) {
    if (existsSync(p)) return import(pathToFileURL(p).href);
  }
  throw new Error('playwright not importable: pass --playwright <path to playwright/index.mjs>');
}

async function engineE2E(o) {
  const game = resolve(o.game);
  for (const base of [resolve(game, '..'), game]) {
    const f = join(base, 'engine', 'src', 'story', 'e2e.js');
    if (existsSync(f)) return import(pathToFileURL(f).href);
  }
  throw new Error(`no engine/src/story/e2e.js beside ${game}`);
}

const o = args(process.argv.slice(2));
if (!o.story || !o.game || !o.out) {
  console.error('usage: bookcheck.mjs --story <package dir> --game <game root> --out <dir> [--url U] [--viewports V] [--time-scale K] [--shots ids] [--playwright path]');
  process.exit(2);
}
const pkg = resolve(o.story);
const story = JSON.parse(readFileSync(join(pkg, 'story.json'), 'utf8'));
const tests = existsSync(join(pkg, 'e2e.json')) ? JSON.parse(readFileSync(join(pkg, 'e2e.json'), 'utf8')) : {};
if (!story.reading) { console.error(`story ${story.id} has no "reading" package — nothing to check`); process.exit(2); }
const reading = JSON.parse(readFileSync(join(resolve(o.game), 'public', 'assets', story.reading, 'reading.json'), 'utf8'));
const titles = Object.fromEntries(reading.passages.map((p) => [p.id, p.title]));
const order = reading.passages.map((p) => p.id);

// What the engine shows per beat (beatsToSteps in engine/src/story/e2e.js):
// the passage a beat names holds until the next names another, or `false`.
const expected = {};
{
  let passage = null;
  for (const b of story.beats) {
    if (b.reading !== undefined) passage = b.reading || null;
    expected[b.id] = story.reading && passage ? passage : null;
  }
}
// Beats to screenshot: the ones that name a passage, and the ones that turn
// the book off — presence and absence, both worth seeing.
const shots = o.shots ? o.shots.split(',').map((s) => s.trim()) :
  story.beats.filter((b) => typeof b.reading === 'string' || b.reading === false).map((b) => b.id);
const scrollTo = o.scroll ? JSON.parse(o.scroll) : {};

const { routesOf, speedUpTime } = await engineE2E(o);
const { chromium } = await loadPlaywright(o);
const VIEWPORTS = {
  desktop: { viewport: { width: 1280, height: 800 } },
  phone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};
const timeScale = Number(o['time-scale']);
const out = resolve(o.out);
mkdirSync(join(out, 'shots'), { recursive: true });
const gpu = process.platform === 'darwin' ? ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
const browser = await chromium.launch({ headless: true, args: gpu });
const all = [];
for (const vp of o.viewports.split(',').map((s) => s.trim()).filter((v) => v in VIEWPORTS)) {
  const ctx = await browser.newContext(VIEWPORTS[vp]);
  if (timeScale > 1) await ctx.addInitScript(speedUpTime, timeScale);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('requestfailed', (r) => { if (!/favicon/.test(r.url())) errors.push(`request failed: ${r.url()}`); });
  await page.exposeFunction('__bookShot', async (name) => {
    await page.screenshot({ path: join(out, 'shots', `${vp}-${name}.png`), scale: 'css' });
    return `${vp}-${name}.png`;
  });
  for (const route of routesOf(story, tests)) {
    const errs0 = errors.length;
    await page.goto(`${o.url}/?e2e`, { waitUntil: 'load' });
    await page.waitForFunction(() => !!window.__storyE2E, null, { timeout: 90000 });
    const log = await page.evaluate(async ([story, tests, route, expected, shots, scrollTo]) => {
      const out = [];
      const wait = async (fn, ms) => { const t0 = performance.now(); while (!fn()) { if (performance.now() - t0 > ms) return false; await new Promise((r) => setTimeout(r, 60)); } return true; };
      await window.__storyE2E.run(story, tests, {
        route,
        shoot: async (name) => {
          if (name.endsWith('-more')) return 'skip'; // the hints face: the driver opens it again right after
          const id = name.slice(4);
          const book = document.querySelector('.eng-story-book');
          const shown = !!book && !book.classList.contains('hidden') && book.getBoundingClientRect().width > 0;
          const rec = { id, route: route.name, shown, want: expected[id] || null };
          if (shown) {
            book.click();
            const el = document.querySelector('.eng-reading');
            const title = document.querySelector('.eng-reading-title');
            rec.opened = await wait(() => !el.classList.contains('hidden') && title.textContent.trim() && title.textContent.trim() !== 'Loading…', 12000);
            rec.paneTitle = title.textContent.trim();
            rec.pos = document.querySelector('.eng-reading-pos')?.textContent?.trim() || '';
            if (shots.includes(id)) await window.__bookShot(`${name}-reading`);
            const text = scrollTo[id];
            if (rec.opened && text) {
              // scroll the pane to the block naming this text and shoot it: the
              // below-the-fold review. Restore the position before closing.
              const sc = document.querySelector('.eng-reading-scroll');
              const top0 = sc.scrollTop;
              const p = [...document.querySelectorAll('.eng-reading-body p, .eng-reading-body li, .eng-reading-body h4')]
                .find((x) => x.textContent.includes(text));
              if (p) {
                p.scrollIntoView({ block: 'start' });
                await new Promise((r) => setTimeout(r, 200));
                rec.scrolled = { found: true, top: sc.scrollTop };
                await window.__bookShot(`${name}-scrolled`);
              } else {
                rec.scrolled = { found: false };
              }
              sc.scrollTop = top0;
            }
            document.querySelector('.eng-reading-back')?.click();
            rec.closed = await wait(() => el.classList.contains('hidden'), 4000);
          }
          if (shots.includes(id)) await window.__bookShot(`${name}-card`);
          out.push(rec);
          return name;
        },
      });
      return out;
    }, [story, tests, route, expected, shots, scrollTo]);
    const routeErrors = errors.slice(errs0);
    for (const rec of log) all.push({ vp, ...rec });
    if (routeErrors.length) all[all.length - 1].routeErrors = routeErrors; // attached once, listed per problem below
    console.log(`${vp} ${route.name}: ${log.length} beats checked${routeErrors.length ? ` (${routeErrors.length} page errors)` : ''}`);
  }
  await ctx.close();
}
await browser.close();

// Judge against the engine's expected state and the reading's own outline.
const problems = [];
for (const r of all) {
  const at = `${r.vp} ${r.route} ${r.id}`;
  if (r.want && !r.shown) problems.push(`${at}: book button missing (want passage ${r.want})`);
  if (!r.want && r.shown) problems.push(`${at}: book button shown, should be hidden`);
  if (r.want && r.shown) {
    if (!r.opened) problems.push(`${at}: reading view did not open`);
    else {
      if (r.paneTitle !== titles[r.want]) problems.push(`${at}: opened "${r.paneTitle}", want "${titles[r.want]}" (${r.want})`);
      const pos = `${order.indexOf(r.want) + 1} of ${order.length}`;
      if (r.pos && r.pos !== pos) problems.push(`${at}: position "${r.pos}", want "${pos}"`);
      if (r.scrolled && !r.scrolled.found) problems.push(`${at}: scroll text not found in the passage`);
      if (!r.closed) problems.push(`${at}: reading view did not close on Back`);
    }
  }
  for (const e of r.routeErrors || []) problems.push(`${at}: ${e}`);
}
const report = {
  story: { id: story.id, reading: story.reading, beats: story.beats.length },
  url: o.url, viewports: o.viewports, timeScale,
  when: new Date().toISOString(),
  passed: problems.length === 0, observations: all.length, problems, record: all,
};
writeFileSync(join(out, 'bookcheck.json'), JSON.stringify(report, null, 2) + '\n');
console.log(`${report.passed ? 'PASSED' : 'FAILED'}: ${out}/bookcheck.json (${all.length} beats)`);
for (const p of problems) console.error(p);
process.exit(report.passed ? 0 : 1);
