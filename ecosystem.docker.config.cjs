/**
 * pm2 process definitions for the container.
 *
 * Separate from `ecosystem.config.cjs`, which is the local development pair
 * (Postgres.app on the host, Vite in dev mode with HMR). This one runs the
 * built UI and takes its database from the environment, so the two cannot be
 * confused for each other.
 *
 *   pm2-runtime start ecosystem.docker.config.cjs
 *
 * `pm2-runtime` rather than `pm2`: it runs in the foreground as PID 1's child,
 * forwards SIGTERM to both apps, and exits when they do — which is what makes
 * `docker stop` and an ECS task drain behave.
 */
const path = require('path')

const root = __dirname
const python = path.join(root, '.venv', 'bin', 'dex')

// Everything the container needs is already in the process environment, but
// pm2 does not pass it through to `env`-less apps, so the vars each app
// actually reads are named explicitly. An unset var stays unset rather than
// becoming the string "undefined".
const pass = (...names) =>
  Object.fromEntries(names.filter((n) => process.env[n] !== undefined).map((n) => [n, process.env[n]]))

module.exports = {
  apps: [
    {
      name: 'dex-api',
      script: python,
      cwd: root,
      interpreter: 'none', // a console-script shebang, not a JS file
      env: {
        DEX_HOST: process.env.DEX_HOST || '0.0.0.0',
        DEX_PORT: process.env.DEX_PORT || '4317',
        ...pass(
          'DEX_DATABASE_URL',
          'DEX_WORKSPACE',
          'DEX_ASSETS',
          'DEX_STATE',
          'DEX_PROJECT',
          'DEX_MODEL',
          'DEX_PLANNER_MODEL',
          'DEX_EFFORT',
          'DEX_MAX_TURNS',
          'DEX_TASK_TIMEOUT',
          'DEX_TOKEN',
          'DEX_FAKE_AGENT',
          // How the agent authenticates. Without one of these every task
          // fails at its first turn, so /api/health reports it.
          'ANTHROPIC_API_KEY',
          'ANTHROPIC_AUTH_TOKEN',
          'CLAUDE_CODE_OAUTH_TOKEN',
        ),
      },
      autorestart: true,
      max_restarts: 20,
      // Room for a crash loop to breathe instead of hammering Postgres.
      restart_delay: 2000,
      min_uptime: '20s',
      max_memory_restart: '1500M',
      // Container logs belong on stdout, for `docker logs` and CloudWatch.
      out_file: '/dev/stdout',
      error_file: '/dev/stderr',
      time: true,
    },
    {
      name: 'dex-web',
      // `vite preview` serves web/dist and proxies /api to the API above. It
      // is not the dev server: no HMR, no transform pipeline, no file watching.
      // The binary directly, not `npx`: npx reaches for the network when
      // resolution fails, and a container should fail loudly instead.
      script: path.join(root, 'node_modules', '.bin', 'vite'),
      args: `preview --config ${path.join(root, 'web', 'vite.config.ts')} --host 0.0.0.0 --port ${
        process.env.DEX_WEB_PORT || '4318'
      }`,
      cwd: path.join(root, 'web'),
      interpreter: 'none',
      env: {
        NODE_ENV: 'production',
        // Where preview sends /api. Same container, so loopback.
        DEX_API_ORIGIN: process.env.DEX_API_ORIGIN || `http://127.0.0.1:${process.env.DEX_PORT || '4317'}`,
        // Vite refuses requests whose Host header it does not recognise, a
        // DNS-rebinding guard. A deployment's hostname is not known at build
        // time, so it is named here. See the note in vite.config.ts.
        ...pass('DEX_ALLOWED_HOSTS'),
      },
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      min_uptime: '20s',
      max_memory_restart: '600M',
      out_file: '/dev/stdout',
      error_file: '/dev/stderr',
      time: true,
    },
  ],
}
