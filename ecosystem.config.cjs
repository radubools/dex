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

const root = __dirname
const python = path.join(root, '.venv', 'bin', 'dex')

module.exports = {
  apps: [
    {
      name: 'dex-api',
      script: python,
      cwd: root,
      interpreter: 'none', // it is a console-script shebang, not a JS file
      env: {
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
