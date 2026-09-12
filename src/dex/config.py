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
    #: is the sensible setting on a private network you trust.
    token: str = os.environ.get("DEX_TOKEN", "")

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
