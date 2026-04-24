from __future__ import annotations
import asyncio
import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.config import load_config
from core.logging_setup import configure_logging
from database.connection import DatabasePool
EXPORTABLE_TABLES = {'infractions': 'SELECT * FROM infractions WHERE guild_id = ?', 'messages': 'SELECT * FROM messages WHERE guild_id = ?', 'voice_events': 'SELECT * FROM voice_events WHERE guild_id = ?', 'analytics_snapshots': 'SELECT * FROM analytics_snapshots WHERE guild_id = ?', 'member_events': 'SELECT * FROM member_events WHERE guild_id = ?', 'nickname_history': 'SELECT * FROM nickname_history WHERE guild_id = ?', 'role_history': 'SELECT * FROM role_history WHERE guild_id = ?', 'scrape_jobs': 'SELECT * FROM scrape_jobs WHERE guild_id = ?'}

async def run_export(_b: str, _o: str, _h: int, _g: str, _e: int | None) -> None:
    _a = load_config(_b)
    configure_logging(level='WARNING', json_logs=False, log_file='./logs/export.log')
    _k = DatabasePool(_a.database)
    await _k.initialise()
    _l = EXPORTABLE_TABLES.get(_o)
    if not _l:
        print(f"❌ Unknown table: {_o}. Available: {', '.join(EXPORTABLE_TABLES)}")
        await _k.close()
        sys.exit(1)
    _n = await _k.fetch_all(_l, (_h,))
    if _e and _n:
        _c = (datetime.utcnow() - timedelta(days=_e)).isoformat()
        _d = None
        for field in ('created_at', 'occurred_at', 'snapshot_hour'):
            if field in _n[0]:
                _d = field
                break
        if _d:
            _n = [_m for _m in _n if _m.get(_d, '') >= _c]
    _i = Path(_a.analytics.export_path)
    _i.mkdir(parents=True, exist_ok=True)
    _p = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    _j = _i / f'{_o}_{_h}_{_p}.{_g}'
    if _g == 'json':
        with open(_j, 'w', encoding='utf-8') as _f:
            json.dump([dict(_m) for _m in _n], _f, indent=2, default=str)
    else:
        if not _n:
            print(f'No rows found for guild {_h}.')
            await _k.close()
            return
        with open(_j, 'w', newline='', encoding='utf-8') as _f:
            _q = csv.DictWriter(_f, fieldnames=_n[0].keys())
            _q.writeheader()
            _q.writerows(_n)
    print(f'✅ Exported {len(_n)} rows to: {_j}')
    await _k.close()

def main() -> None:
    _a = argparse.ArgumentParser(description='nonon bot — data export tool')
    _a.add_argument('--config', default='config/config.yml')
    _a.add_argument('--table', required=True, help=f"Table to export: {', '.join(EXPORTABLE_TABLES)}")
    _a.add_argument('--guild', required=True, type=int, help='Guild ID')
    _a.add_argument('--format', dest='fmt', choices=['csv', 'json'], default='csv')
    _a.add_argument('--days', type=int, default=None, help='Limit to last N days')
    args = _a.parse_args()
    asyncio.run(run_export(args.config, args.table, args.guild, args.fmt, args.days))
if __name__ == '__main__':
    main()