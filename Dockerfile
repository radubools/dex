# dex, as two processes under pm2 in one container.
#
# Stage 1 builds the UI; stage 2 runs the Python server and a production
# preview server for that build, both supervised by pm2-runtime.
#
# Deliberately does NOT install manim or piper. They pull in cairo, pango and a
# voice model and roughly quadruple the image; the server already reports manim
# as unavailable and degrades rather than failing tasks. Build with
# `--build-arg EXTRAS=manim,voice` if a project here needs rendered animations.

# ---------------------------------------------------------------- UI build ---
FROM node:22-bookworm-slim AS ui

WORKDIR /app
# Lockfile first so a source-only change does not reinstall the world.
COPY package.json package-lock.json ./
COPY web/package.json ./web/
RUN npm ci

COPY web ./web
COPY tsconfig*.json ./
# Fails the build on a type error rather than shipping a broken bundle.
RUN npm run build --workspace=web


# ------------------------------------------------------------------ runtime ---
FROM python:3.12-slim-bookworm AS runtime

ARG EXTRAS=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22 \
    # The agent's CLI and pm2 both want a writable home.
    HOME=/home/dex \
    DEX_WORKSPACE=/app \
    DEX_ASSETS=/data/assets \
    DEX_STATE=/data/state \
    DEX_HOST=0.0.0.0 \
    DEX_PORT=4317

# Node is needed for pm2 and for serving the built UI; ffmpeg is small and the
# animation tooling reads durations with it even when manim is absent.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git ffmpeg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && npm install -g pm2@5 \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# uv resolves from the committed lockfile, so the image gets the versions the
# tests ran against rather than whatever is newest today.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# The user is created before anything is copied, so nothing below needs a
# `chown -R` over /app. The first version ended that way and the recursive
# chown rewrote every file into a fresh 889MB layer — /app stays root-owned and
# the app only ever reads it.
RUN useradd --create-home --uid 10001 dex \
    && mkdir -p /data/assets /data/state \
    && chown dex:dex /data /data/assets /data/state

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Dependency install and the CLI check share one layer: as two, the `chmod`
# rewrote the whole .venv into another 223MB.
RUN if [ -n "$EXTRAS" ]; then \
        uv sync --frozen --no-dev --extra "$(echo "$EXTRAS" | tr ',' ' ' | sed 's/ / --extra /g')"; \
    else \
        uv sync --frozen --no-dev; \
    fi \
    && .venv/bin/python -c "import sys; from dex.runner import bundled_cli; \
       p = bundled_cli(); sys.exit(0) if p else sys.exit('no bundled Claude CLI in the image')" \
    && chmod +x "$(.venv/bin/python -c 'from dex.runner import bundled_cli; print(bundled_cli())')"

# The UI: its build output, plus the node_modules and config `vite preview`
# needs to serve and proxy it.
COPY --from=ui /app/web/dist ./web/dist
COPY --from=ui /app/node_modules ./node_modules
COPY web/package.json web/vite.config.ts ./web/
COPY package.json ./

COPY ecosystem.docker.config.cjs ./
# The starter guide for a new project. Without it every project created in the
# container is seeded with the stub, and every task in it stops to ask what to
# produce.
COPY AGENTS.template ./
COPY docker/entrypoint.sh /usr/local/bin/dex-entrypoint
COPY assets ./assets.seed
RUN chmod +x /usr/local/bin/dex-entrypoint

USER dex

EXPOSE 4317 4318

# pm2-runtime, not pm2: it stays in the foreground, forwards signals, and the
# container dies with it instead of outliving a crashed app.
ENTRYPOINT ["dex-entrypoint"]
CMD ["pm2-runtime", "start", "ecosystem.docker.config.cjs"]
