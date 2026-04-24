from __future__ import annotations
import asyncio
import re
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
    from moderation.infractions import InfractionManager
log: structlog.BoundLogger = structlog.get_logger(__name__)
URL_PATTERN = re.compile('https?://[^\\s]+', re.IGNORECASE)
DOMAIN_PATTERN = re.compile('https?://(?:www\\.)?([^/\\s]+)', re.IGNORECASE)

class AutoMod(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._msg_timestamps: dict[tuple, deque] = defaultdict(lambda: deque())
        self._join_timestamps: dict[int, deque] = defaultdict(lambda: deque())
        self._raid_mode: dict[int, bool] = {}
        self._pattern_cache: dict[int, list[re.Pattern]] = {}

    def _get_patterns(self, guild_id: int) -> list[re.Pattern]:
        if guild_id not in self._pattern_cache:
            cfg = self.bot.config.get_guild_moderation(guild_id)
            self._pattern_cache[guild_id] = [re.compile(p, re.IGNORECASE) for p in cfg.banned_patterns]
        return self._pattern_cache[guild_id]

    def invalidate_pattern_cache(self, guild_id: int) -> None:
        self._pattern_cache.pop(guild_id, None)

    def _is_immune(self, member: discord.Member) -> bool:
        cfg = self.bot.config.get_guild_moderation(member.guild.id)
        immune_roles = set(cfg.mod_roles + cfg.admin_roles)
        return any((r.name in immune_roles for r in member.roles)) or member.guild_permissions.administrator

    async def _log_action(self, guild_id: int, action: str, user: discord.Member, reason: str) -> None:
        discord_cfg = self.bot.config.get_guild_discord(guild_id)
        if not discord_cfg.mod_log_channel_id:
            return
        channel = self.bot.get_channel(discord_cfg.mod_log_channel_id)
        if not channel:
            return
        embed = discord.Embed(title=f'🤖 AutoMod — {action}', colour=16729344, description=f'**User:** {user.mention} (`{user.id}`)\n**Reason:** {reason}')
        embed.set_footer(text=f'Guild {guild_id}')
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _warn_user(self, member: discord.Member, reason: str) -> None:
        from moderation.infractions import InfractionManager
        mgr = InfractionManager(self.bot)
        await mgr.add(member.guild.id, member.id, self.bot.user.id, 'warn', f'[AutoMod] {reason}')
        await self._log_action(member.guild.id, 'Warn', member, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        if self._is_immune(member):
            return
        guild_id = message.guild.id
        cfg = self.bot.config.get_guild_moderation(guild_id)
        content = message.content
        if cfg.automod_spam_enabled:
            key = (guild_id, member.id)
            now = time.monotonic()
            dq = self._msg_timestamps[key]
            dq.append(now)
            window = cfg.spam_window_seconds
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= cfg.spam_message_threshold:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self._warn_user(member, f'Spam ({len(dq)} msgs in {window}s)')
                dq.clear()
                return
        if cfg.automod_banned_words_enabled:
            lower = content.lower()
            for word in cfg.banned_words:
                if word.lower() in lower:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    await self._warn_user(member, f'Banned word: {word!r}')
                    return
        if cfg.automod_banned_patterns_enabled:
            for pat in self._get_patterns(guild_id):
                if pat.search(content):
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    await self._warn_user(member, f'Banned pattern: {pat.pattern!r}')
                    return
        if cfg.automod_mention_spam_enabled:
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count > cfg.max_mentions_per_message:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self._warn_user(member, f'Excessive mentions ({mention_count})')
                return
        if cfg.automod_link_filter_enabled and URL_PATTERN.search(content):
            domains = DOMAIN_PATTERN.findall(content)
            whitelist = {d.lower() for d in cfg.link_whitelist}
            bad_domains = [d for d in domains if d.lower() not in whitelist]
            if bad_domains:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self._warn_user(member, f"Disallowed link ({', '.join(bad_domains)})")
                return
        if cfg.automod_line_count_enabled:
            if content.count('\n') + 1 > cfg.max_lines_per_message:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await self._warn_user(member, 'Message too long (excessive lines)')
                return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_id = member.guild.id
        cfg = self.bot.config.get_guild_moderation(guild_id)
        if not cfg.automod_raid_detection_enabled:
            return
        now = time.monotonic()
        dq = self._join_timestamps[guild_id]
        dq.append(now)
        window = cfg.raid_join_window_seconds
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= cfg.raid_join_threshold and (not self._raid_mode.get(guild_id)):
            self._raid_mode[guild_id] = True
            log.warning('raid.detected', guild=guild_id, joins=len(dq), window=window)
            await self._activate_raid_mode(member.guild)

    async def _activate_raid_mode(self, guild: discord.Guild) -> None:
        discord_cfg = self.bot.config.get_guild_discord(guild.id)
        embed = discord.Embed(title='🚨 RAID DETECTED', colour=16711680, description='Unusual join activity detected. Verification level has been raised automatically. Use `/unlock` to restore normal verification when the threat is over.')
        try:
            await guild.edit(verification_level=discord.VerificationLevel.highest)
        except discord.HTTPException as exc:
            log.error('raid.verification_level_failed', error=str(exc))
        if discord_cfg.mod_log_channel_id:
            ch = self.bot.get_channel(discord_cfg.mod_log_channel_id)
            if ch:
                try:
                    await ch.send(embed=embed)
                except discord.HTTPException:
                    pass
        await asyncio.sleep(600)
        self._raid_mode[guild.id] = False
        self._join_timestamps[guild.id].clear()
        log.info('raid.mode_cleared', guild=guild.id)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(AutoMod(_a))