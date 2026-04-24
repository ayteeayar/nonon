from __future__ import annotations
import re
from typing import TYPE_CHECKING
import discord
import structlog
if TYPE_CHECKING:
    from database.connection import DatabasePool
log: structlog.BoundLogger = structlog.get_logger(__name__)
_URL_RE = re.compile('https?://\\S+', re.IGNORECASE)
EARN_ATTACHMENT = 5
EARN_LINK = 3
EARN_MESSAGE = 1

def _passive_amount(_a: discord.Message) -> int:
    if _a.attachments:
        return EARN_ATTACHMENT
    if _URL_RE.search(_a.content or ''):
        return EARN_LINK
    return EARN_MESSAGE

async def get_balance(_a: 'DatabasePool', _b: int, _d: int) -> int:
    _c = await _a.fetch_one('SELECT balance FROM chip_balances WHERE guild_id = ? AND user_id = ?', (_b, _d))
    return _c['balance'] if _c else 0

async def get_full_balance_row(_a: 'DatabasePool', _b: int, _d: int) -> dict:
    _c = await _a.fetch_one('SELECT balance, total_earned, total_spent FROM chip_balances WHERE guild_id = ? AND user_id = ?', (_b, _d))
    if _c:
        return dict(_c)
    return {'balance': 0, 'total_earned': 0, 'total_spent': 0}

async def _upsert(_b: 'DatabasePool', _e: int, _l: int, _c: int, _i: str) -> int:
    _j = await _b.fetch_one('SELECT balance, total_earned, total_spent FROM chip_balances WHERE guild_id = ? AND user_id = ?', (_e, _l))
    _a = _j['balance'] if _j else 0
    _d = _j['total_earned'] if _j else 0
    _k = _j['total_spent'] if _j else 0
    _f = _a + _c
    if _f < 0:
        raise ValueError(f'insufficient chips: have {_a}, need {abs(_c)}')
    _g = _d + max(_c, 0)
    _h = _k + max(-_c, 0)
    await _b.execute("\n        INSERT INTO chip_balances (guild_id, user_id, balance, total_earned, total_spent, updated_at)\n        VALUES (?, ?, ?, ?, ?, datetime('now'))\n        ON CONFLICT(guild_id, user_id) DO UPDATE SET\n            balance      = excluded.balance,\n            total_earned = excluded.total_earned,\n            total_spent  = excluded.total_spent,\n            updated_at   = excluded.updated_at\n        ", (_e, _l, _f, _g, _h))
    await _b.execute('INSERT INTO chip_transactions (guild_id, user_id, amount, reason) VALUES (?, ?, ?, ?)', (_e, _l, _c, _i))
    return _f

async def earn(_b: 'DatabasePool', _c: int, _e: int, _a: int, _d: str) -> int:
    return await _upsert(_b, _c, _e, _a, _d)

async def deduct(_b: 'DatabasePool', _c: int, _e: int, _a: int, _d: str) -> int:
    return await _upsert(_b, _c, _e, -_a, _d)

async def passive_earn_from_message(_b: 'DatabasePool', _c: discord.Message) -> None:
    if not _c.guild:
        return
    if _c.author.bot:
        return
    if _c.type not in (discord.MessageType.default, discord.MessageType.reply):
        return
    _a = _passive_amount(_c)
    try:
        await _upsert(_b, _c.guild.id, _c.author.id, _a, 'passive:attachment' if _c.attachments else 'passive:link' if _a == EARN_LINK else 'passive:message')
    except Exception as exc:
        log.warning('chips.passive_earn_failed', error=str(exc))

