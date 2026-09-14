"""Runtime configuration, all overridable from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


#: Hard ceiling on workers, whatever the live setting says. One home for it, so
#: the queue, the store's clamp and the API's validation cannot drift apart.
MAX_WORKERS = 16

#: Used until the database has a value of its own.
DEFAULT_CONCURRENCY = 3

#: Tasks at once while shared-utility proposals are on. One at a time, because
#: a promotion has to land — and `utils/API.md` be regenerated — before the next
#: task reads the index. Six in parallel all read the same empty roster, several
#: write the same helper, and the operator answers the same question six times.
SEQUENTIAL_CONCURRENCY = 1

#: Restored when proposals are switched off. Deliberately not
#: DEFAULT_CONCURRENCY: that is the cold-start guess for a fresh install, while
#: this is the parallelism an operator turning the loop off is asking to get
#: back.
PARALLEL_CONCURRENCY = 6


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Config:
    #: Everything the agent is allowed to touch lives under here.
    workspace: Path = field(default_factory=lambda: _env_path("DEX_WORKSPACE", Path.cwd()))
    #: The assets root. Each project is a subdirectory of it, holding that
    #: project's AGENTS.md and one directory per task.
    assets_dir: Path = field(default_factory=lambda: _env_path("DEX_ASSETS", Path.cwd() / "assets"))
    #: Used when nothing names a project — the one that existed before projects.
    default_project: str = os.environ.get("DEX_PROJECT", "algorithms")
    #: Persisted chat threads live here.
    state_dir: Path = field(default_factory=lambda: _env_path("DEX_STATE", Path.cwd() / ".dex"))
    #: Where threads, tasks, queue state, and events are persisted.
    database_url: str = os.environ.get("DEX_DATABASE_URL", "postgresql://localhost/dex")
    model: str = os.environ.get("DEX_MODEL", "claude-opus-5")
    #: Planning is a short, cheap call; it does not need the big model.
    planner_model: str = os.environ.get("DEX_PLANNER_MODEL", "claude-sonnet-5")
    #: An agent run that has not finished by now is almost certainly stuck.
    task_timeout_s: float = float(os.environ.get("DEX_TASK_TIMEOUT", "2400"))
    max_turns: int = int(os.environ.get("DEX_MAX_TURNS", "160"))
    effort: str = os.environ.get("DEX_EFFORT", "high")
    host: str = os.environ.get("DEX_HOST", "0.0.0.0")
    port: int = int(os.environ.get("DEX_PORT", "4317"))
    #: Replay a scripted run instead of calling the model — for UI work offline.
    fake_agent: bool = os.environ.get("DEX_FAKE_AGENT") == "1"
    #: Optional shared secret. Unset (the default) means no auth at all, which
    #: is the sensible setting on a private network you trust. Still honoured
    #: when Google sign-in is configured: it is the service credential for
    #: health checks and the CLI, which have no browser to sign in with.
    token: str = os.environ.get("DEX_TOKEN", "")

    # --- Google sign-in -----------------------------------------------------
    #: From a Google Cloud OAuth 2.0 "Web application" client. Unset leaves
    #: sign-in switched off and dex behaving exactly as it did before.
    google_client_id: str = os.environ.get("DEX_GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.environ.get("DEX_GOOGLE_CLIENT_SECRET", "")
    #: The externally visible origin, e.g. https://d123.cloudfront.net. Google
    #: matches the redirect URI exactly, so this cannot be inferred from the
    #: request: a Host header is attacker-controlled and an origin guessed from
    #: one would let a forged Host redirect the code somewhere else.
    public_origin: str = os.environ.get("DEX_PUBLIC_ORIGIN", "http://localhost:4318")
    #: Addresses promoted to admin on sign-in, comma-separated. Needed to get
    #: the first admin in: with no admin, nobody can grant anyone a role.
    admin_emails: str = os.environ.get("DEX_ADMIN_EMAILS", "")
    #: Restrict sign-in to one Google Workspace domain, e.g. "example.com".
    #: Empty allows any Google account -- which is fine, because a new account
    #: lands with no role and can see nothing until granted one.
    google_hd: str = os.environ.get("DEX_GOOGLE_HD", "")

    # --- Username and password sign-in --------------------------------------
    #: Off unless asked for, the same as Google: switching it on turns an open
    #: install into one that demands a login, which is not something an
    #: upgrade should do by itself.
    password_auth: bool = os.environ.get("DEX_PASSWORD_AUTH", "") in {"1", "true", "yes", "on"}
    #: The account created when password sign-in is on and no admin exists yet.
    #: Something has to get the first admin in; a Google install does it with
    #: `DEX_ADMIN_EMAILS`, and this is the equivalent. dex forces a new password
    #: on first sign-in, so the default pair is a door, not a credential.
    seed_admin_username: str = os.environ.get("DEX_SEED_ADMIN_USERNAME", "admin")
    seed_admin_password: str = os.environ.get("DEX_SEED_ADMIN_PASSWORD", "admin")

    @property
    def google_enabled(self) -> bool:
        """Whether sign-in is configured. Both halves or neither."""
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def auth_enabled(self) -> bool:
        """Whether anyone has to sign in at all.

        Either mechanism is enough. This is what the access dependency asks,
        so that adding password sign-in to a Google install -- or running with
        only one of the two -- needs no further thought at the call sites.
        """
        return self.google_enabled or self.password_auth

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def redirect_uri(self) -> str:
        """What must be registered in the Google client, character for character."""
        return f"{self.public_origin.rstrip('/')}/api/auth/google/callback"

    @property
    def project(self) -> str:
        """Kept for callers that predate multiple projects."""
        return self.default_project

    def project_dir(self, project: str | None = None) -> Path:
        """Where a project's AGENTS.md and task directories live."""
        return self.assets_dir / (project or self.default_project)

    def guide_path(self, project: str | None = None) -> Path:
        return self.project_dir(project) / "AGENTS.md"

    @property
    def animation_cache(self) -> Path:
        """Derived animation variants; safe to delete at any time."""
        return self.state_dir / "animations"

    @property
    def threads_dir(self) -> Path:
        return self.state_dir / "threads"

    def ensure_dirs(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.project_dir().mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
