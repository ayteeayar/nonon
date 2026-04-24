from __future__ import annotations
import asyncio
import datetime
from typing import TYPE_CHECKING
import discord
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
INFRACTION_TYPES = frozenset({'warn', 'mute', 'kick', 'ban', 'softban', 'note'})

class InfractionManager:

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    async def add(self, guild_id: int, user_id: int, moderator_id: int, infraction_type: str, reason: str, duration_seconds: int | None=None) -> int:
        expires_at: str | None = None
        if duration_seconds:
            dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
            expires_at = dt.isoformat()
        row_id = await self.bot.db.execute_returning('\n            INSERT INTO infractions\n                (guild_id, user_id, moderator_id, infraction_type, reason,\n                 duration_seconds, active, expires_at)\n            VALUES (?, ?, ?, ?, ?, ?, 1, ?)\n            ', (guild_id, user_id, moderator_id, infraction_type, reason, duration_seconds, expires_at))
        log.info('infraction.added', id=row_id, guild=guild_id, user=user_id, type=infraction_type, reason=reason)
        await self._check_escalation(guild_id, user_id)
        return row_id

    async def get_user_infractions(self, guild_id: int, user_id: int) -> list[dict]:
        rows = await self.bot.db.fetch_all('\n            SELECT * FROM infractions\n            WHERE guild_id = ? AND user_id = ?\n            ORDER BY created_at DESC\n            ', (guild_id, user_id))
        return [dict(r) for r in rows]

    async def remove(self, infraction_id: int, resolved_by: int) -> bool:
        row = await self.bot.db.fetch_one('SELECT id FROM infractions WHERE id = ? AND active = 1', (infraction_id,))
        if not row:
            return False
        await self.bot.db.execute("\n            UPDATE infractions\n            SET active = 0, resolved_at = datetime('now'), resolved_by = ?\n            WHERE id = ?\n            ", (resolved_by, infraction_id))
        return True

    async def count_active_warnings(self, guild_id: int, user_id: int) -> int:
        val = await self.bot.db.fetch_val("\n            SELECT COUNT(*) FROM infractions\n            WHERE guild_id = ? AND user_id = ?\n              AND infraction_type = 'warn' AND active = 1\n            ", (guild_id, user_id))
        return int(val or 0)

    async def _check_escalation(self, guild_id: int, user_id: int) -> None:
        mod_cfg = self.bot.config.get_guild_moderation(guild_id)
        count = await self.count_active_warnings(guild_id, user_id)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return
        if count >= mod_cfg.auto_ban_threshold:
            try:
                await guild.ban(discord.Object(id=user_id), reason=f'Auto-ban: {count} active warnings')
                log.info('escalation.auto_ban', guild=guild_id, user=user_id, warnings=count)
            except discord.HTTPException as exc:
                log.error('escalation.ban_failed', error=str(exc))
        elif count >= mod_cfg.auto_mute_threshold:
            duration = datetime.timedelta(minutes=mod_cfg.mute_duration_minutes)
            try:
                await member.timeout(duration, reason=f'Auto-mute: {count} active warnings')
                log.info('escalation.auto_mute', guild=guild_id, user=user_id, warnings=count)
            except discord.HTTPException as exc:
                log.error('escalation.mute_failed', error=str(exc))

    async def expire_old_infractions(self) -> int:
        val = await self.bot.db.fetch_val("\n            SELECT COUNT(*) FROM infractions\n            WHERE active = 1 AND expires_at IS NOT NULL AND expires_at < datetime('now')\n            ")
        count = int(val or 0)
        if count:
            await self.bot.db.execute("\n                UPDATE infractions\n                SET active = 0, resolved_at = datetime('now')\n                WHERE active = 1 AND expires_at IS NOT NULL AND expires_at < datetime('now')\n                ")
            log.info('infractions.expired', count=count)
        return count

    @staticmethod
    def build_embed(records: list[dict], user: discord.User | discord.Member) -> discord.Embed:
        COLOURS = {'warn': 16753920, 'mute': 16776960, 'kick': 16737792, 'ban': 16711680, 'softban': 16724736, 'note': 8421504}
        embed = discord.Embed(title=f'📋 Infractions — {user}', colour=5793266)
        embed.set_thumbnail(url=user.display_avatar.url)
        if not records:
            embed.description = 'No infractions on record.'
            return embed
        for r in records[:10]:
            icon = '🔴' if r['active'] else '⚫'
            val = f"**Type:** {r['infraction_type']}\n**Reason:** {r['reason'] or 'No reason'}\n**Moderator:** <@{r['moderator_id']}>\n**Date:** {r['created_at'][:10]}"
            embed.add_field(name=f"{icon} #{r['id']}", value=val, inline=False)
        if len(records) > 10:
            embed.set_footer(text=f'+{len(records) - 10} more infractions not shown')
        return embed