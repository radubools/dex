"""The kernel's word that an ideation run changes nothing (design/24).

Ideation runs unattended and its commands are approved unasked. Looking at a
command cannot say what it writes: `python -c`, `sed -i` and a test that saves
a file all read as harmless. So every shell command it runs goes through
`sandbox-exec`, and the rule holds whatever the command says:

- it writes only the `.md` files directly in the run's own directory, and a
  scratch directory. A todo's directory is below that, so a spec cannot change
  after it was approved;
- it reaches no network but dex's test ports, so not the running dex or its
  Postgres: it cannot approve its own todo or start work.

`available()` false means no shell for ideation: refused, never run unconfined.
"""

from __future__ import annotations

import re
import shlex
import shutil
import tempfile
from pathlib import Path

#: AGENTS.md: the ports dex's tests may use. Seatbelt has no port ranges, and a
#: `localhost:*` allow cannot be narrowed afterwards by a deny.
TEST_PORTS = range(4400, 4500)

#: Vite's own caches: a game's tests and story e2e can't start without them,
#: and they are rebuilt at will, so nothing here is code or an asset.
BUILD_CACHE = r"/node_modules/[.]vite(-temp)?(/|$)"

#: Reach other processes rather than the disk, which the rules below can't see.
NO_EXEC = ("/usr/bin/open", "/usr/bin/osascript", "/bin/launchctl", "/usr/bin/crontab", "/usr/bin/at", "/usr/bin/sudo")


def available() -> bool:
    return shutil.which("sandbox-exec") is not None


def scratch_for(directory: Path) -> Path:
    """Where a run's temporary files and tool caches go."""
    return Path(tempfile.gettempdir()).resolve() / f"dex-ideation-{directory.name}"


def _quoted(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def profile(directory: Path, scratch: Path) -> str:
    own = re.escape(str(directory.resolve()))
    ports = "\n".join(f'(allow network-outbound (remote ip "localhost:{p}"))' for p in TEST_PORTS)
    programs = " ".join(f'(literal "{p}")' for p in NO_EXEC)
    return f"""(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (regex "{_quoted(f'^{own}/[^/]+[.]md$')}")
  (subpath "{_quoted(str(scratch))}")
  (regex "{_quoted(BUILD_CACHE)}")
  (regex "^/dev/"))
(deny process-exec {programs})
(deny network-outbound)
{ports}
"""


def confine(command: str, directory: Path) -> str:
    """`command`, as a run in `directory` is allowed to run it."""
    scratch = scratch_for(directory)
    (scratch / "npm").mkdir(parents=True, exist_ok=True)
    return shlex.join([
        "sandbox-exec", "-p", profile(directory, scratch),
        "/usr/bin/env", f"TMPDIR={scratch}", f"npm_config_cache={scratch / 'npm'}",
        # Writes that fail here would only be noise in a test's output.
        "PYTHONDONTWRITEBYTECODE=1", "PYTEST_ADDOPTS=-p no:cacheprovider",
        "/bin/zsh", "-c", command,
    ])
