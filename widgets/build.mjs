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
import { readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const wanted = process.argv.slice(2)

const widgets = readdirSync(root)
  .filter((name) => statSync(join(root, name)).isDirectory())
  .filter((name) => existsSync(join(root, name, 'widget.json')))
  .filter((name) => wanted.length === 0 || wanted.includes(name))

if (widgets.length === 0) {
  console.error(wanted.length ? `no such widget: ${wanted.join(', ')}` : 'no widgets to build')
  process.exit(1)
}

for (const name of widgets) {
  const dir = join(root, name)
  process.stdout.write(`building ${name}… `)
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
