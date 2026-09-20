"""Watches the assets tree and reports new artifacts against their task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from watchfiles import Change, awatch

from .artifacts import KINDS, is_artifact
from .bus import EventBus
from .config import Config
from .models import Event
from .queue import TaskManager
from .store import PackageTagStore, read_manifest_tags

log = logging.getLogger("dex.watcher")


class AssetWatcher:
    """Emits an `asset` event the first time a generated file appears or changes.

    The UI uses this to light up viewers while a task is still running, rather
    than waiting for the whole package to finish.
    """

    def __init__(self, config: Config, bus: EventBus, tasks: TaskManager) -> None:
        self.config = config
        self.bus = bus
        self.tasks = tasks
        self.tags = PackageTagStore(tasks.db)
        self._runtime: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.config.ensure_dirs()
        # Manifests can be edited while the server is down — by an agent another
        # process ran, or by hand. Catching up here means the index is right
        # before anyone opens the Library, rather than right after.
        await self.catch_up()
        self._runtime = asyncio.create_task(self._watch(), name="dex-watcher")

    async def catch_up(self) -> None:
        """Bring the tag index in line with the manifests on disk."""
        root = self.config.assets_dir
        if not root.is_dir():
            return
        for project in (p for p in root.iterdir() if p.is_dir()):
            try:
                changed = await self.tags.reconcile(root, project.name)
            except Exception:  # an index is not worth failing startup over
                log.exception("could not index tags for %s", project.name)
                continue
            if changed:
                log.info("indexed tags for %d package(s) in %s", changed, project.name)

    async def stop(self) -> None:
        if self._runtime:
            self._runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runtime
            self._runtime = None

    async def _task_for(self, path: Path) -> tuple[str | None, str]:
        """Map a file to the task that owns its directory."""
        rel = path.relative_to(self.config.assets_dir)
        # <project>/<task-directory>/<file>
        directory = rel.parts[1] if len(rel.parts) > 1 else ""
        # Match on the directory a task writes into, not its slug: a resumed or
        # re-run attempt has its own slug but continues its parent's package.
        for task in self.tasks.live.values():
            if (task.output_slug or task.slug) == directory:
                return task.id, str(rel)
        # Not running here: ask Postgres, so assets still attach to a task that
        # another process ran or that finished before this server started.
        row = await self.tasks.db.pool.fetchrow(
            """SELECT id FROM tasks
               WHERE COALESCE(output_slug, slug) = $1
               ORDER BY created_at DESC LIMIT 1""",
            directory,
        )
        return (row["id"] if row else None), str(rel)

    async def _watch(self) -> None:
        async for changes in awatch(self.config.assets_dir, recursive=True):
            for change, raw in changes:
                await self._handle(change, raw)

    async def _handle(self, change: Change, raw: str) -> None:
        """Report one filesystem change as an asset event.

        Split out from the watch loop so it can be exercised directly: the loop
        itself never returns, which left this logic effectively untested.
        """
        path = Path(raw)
        if not is_artifact(path):
            return
        try:
            task_id, rel = await self._task_for(path)
        except ValueError:
            return  # outside the assets root

        # Deletions are reported too. Skipping them left the UI counting
        # scratch files an agent had written and then removed, so a task
        # showed more files than its directory actually holds.
        # Trust the filesystem over the reported change: watchfiles can batch a
        # create and a delete into a trailing `added` for a path that is already
        # gone, which would put a removed file back into the list.
        gone = change is Change.deleted or not path.exists()
        live = self.tasks.live.get(task_id) if task_id else None
        if live is not None:
            if gone and rel in live.artifacts:
                live.artifacts.remove(rel)
            elif not gone and rel not in live.artifacts:
                live.artifacts.append(rel)

        self.bus.publish(
            Event(
                type="asset",
                task_id=task_id,
                data={
                    "path": rel,
                    "kind": KINDS.get(path.suffix, "file"),
                    "bytes": path.stat().st_size if path.exists() else 0,
                    # What is actually true of the file, not merely what the
                    # watcher reported: the UI drops the path on "deleted", so a
                    # straggling "added" for a gone file would resurrect it.
                    "change": "deleted" if gone else change.name,
                },
            )
        )

        if path.name == "manifest.json":
            await self._reindex(path, gone)

    async def _reindex(self, manifest: Path, gone: bool) -> None:
        """Mirror one manifest's tags into the index and say so on the stream.

        The Library builds its filters from this, so a tag written by a task
        appears as a pill without anyone reloading.
        """
        rel = manifest.relative_to(self.config.assets_dir)
        if len(rel.parts) < 3:
            return  # <project>/<package>/manifest.json, nothing shallower
        project, slug = rel.parts[0], rel.parts[1]
        tags = [] if gone else read_manifest_tags(manifest)
        try:
            if gone:
                await self.tags.forget(project, slug)
            else:
                await self.tags.set(project, slug, tags)
        except Exception:
            log.exception("could not index tags for %s/%s", project, slug)
            return
        self.bus.publish(
            Event(
                type="tags",
                data={"project": project, "slug": slug, "tags": tags, "removed": gone},
            )
        )
