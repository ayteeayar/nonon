from __future__ import annotations
import datetime
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_ACTIVITY_TYPE_MAP: dict[discord.ActivityType, str] = {discord.ActivityType.playing: 'playing', discord.ActivityType.streaming: 'streaming', discord.ActivityType.listening: 'listening', discord.ActivityType.watching: 'watching', discord.ActivityType.custom: 'custom', discord.ActivityType.competing: 'competing', discord.ActivityType.unknown: 'unknown'}
_ACTIVITY_PRIORITY: dict[str, int] = {'streaming': 0, 'playing': 1, 'listening': 2, 'watching': 3, 'competing': 4, 'custom': 5, 'unknown': 6}

def _pick_activity(_a: list[discord.BaseActivity | discord.Activity | discord.Spotify | discord.Game | discord.Streaming | discord.CustomActivity]) -> discord.BaseActivity | None:
    if not _a:
        return None
    return min(_a, key=lambda a: _ACTIVITY_PRIORITY.get(_ACTIVITY_TYPE_MAP.get(getattr(a, 'type', discord.ActivityType.unknown), 'unknown'), 99))

def _extract_activity_fields(_a: discord.BaseActivity | None) -> dict:
    if _a is None:
        return {'activity_type': None, 'activity_name': None, 'activity_detail': None, 'activity_state': None, 'streaming_url': None}
    _b = _ACTIVITY_TYPE_MAP.get(getattr(_a, 'type', discord.ActivityType.unknown), 'unknown')
    _e: str | None = getattr(_a, 'name', None)
    _c: str | None = getattr(_a, 'details', None)
    _f: str | None = getattr(_a, 'state', None)
    _g: str | None = getattr(_a, 'url', None) if _b == 'streaming' else None
    if isinstance(_a, discord.Spotify):
        _e = f"{_a.title} — {', '.join(_a.artists)}"
    if _b == 'custom' and hasattr(_a, 'emoji'):
        _d = str(_a.emoji) if _a.emoji else ''
        _e = f"{_d} {_e or ''}".strip()
    return {'activity_type': _b, 'activity_name': _e, 'activity_detail': _c, 'activity_state': _f, 'streaming_url': _g}

class PresenceTracker(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._last_recorded: dict[int, dict[int, str]] = {}

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        cfg_logging = self.bot.config.get_guild_logging(after.guild.id)
        if not cfg_logging.log_presence_updates:
            return
        gid = after.guild.id
        uid = after.id
        status_changed = before.status != after.status
        before_act = _pick_activity(list(before.activities))
        after_act = _pick_activity(list(after.activities))
        before_fields = _extract_activity_fields(before_act)
        after_fields = _extract_activity_fields(after_act)
        activity_changed = before_fields != after_fields
        if not status_changed and (not activity_changed):
            return
        now = datetime.datetime.utcnow()
        now_iso = now.isoformat()
        min_interval = cfg_logging.presence_min_interval_seconds
        user_last = self._last_recorded.get(gid, {}).get(uid)
        if user_last:
            last_dt = datetime.datetime.fromisoformat(user_last)
            if (now - last_dt).total_seconds() < min_interval:
                log.debug('presence.throttled', guild=gid, user=uid, seconds_since_last=round((now - last_dt).total_seconds(), 1))
                return
        status_str = str(after.status)
        try:
            await self.bot.db.execute('\n                INSERT INTO presence_events\n                    (guild_id, user_id, recorded_at, status,\n                     activity_type, activity_name, activity_detail,\n                     activity_state, streaming_url)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ', (gid, uid, now_iso, status_str, after_fields['activity_type'], after_fields['activity_name'], after_fields['activity_detail'], after_fields['activity_state'], after_fields['streaming_url']))
            self._last_recorded.setdefault(gid, {})[uid] = now_iso
            log.debug('presence.recorded', guild=gid, user=uid, status=status_str, activity=after_fields['activity_type'], name=after_fields['activity_name'])
        except Exception as exc:
            log.error('presence.insert_failed', guild=gid, user=uid, error=str(exc))

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(PresenceTracker(_a))