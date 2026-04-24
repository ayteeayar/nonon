from __future__ import annotations
import asyncio
import base64
import os
from typing import AsyncIterator
import aiohttp
import structlog
from core.config import SourceConfig
from providers.base import BaseProvider, ProviderSnapshot, SourceFile
log: structlog.BoundLogger = structlog.get_logger(__name__)
GITHUB_API = 'https://api.github.com'

class GitHubProvider(BaseProvider):

    def __init__(self, config: SourceConfig) -> None:
        self._repo = config.github_repo
        self._branch = config.github_branch
        self._token = os.environ.get(config.github_token_env, '')
        self._ignore = config.ignore_patterns
        self._poll_interval = config.poll_interval_seconds
        self._session: aiohttp.ClientSession | None = None
        self._etag: str | None = None
        self._last_sha: str | None = None

    def _headers(self) -> dict[str, str]:
        h = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if self._token:
            h['Authorization'] = f'Bearer {self._token}'
        return h

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers())
        return self._session

    async def fetch_snapshot(self) -> ProviderSnapshot:
        try:
            tree = await self._fetch_tree()
            if tree is None:
                return ProviderSnapshot(files=[])
            files = await self._fetch_files(tree)
            return ProviderSnapshot(files=files)
        except Exception as exc:
            log.error('github.fetch_error', repo=self._repo, error=str(exc))
            return ProviderSnapshot(error=str(exc))

    async def _fetch_tree(self) -> list[dict] | None:
        session = await self._get_session()
        url = f'{GITHUB_API}/repos/{self._repo}/git/trees/{self._branch}?recursive=1'
        headers: dict[str, str] = {}
        if self._etag:
            headers['If-None-Match'] = self._etag
        async with session.get(url, headers=headers) as resp:
            if resp.status == 304:
                return None
            if resp.status == 200:
                self._etag = resp.headers.get('ETag')
                data = await resp.json()
                return [item for item in data.get('tree', []) if item.get('type') == 'blob' and self._is_text_file(item['path']) and (not self._matches_ignore(item['path'], self._ignore))]
            log.error('github.tree_error', status=resp.status, repo=self._repo)
            return None

    async def _fetch_files(self, tree: list[dict]) -> list[SourceFile]:
        session = await self._get_session()
        files: list[SourceFile] = []

        async def _fetch_one(item: dict) -> SourceFile | None:
            url = f"{GITHUB_API}/repos/{self._repo}/contents/{item['path']}?ref={self._branch}"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    content_b64 = data.get('content', '')
                    content = base64.b64decode(content_b64.replace('\n', '')).decode('utf-8', errors='replace')
                    path = item['path']
                    parts = path.split('/')
                    folder = parts[0] if len(parts) > 1 else ''
                    return SourceFile(path=path, name=parts[-1], content=content, folder=folder, size_bytes=item.get('size', 0), etag=data.get('sha'))
            except Exception as exc:
                log.warning('github.file_fetch_error', path=item['path'], error=str(exc))
                return None
        tasks = [_fetch_one(item) for item in tree]
        batch_size = 10
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            results = await asyncio.gather(*batch)
            files.extend((r for r in results if r is not None))
            await asyncio.sleep(0.5)
        return files

    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        while True:
            await asyncio.sleep(self._poll_interval)
            snap = await self.fetch_snapshot()
            if snap.ok:
                yield snap

    async def close(self) -> None:
        if self._session and (not self._session.closed):
            await self._session.close()