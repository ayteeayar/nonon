from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Sequence
import aiosqlite
import structlog
from core.config import DatabaseConfig
log: structlog.BoundLogger = structlog.get_logger(__name__)

class Row(dict):

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

class _SQLitePool:

    def __init__(self, path: str, pool_size: int=5) -> None:
        self._path = path
        self._pool_size = pool_size
        self._queue: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._connections: list[aiosqlite.Connection] = []
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        for _ in range(self._pool_size):
            conn = await aiosqlite.connect(self._path)
            conn.row_factory = aiosqlite.Row
            await conn.execute('PRAGMA journal_mode=WAL')
            await conn.execute('PRAGMA foreign_keys=ON')
            await conn.execute('PRAGMA synchronous=NORMAL')
            await conn.execute('PRAGMA busy_timeout=5000')
            self._connections.append(conn)
            await self._queue.put(conn)
        log.info('sqlite.pool.open', path=self._path, size=self._pool_size)

    async def close(self) -> None:
        for conn in self._connections:
            await conn.close()
        log.info('sqlite.pool.closed')

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await self._queue.get()
        try:
            yield conn
        finally:
            await self._queue.put(conn)

    async def execute(self, query: str, params: Sequence[Any]=()) -> None:
        async with self._write_lock:
            async with self.acquire() as conn:
                await conn.execute(query, params)
                await conn.commit()

    async def executemany(self, query: str, params_list: list[Sequence[Any]]) -> None:
        async with self._write_lock:
            async with self.acquire() as conn:
                await conn.executemany(query, params_list)
                await conn.commit()

    async def fetch_one(self, query: str, params: Sequence[Any]=()) -> Row | None:
        async with self.acquire() as conn:
            async with conn.execute(query, params) as cur:
                row = await cur.fetchone()
                if row is None:
                    return None
                return Row(dict(row))

    async def fetch_all(self, query: str, params: Sequence[Any]=()) -> list[Row]:
        async with self.acquire() as conn:
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [Row(dict(r)) for r in rows]

    async def fetch_val(self, query: str, params: Sequence[Any]=()) -> Any:
        row = await self.fetch_one(query, params)
        if row is None:
            return None
        return next(iter(row.values()))

    async def execute_returning(self, query: str, params: Sequence[Any]=()) -> int:
        async with self._write_lock:
            async with self.acquire() as conn:
                cur = await conn.execute(query, params)
                await conn.commit()
                return cur.lastrowid or 0

class _PostgresPool:

    def __init__(self, dsn: str, min_size: int, max_size: int) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None

    async def open(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=self._min_size, max_size=self._max_size)
        log.info('postgres.pool.open', min=self._min_size, max=self._max_size)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
        log.info('postgres.pool.closed')

    def _to_pg_query(self, query: str) -> str:
        idx = 0
        result = []
        for ch in query:
            if ch == '?':
                idx += 1
                result.append(f'${idx}')
            else:
                result.append(ch)
        return ''.join(result)

    async def execute(self, query: str, params: Sequence[Any]=()) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(self._to_pg_query(query), *params)

    async def executemany(self, query: str, params_list: list[Sequence[Any]]) -> None:
        async with self._pool.acquire() as conn:
            await conn.executemany(self._to_pg_query(query), params_list)

    async def fetch_one(self, query: str, params: Sequence[Any]=()) -> Row | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(self._to_pg_query(query), *params)
            if row is None:
                return None
            return Row(dict(row))

    async def fetch_all(self, query: str, params: Sequence[Any]=()) -> list[Row]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(self._to_pg_query(query), *params)
            return [Row(dict(r)) for r in rows]

    async def fetch_val(self, query: str, params: Sequence[Any]=()) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(self._to_pg_query(query), *params)

    async def execute_returning(self, query: str, params: Sequence[Any]=()) -> int:
        pg_query = self._to_pg_query(query)
        if 'RETURNING' not in pg_query.upper():
            pg_query += ' RETURNING id'
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(pg_query, *params)
            return int(val) if val is not None else 0

class DatabasePool:

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._backend: _SQLitePool | _PostgresPool | None = None

    async def initialise(self) -> None:
        cfg = self._config
        if cfg.backend == 'sqlite':
            self._backend = _SQLitePool(cfg.sqlite_path, pool_size=cfg.pool_min_size)
        else:
            dsn = os.environ.get(cfg.pg_dsn_env, '')
            if not dsn:
                raise RuntimeError(f'Environment variable {cfg.pg_dsn_env} is not set.')
            self._backend = _PostgresPool(dsn, cfg.pool_min_size, cfg.pool_max_size)
        await self._backend.open()
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        await self.execute("\n            CREATE TABLE IF NOT EXISTS schema_migrations (\n                version    TEXT PRIMARY KEY,\n                applied_at TEXT NOT NULL DEFAULT (datetime('now'))\n            )\n            ")
        migration_dir = Path(__file__).parent / 'migrations'
        sql_files = sorted(migration_dir.glob('*.sql'))
        for sql_file in sql_files:
            version = sql_file.stem.split('_')[0]
            existing = await self.fetch_val('SELECT version FROM schema_migrations WHERE version = ?', (version,))
            if existing:
                log.debug('migration.already_applied', version=version)
                continue
            sql = sql_file.read_text(encoding='utf-8')
            statements = [s.strip() for s in sql.split(';') if s.strip()]
            for stmt in statements:
                bare = '\n'.join((line for line in stmt.splitlines() if not line.strip().startswith('--'))).strip()
                if not bare:
                    continue
                try:
                    await self.execute(stmt)
                except Exception as exc:
                    log.warning('migration.stmt_error', version=version, error=str(exc))
            await self.execute('INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)', (version,))
            log.info('migration.applied', version=version)

    async def close(self) -> None:
        if self._backend:
            await self._backend.close()

    async def execute(self, query: str, params: Sequence[Any]=()) -> None:
        assert self._backend
        await self._backend.execute(query, params)

    async def executemany(self, query: str, params_list: list[Sequence[Any]]) -> None:
        assert self._backend
        await self._backend.executemany(query, params_list)

    async def fetch_one(self, query: str, params: Sequence[Any]=()) -> Row | None:
        assert self._backend
        return await self._backend.fetch_one(query, params)

    async def fetch_all(self, query: str, params: Sequence[Any]=()) -> list[Row]:
        assert self._backend
        return await self._backend.fetch_all(query, params)

    async def fetch_val(self, query: str, params: Sequence[Any]=()) -> Any:
        assert self._backend
        return await self._backend.fetch_val(query, params)

    async def execute_returning(self, query: str, params: Sequence[Any]=()) -> int:
        assert self._backend
        return await self._backend.execute_returning(query, params)