async def backfill_from_db(_a: 'DatabasePool', _c: int, _b: bool=False) -> dict[str, int]:
    log.info('chips.backfill.start', guild_id=_c, dry_run=_b)
    _e = await _a.fetch_all("\n        SELECT author_id,\n               COUNT(*) AS msg_count,\n               SUM(CASE WHEN attachment_filenames IS NOT NULL AND attachment_filenames != ''\n                        THEN 1 ELSE 0 END) AS attach_count,\n               SUM(CASE WHEN (attachment_filenames IS NULL OR attachment_filenames = '')\n                          AND content LIKE '%http%'\n                        THEN 1 ELSE 0 END) AS link_count,\n               SUM(CASE WHEN (attachment_filenames IS NULL OR attachment_filenames = '')\n                          AND content NOT LIKE '%http%'\n                        THEN 1 ELSE 0 END) AS plain_count\n        FROM messages\n        WHERE guild_id = ?\n          AND (is_bot = 0 OR is_bot IS NULL)\n        GROUP BY author_id\n        ", (_c,))
    _g = 0
    _f = 0
    _i = 0
    for _d in _e:
        _h = _d['author_id']
        chips = _d['attach_count'] * EARN_ATTACHMENT + _d['link_count'] * EARN_LINK + _d['plain_count'] * EARN_MESSAGE
        _g += _d['msg_count']
        _f += chips
        _i += 1
        if not _b and chips > 0:
            try:
                await _upsert(_a, _c, _h, chips, 'backfill:history')
            except Exception as exc:
                log.warning('chips.backfill.user_failed', guild_id=_c, user_id=_h, error=str(exc))
    log.info('chips.backfill.complete', guild_id=_c, dry_run=_b, total_messages=_g, total_chips=_f, users_affected=_i)
    return {'total_messages': _g, 'total_chips': _f, 'users_affected': _i}

async def get_jackpot(_a: 'DatabasePool', _b: int) -> int:
    _c = await _a.fetch_one('SELECT amount FROM casino_jackpots WHERE guild_id = ?', (_b,))
    if _c:
        return _c['amount']
    await _a.execute('INSERT OR IGNORE INTO casino_jackpots (guild_id) VALUES (?)', (_b,))
    return 10000

async def increment_jackpot(_b: 'DatabasePool', _c: int, _a: int) -> int:
    await _b.execute('\n        INSERT INTO casino_jackpots (guild_id, amount) VALUES (?, 10000 + ?)\n        ON CONFLICT(guild_id) DO UPDATE SET amount = amount + ?\n        ', (_c, _a, _a))
    _d = await _b.fetch_one('SELECT amount FROM casino_jackpots WHERE guild_id = ?', (_c,))
    return _d['amount'] if _d else 10000

async def claim_jackpot(_b: 'DatabasePool', _c: int, _e: int) -> int:
    _d = await _b.fetch_one('SELECT amount, seed FROM casino_jackpots WHERE guild_id = ?', (_c,))
    if not _d:
        return 0
    _a = _d['amount']
    await _b.execute("\n        UPDATE casino_jackpots\n        SET amount = seed, last_won_at = datetime('now'), last_won_by = ?\n        WHERE guild_id = ?\n        ", (_e, _c))
    return _a

async def record_game_result(_a: 'DatabasePool', _c: int, _f: int, _b: str, _g: int, _h: int, _e: str) -> None:
    _d = _h - _g
    await _a.execute("\n        INSERT INTO casino_game_stats\n            (guild_id, user_id, game, games_played, games_won, games_lost, games_pushed,\n             total_wagered, total_won, biggest_win, biggest_loss, updated_at)\n        VALUES (?, ?, ?, 1,\n                CASE WHEN ? = 'win'  THEN 1 ELSE 0 END,\n                CASE WHEN ? = 'loss' THEN 1 ELSE 0 END,\n                CASE WHEN ? = 'push' THEN 1 ELSE 0 END,\n                ?, ?,\n                CASE WHEN ? > 0 THEN ? ELSE 0 END,\n                CASE WHEN ? < 0 THEN ABS(?) ELSE 0 END,\n                datetime('now'))\n        ON CONFLICT(guild_id, user_id, game) DO UPDATE SET\n            games_played  = games_played  + 1,\n            games_won     = games_won     + CASE WHEN ? = 'win'  THEN 1 ELSE 0 END,\n            games_lost    = games_lost    + CASE WHEN ? = 'loss' THEN 1 ELSE 0 END,\n            games_pushed  = games_pushed  + CASE WHEN ? = 'push' THEN 1 ELSE 0 END,\n            total_wagered = total_wagered + ?,\n            total_won     = total_won     + ?,\n            biggest_win   = MAX(biggest_win,  CASE WHEN ? > 0 THEN ? ELSE 0 END),\n            biggest_loss  = MAX(biggest_loss, CASE WHEN ? < 0 THEN ABS(?) ELSE 0 END),\n            updated_at    = datetime('now')\n        ", (_c, _f, _b, _e, _e, _e, _g, _h, _d, _d, _d, _d, _e, _e, _e, _g, _h, _d, _d, _d, _d))