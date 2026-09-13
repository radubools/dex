#!/bin/sh
# Prepare the writable volumes, then hand off to pm2.
#
# `exec` at the end matters: pm2-runtime becomes PID 1's direct child and gets
# the SIGTERM from `docker stop`, so running tasks are marked preempted and
# paused rather than killed mid-run.
set -eu

ASSETS="${DEX_ASSETS:-/data/assets}"
STATE="${DEX_STATE:-/data/state}"
SEED="/app/assets.seed"

mkdir -p "$ASSETS" "$STATE"

# A named volume starts empty. Without the project guides a task has nothing
# defining what it should produce, so it stops and asks — for every task. Seed
# once, and never overwrite: after the first boot the volume is the truth.
if [ -d "$SEED" ] && [ -z "$(ls -A "$ASSETS" 2>/dev/null)" ]; then
  echo "seeding $ASSETS from the image"
  cp -R "$SEED/." "$ASSETS/"
fi

# Say plainly what is missing, at the top of the logs, rather than letting the
# first task fail with something obscure.
if [ -z "${ANTHROPIC_API_KEY:-}${ANTHROPIC_AUTH_TOKEN:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "WARNING: no Claude credentials in the environment — the UI will load," >&2
  echo "         but every task will fail on its first turn. Set ANTHROPIC_API_KEY." >&2
fi
if [ -z "${DEX_TOKEN:-}" ]; then
  echo "WARNING: DEX_TOKEN is empty — the API is unauthenticated. Anyone who can" >&2
  echo "         reach this port can run tasks. Fine on a private network; not" >&2
  echo "         fine on a public address." >&2
fi

exec "$@"
