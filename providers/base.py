from __future__ import annotations
import abc
from dataclasses import dataclass, field
from typing import AsyncIterator

@dataclass
class SourceFile:
    path: str
    name: str
    content: str
    folder: str
    size_bytes: int = 0
    etag: str | None = None
    last_modified: str | None = None

@dataclass
class ProviderSnapshot:
    files: list[SourceFile] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def by_path(self) -> dict[str, SourceFile]:
        return {f.path: f for f in self.files}

class BaseProvider(abc.ABC):

    @abc.abstractmethod
    async def fetch_snapshot(self) -> ProviderSnapshot:
        ...

    @abc.abstractmethod
    async def watch(self) -> AsyncIterator[ProviderSnapshot]:
        ...

    async def close(self) -> None:
        pass

    @staticmethod
    def _matches_ignore(path: str, patterns: list[str]) -> bool:
        import fnmatch
        name = path.split('/')[-1]
        for pat in patterns:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
                return True
        return False

    @staticmethod
    def _is_text_file(filename: str) -> bool:
        TEXT_EXTS = {'.md', '.txt', '.rst', '.yaml', '.yml', '.json', '.toml', '.ini', '.cfg', '.csv', '.html', '.xml', '.py', '.js', '.ts', '.sh', '.bash'}
        import os
        _, ext = os.path.splitext(filename.lower())
        return ext in TEXT_EXTS