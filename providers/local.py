from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import AsyncIterator
import aiofiles
import structlog
from core.config import SourceConfig
from providers.base import BaseProvider, ProviderSnapshot, SourceFile
log: structlog.BoundLogger = structlog.get_logger(__name__)

class LocalProvider(BaseProvider):

    def __init__(self, config: SourceConfig) -> None:
        self._root = Path(config.path)
        self._ignore = config.ignore_patterns
        self._poll_interval = config.poll_interval_seconds
        self._debounce = config.debounce_seconds
        self._changed = asyncio.Event()
        self._observer: object | None = None

    async def fetch_snapshot(self) -> ProviderSnapshot:
        if not self._root.exists():
            return ProviderSnapshot(error=f'Path does not exist: {self._root}')
        try:
            files = await self._scan()
            return ProviderSnapshot(files=files)
        except Exception as exc:
            log.error('local.scan_error', path=str(self._root), error=str(exc))
            return ProviderSnapshot(error=str(exc))

    async def _scan(self) -> list[SourceFile]:
        results: list[SourceFile] = []
        loop = asyncio.get_running_loop()

        def _walk() -> list[tuple[str, str]]:
            found: list[tuple[str, str]] = []
            for dirpath, dirnames, filenames in os.walk(self._root):
                dirnames[:] = [d for d in dirnames if not self._matches_ignore(d, self._ignore)]
                for fn in filenames:
                    if self._matches_ignore(fn, self._ignore):
                        continue
                    if not self._is_text_file(fn):
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, self._root)
                    found.append((rel.replace('\\', '/'), full))
            return found
        pairs = await loop.run_in_executor(None, _walk)
        for rel_path, full_path in pairs:
            try:
                async with aiofiles.open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = await f.read()
                stat = os.stat(full_path)
                folder = rel_path.split('/')[0] if '/' in rel_path else ''
                results.append(SourceFile(path=rel_path, name=os.path.basename(rel_path), content=content, folder=folder, size_bytes=stat.st_size))
            except Exception as exc:
                log.warning('local.file_read_error', path=full_path, error=str(exc))
        return results

    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        self._start_watchdog()
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(self._wait_for_change()), timeout=float(self._poll_interval))
            except asyncio.TimeoutError:
                pass
            yield (await self.fetch_snapshot())

    async def _wait_for_change(self) -> None:
        self._changed.clear()
        await self._changed.wait()
        await asyncio.sleep(self._debounce)

    def _start_watchdog(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            changed = self._changed

            class _Handler(FileSystemEventHandler):

                def on_any_event(self, event: object) -> None:
                    changed.set()
            observer = Observer()
            observer.schedule(_Handler(), str(self._root), recursive=True)
            observer.daemon = True
            observer.start()
            self._observer = observer
            log.info('local.watchdog.started', path=str(self._root))
        except Exception as exc:
            log.warning('local.watchdog_unavailable', error=str(exc))

    async def close(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
            except Exception:
                pass