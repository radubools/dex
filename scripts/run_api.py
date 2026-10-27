"""Start dex's server with a clean environment: what pm2 runs as dex-api.

pm2 hands a process the environment of whichever shell started or restarted
it (`--update-env` merges it in, and pm2 7 ignores `filter_env`). Started from
a Claude app session, that environment carries the app's own ANTHROPIC_BASE_URL
and CLAUDE_CODE_* settings, and every task then fails with "Invalid API key".
So dex is never given that environment: this keeps only the variables named
here, and takes the Claude credential from one place, ~/.zshrc's

    export CLAUDE_CODE_OAUTH_TOKEN=...

(else `.env.claude` beside this repo). The token is read here, at start, so it
is never stored in pm2's saved process list either.

    .venv/bin/python scripts/run_api.py --reload     # what ecosystem.config.cjs runs
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Besides DEX_*: what a process needs to find its home, its tools and its locale.
KEEP = {"HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "PATH"}
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"


def token_from_zshrc(path: Path = Path.home() / ".zshrc") -> str:
    """The last `export CLAUDE_CODE_OAUTH_TOKEN=…` in ~/.zshrc, unquoted ('' if none)."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    found = ""
    for line in lines:
        m = re.match(rf"\s*(?:export\s+)?{TOKEN}=(.*)$", line)
        if m:
            value = m.group(1).strip()
            if value[:1] in "\"'" and value[-1:] == value[:1]:
                value = value[1:-1]
            found = value.split()[0] if value else ""
    return found


def token_from_env_file(path: Path = ROOT / ".env.claude") -> str:
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == TOKEN:
                return value.strip()
    except OSError:
        pass
    return ""


def clean_env(environ: dict[str, str]) -> dict[str, str]:
    """Only KEEP and DEX_* from `environ`, plus the token (nothing else Claude or Anthropic)."""
    env = {k: v for k, v in environ.items() if k in KEEP or k.startswith("DEX_")}
    token = token_from_zshrc() or token_from_env_file()
    if token:
        env[TOKEN] = token
    return env


def main() -> None:
    dex = ROOT / ".venv" / "bin" / "dex"
    os.execve(dex, [str(dex), *sys.argv[1:]], clean_env(dict(os.environ)))


if __name__ == "__main__":
    main()
