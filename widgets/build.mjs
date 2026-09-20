/**
 * Build every widget, or the ones named, into `<widget>/dist/index.js`.
 *
 *   node widgets/build.mjs            # all of them
 *   node widgets/build.mjs pose-3d    # just one
 *
 * Uses Vite's library mode through its JS API rather than a config file per
 * widget: an agent authoring a widget should write `src/index.ts` and a
 * `widget.json`, and nothing else. Everything is bundled into the one ESM file,
 * because the browser loads it inside a sandboxed frame with an opaque origin —
 * it cannot fetch anything of its own, so it cannot have external imports.
 */
import { build } from 'vite'
import { readdirSync, statSync, existsSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const workspace = dirname(root)
const wanted = process.argv.slice(2)

/** Subdirectories of `base`, or nothing if it is not there. */
function dirsIn(base) {
  if (!existsSync(base)) return []
  return readdirSync(base)
    .map((name) => join(base, name))
    .filter((dir) => statSync(dir).isDirectory())
}

/** Every directory carrying a `widget.json`. */
function widgetsIn(base) {
  if (!existsSync(base)) return []
  return readdirSync(base)
    .map((name) => join(base, name))
    .filter((dir) => statSync(dir).isDirectory())
    .filter((dir) => existsSync(join(dir, 'widget.json')))
}

// A skill's widget is built where it lives, so the bundle it ships is the one
// that was committed with its source. Materialising copies the built directory
// into `widgets/`; building the copy instead would leave the skill holding a
// stale bundle, which is the artefact anyone else would receive.
// Every widget lives in a skill and is built there. There is no longer a copy
// anywhere else to keep in step.
const widgets = dirsIn(join(workspace, 'skills'))
  .flatMap((skill) => dirsIn(skill).flatMap((version) => widgetsIn(join(version, 'widgets'))))
  .filter((dir) => wanted.length === 0 || wanted.includes(dir.split('/').pop()))

if (widgets.length === 0) {
  console.error(wanted.length ? `no such widget: ${wanted.join(', ')}` : 'no widgets to build')
  process.exit(1)
}

for (const dir of widgets) {
  process.stdout.write(`building ${dir.replace(`${workspace}/`, '')}… `)
  await build({
    root: dir,
    configFile: false,
    logLevel: 'error',
    build: {
      lib: { entry: join(dir, 'src/index.ts'), formats: ['es'], fileName: () => 'index.js' },
      outDir: join(dir, 'dist'),
      emptyOutDir: true,
      // Sourcemaps would double the bytes the host inlines into the frame, and
      // a widget's stack traces already surface through the error bridge.
      sourcemap: false,
      target: 'es2022',
    },
  })
  console.log('ok')
}

// Where each widget's bundle ended up, for the test harness. A widget lives
// inside a versioned skill directory, so its path moves whenever that skill is
// republished — the harness cannot guess it from a name, and every spec asks
// for one by name. Regenerated on every build, which is when tests run.
writeFileSync(
  join(root, 'index.json'),
  JSON.stringify(
    Object.fromEntries(
      widgets.map((dir) => [
        dir.split('/').pop(),
        `${dir.replace(`${workspace}/`, '')}/dist/index.js`,
      ]),
    ),
    null,
    2,
  ) + '\n',
)
