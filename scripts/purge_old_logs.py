from __future__ import annotations
import asyncio
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.config import load_config
from core.logging_setup import configure_logging
from database.connection import DatabasePool
PURGEABLE: list[tuple[str, str]] = [('messages', 'created_at'), ('message_edits', 'edited_at'), ('voice_events', 'occurred_at'), ('member_events', 'occurred_at'), ('guild_events', 'occurred_at'), ('audit_log', 'occurred_at'), ('nickname_history', 'changed_at'), ('username_history', 'changed_at'), ('avatar_history', 'changed_at'), ('role_history', 'changed_at'), ('analytics_snapshots', 'snapshot_hour')]

async def purge(_b: str, _g: bool) -> None:
    _a = load_config(_b)
    configure_logging(level='INFO', json_logs=False, log_file='./logs/purge.log')
    _h = DatabasePool(_a.database)
    await _h.initialise()
    _i = _a.logging.retention_days
    _e = (datetime.utcnow() - timedelta(days=_i)).isoformat()
    print(f'Purging records older than {_i} days (before {_e[:10]})')
    print('DRY RUN — no changes will be made.' if _g else '')
    _k = 0
    for _j, _f in PURGEABLE:
        try:
            _d = await _h.fetch_val(f'SELECT COUNT(*) FROM {_j} WHERE {_f} < ?', (_e,))
            _c = int(_d or 0)
            if _c == 0:
                continue
            if not _g:
                await _h.execute(f'DELETE FROM {_j} WHERE {_f} < ?', (_e,))
            print(f"  {('Would delete' if _g else 'Deleted')} {_c} rows from {_j}")
            _k += _c
        except Exception as exc:
            print(f'  ⚠️  Error pruning {_j}: {exc}')
    print(f"\nTotal: {('would remove' if _g else 'removed')} {_k} rows.")
    await _h.close()

def main() -> None:
    _a = argparse.ArgumentParser(description='nonon bot — log retention enforcer')
    _a.add_argument('--config', default='config/config.yml')
    _a.add_argument('--dry-run', action='store_true', help='Preview deletions without executing')
    args = _a.parse_args()
    asyncio.run(purge(args.config, args.dry_run))
if __name__ == '__main__':
    main()