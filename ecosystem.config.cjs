/**
 * pm2 process definitions for dex.
 *
 *   pm2 start ecosystem.config.cjs   # both servers
 *   pm2 save                         # remember them across reboots
 *   pm2 logs dex-api                 # follow one
 *
 * `.cjs` because package.json sets "type": "module" and pm2 reads this with
 * require().
 */
const path = require('path')
const fs = require('fs')

const root = __dirname
const python = path.join(root, '.venv', 'bin', 'dex')

/**
 * Secrets from a gitignored file rather than from this one, which is committed.
 * Absent is fine: dex then runs with no sign-in, exactly as it did before.
 */
function secrets(name) {
  const file = path.join(root, name)
  if (!fs.existsSync(file)) return {}
  return Object.fromEntries(
    fs
      .readFileSync(file, 'utf8')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'))
      .map((line) => {
        const at = line.indexOf('=')
        return [line.slice(0, at).trim(), line.slice(at + 1).trim()]
      })
      .filter(([key]) => key),
  )
}

// `.env.google.off` and `.env.auth.off` are the parked states. Renaming one to
// drop the `.off` is the whole switch: with a mechanism configured dex demands
// a sign-in, and with neither it behaves exactly as it always has. Kept
// explicit because a restart for any unrelated reason would otherwise have
// turned authentication on by surprise.
//
// Two files rather than one because the two mechanisms are independent and
// either can be on alone: `.env.google` holds the OAuth client, `.env.auth`
// turns on username and password sign-in.
const google = secrets('.env.google')
const localAuth = secrets('.env.auth')

module.exports = {
  apps: [
    {
      name: 'dex-api',
      script: python,
      // Restart the server when src/dex changes. uvicorn's own reloader, not
      // pm2's: it watches only `src/dex` and rebuilds the app in a child
      // process, so an edit to assets/ or web/ does not bounce the server.
      //
      // The cost is real and worth knowing: a reload kills whatever is running.
      // A task mid-flight is lost, and because dex's shutdown race can record a
      // killed task as `failed` rather than `paused`, editing backend code
      // while tasks run will leave failures behind. Pause work first, or drop
      // this argument while a long run matters.
      args: '--reload',
      cwd: root,
      interpreter: 'none', // it is a console-script shebang, not a JS file
      env: {
        ...google,
        ...localAuth,
        DEX_DATABASE_URL: 'postgresql://127.0.0.1/dex',
        DEX_PORT: '4317',
        // Postgres.app and homebrew are not on a daemon's default PATH.
        PATH: `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${
          process.env.PATH || ''
        }`,
      },
      autorestart: true,
      max_restarts: 20,
      // Give a crash loop room to breathe instead of hammering Postgres.
      restart_delay: 2000,
      min_uptime: '20s',
      max_memory_restart: '1500M',
      out_file: path.join(root, '.pm2-logs', 'dex-api.out.log'),
      error_file: path.join(root, '.pm2-logs', 'dex-api.err.log'),
      time: true,
    },
    {
      name: 'dex-web',
      script: 'npm',
      args: 'run dev:ui',
      cwd: root,
      interpreter: 'none',
      env: {
        PATH: `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${process.env.PATH || ''}`,
      },
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      min_uptime: '20s',
      out_file: path.join(root, '.pm2-logs', 'dex-web.out.log'),
      error_file: path.join(root, '.pm2-logs', 'dex-web.err.log'),
      time: true,
    },
  ],
}
