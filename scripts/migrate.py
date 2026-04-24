from __future__ import annotations
import asyncio
import argparse
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import aiosqlite
import structlog
from core.config import load_config
from core.logging_setup import configure_logging
log: structlog.BoundLogger = structlog.get_logger('migrate')
MIGRATIONS_DIR = Path(__file__).parent.parent / 'database' / 'migrations'

async def _ensure_migrations_table(_a: aiosqlite.Connection) -> None:
    await _a.execute("\n        CREATE TABLE IF NOT EXISTS schema_migrations (\n            version    TEXT PRIMARY KEY,\n            applied_at TEXT NOT NULL DEFAULT (datetime('now'))\n        )\n        ")
    await _a.commit()

async def _applied_versions(_a: aiosqlite.Connection) -> set[str]:
    async with _a.execute('SELECT version FROM schema_migrations') as _b:
        _d = await _b.fetchall()
    return {_c[0] for _c in _d}

async def _existing_columns(_a: aiosqlite.Connection, _e: str) -> set[str]:
    async with _a.execute(f'PRAGMA table_info({_e})') as _b:
        _d = await _b.fetchall()
    return {_c[1] for _c in _d}

async def _apply_migration(_d: aiosqlite.Connection, _f: Path) -> None:
    _h = _f.read_text(encoding='utf-8')
    _i = [_g.strip() for _g in _h.split(';') if _g.strip()]
    _c: dict[str, set[str]] = {}
    _a = re.compile('ALTER\\s+TABLE\\s+(\\w+)\\s+ADD\\s+COLUMN\\s+(\\w+)', re.IGNORECASE)
    for _j in _i:
        _e = _a.search(_j.strip())
        if _e:
            _k, _b = (_e.group(1).lower(), _e.group(2).lower())
            if _k not in _c:
                _c[_k] = await _existing_columns(_d, _k)
            if _b in _c[_k]:
                log.debug('migrate.column_exists_skip', table=_k, column=_b)
                continue
            _c.pop(_k, None)
        await _d.execute(_j)
    await _d.commit()
    log.info('migrate.applied', file=_f.name)

async def run_migrations(_c: str) -> None:
    _b = load_config(_c)
    configure_logging(level='INFO', json_logs=False, log_file='./logs/migrate.log')
    _e = _b.database.sqlite_path
    log.info('migrate.start', db=_e)
    async with aiosqlite.connect(_e) as _d:
        await _d.execute('PRAGMA journal_mode=WAL')
        await _d.execute('PRAGMA foreign_keys=ON')
        await _ensure_migrations_table(_d)
        _a = await _applied_versions(_d)
        _g = sorted(MIGRATIONS_DIR.glob('*.sql'))
        _i = [_f for _f in _g if _f.name[:3] not in _a]
        if not _i:
            log.info('migrate.up_to_date', applied=sorted(_a))
            print('Database is up to date.')
            return
        for _h in _i:
            _j = _h.name[:3]
            log.info('migrate.applying', version=_j, file=_h.name)
            print(f'  applying {_h.name}...')
            await _apply_migration(_d, _h)
        log.info('migrate.complete', applied_now=[_f.name for _f in _i])
        print(f'Migrations applied: {len(_i)}')

def main() -> None:
    _a = argparse.ArgumentParser(description='nonon bot — database migration runner')
    _a.add_argument('--config', default='config/config.yml', help='Path to config.yml')
    args = _a.parse_args()
    asyncio.run(run_migrations(args.config))
if __name__ == '__main__':
    main()