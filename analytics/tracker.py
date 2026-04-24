from __future__ import annotations
import asyncio
import csv
import io
import datetime
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands, tasks
import structlog
from core.vanish import ephemeral_for
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

class AnalyticsTracker(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._hourly: dict[int, dict] = {}
        self._active_users: dict[int, set] = {}

    async def cog_load(self) -> None:
        cfg = self.bot.config.analytics
        if cfg.enabled:
            self.snapshot_loop.change_interval(minutes=cfg.snapshot_interval_minutes)
            self.snapshot_loop.start()
            self.weekly_summary_loop.start()
            self.infraction_expiry_loop.start()
            log.info('analytics.started', interval_minutes=cfg.snapshot_interval_minutes)

    async def cog_unload(self) -> None:
        self.snapshot_loop.cancel()
        self.weekly_summary_loop.cancel()
        self.infraction_expiry_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        gid = message.guild.id
        h = self._hourly.setdefault(gid, self._zero_counters())
        h['messages'] += 1
        self._active_users.setdefault(gid, set()).add(message.author.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        h = self._hourly.setdefault(member.guild.id, self._zero_counters())
        h['new_members'] += 1

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        h = self._hourly.setdefault(member.guild.id, self._zero_counters())
        h['left_members'] += 1

    @staticmethod
    def _zero_counters() -> dict:
        return {'messages': 0, 'new_members': 0, 'left_members': 0}

    @tasks.loop(minutes=60)
    async def snapshot_loop(self) -> None:
        hour_str = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H')
        for i, guild in enumerate(self.bot.guilds):
            await asyncio.sleep(i)
            await self._take_snapshot(guild, hour_str)

    @snapshot_loop.before_loop
    async def before_snapshot(self) -> None:
        await self.bot.wait_until_ready()

    async def _take_snapshot(self, guild: discord.Guild, hour_str: str) -> None:
        gid = guild.id
        counters = self._hourly.pop(gid, self._zero_counters())
        active = len(self._active_users.pop(gid, set()))
        voice_minutes = await self._get_voice_minutes_last_hour(gid)
        try:
            await self.bot.db.execute('\n                INSERT INTO analytics_snapshots\n                    (guild_id, snapshot_hour, message_count, member_count,\n                     active_users, voice_minutes, new_members, left_members)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n                ON CONFLICT(guild_id, snapshot_hour) DO UPDATE SET\n                    message_count = excluded.message_count,\n                    member_count  = excluded.member_count,\n                    active_users  = excluded.active_users,\n                    voice_minutes = excluded.voice_minutes,\n                    new_members   = excluded.new_members,\n                    left_members  = excluded.left_members\n                ', (gid, hour_str, counters['messages'], guild.member_count, active, voice_minutes, counters['new_members'], counters['left_members']))
            log.info('analytics.snapshot', guild=gid, hour=hour_str, messages=counters['messages'], active=active)
        except Exception as exc:
            log.error('analytics.snapshot_failed', guild=gid, error=str(exc))

    async def _get_voice_minutes_last_hour(self, guild_id: int) -> int:
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).isoformat()
        val = await self.bot.db.fetch_val('\n            SELECT COALESCE(SUM(duration_seconds), 0) FROM voice_sessions\n            WHERE guild_id = CAST(? AS INTEGER) AND left_at >= ?\n            ', (guild_id, cutoff))
        result = int((val or 0) / 60)
        log.debug('analytics.voice_minutes_last_hour', guild=guild_id, minutes=result)
        return result

    @tasks.loop(hours=1)
    async def weekly_summary_loop(self) -> None:
        cfg = self.bot.config.analytics
        now = datetime.datetime.utcnow()
        if now.weekday() == cfg.weekly_summary_day and now.hour == cfg.weekly_summary_hour:
            for guild in self.bot.guilds:
                await self._post_weekly_summary(guild)

    @weekly_summary_loop.before_loop
    async def before_weekly(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_weekly_summary(self, guild: discord.Guild) -> None:
        cfg_discord = self.bot.config.get_guild_discord(guild.id)
        if not cfg_discord.status_channel_id:
            return
        channel = self.bot.get_channel(cfg_discord.status_channel_id)
        if not channel:
            return
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime('%Y-%m-%dT%H')
        rows = await self.bot.db.fetch_all('\n            SELECT * FROM analytics_snapshots\n            WHERE guild_id = CAST(? AS INTEGER) AND snapshot_hour >= ?\n            ORDER BY snapshot_hour\n            ', (guild.id, cutoff))
        log.debug('analytics.weekly_summary_rows', guild=guild.id, count=len(rows))
        if not rows:
            return
        total_msgs = sum((r['message_count'] for r in rows))
        total_voice = sum((r['voice_minutes'] for r in rows))
        peak_active = max((r['active_users'] for r in rows), default=0)
        new_members = sum((r['new_members'] for r in rows))
        left_members = sum((r['left_members'] for r in rows))
        embed = discord.Embed(title='📊 Weekly Summary', colour=5793266, description=f'7-day report for **{guild.name}**')
        embed.add_field(name='💬 Messages', value=f'{total_msgs:,}')
        embed.add_field(name='🎙️ Voice Minutes', value=f'{total_voice:,}')
        embed.add_field(name='👥 Peak Active/Hour', value=str(peak_active))
        embed.add_field(name='📥 Joined', value=str(new_members))
        embed.add_field(name='📤 Left', value=str(left_members))
        embed.add_field(name='👥 Current Members', value=str(guild.member_count))
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text='nonon analytics')
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error('analytics.summary_send_failed', guild=guild.id, error=str(exc))

    @tasks.loop(minutes=30)
    async def infraction_expiry_loop(self) -> None:
        from moderation.infractions import InfractionManager
        mgr = InfractionManager(self.bot)
        count = await mgr.expire_old_infractions()
        if count:
            log.info('infractions.auto_expired', count=count)

    @infraction_expiry_loop.before_loop
    async def before_expiry(self) -> None:
        await self.bot.wait_until_ready()

    async def _raw_counts(self, guild_id: int, cutoff_iso: str) -> dict:
        db = self.bot.db
        msg_count = await db.fetch_val('SELECT COUNT(*) FROM messages WHERE guild_id = CAST(? AS INTEGER) AND created_at >= ?', (guild_id, cutoff_iso))
        log.debug('analytics.raw.msg_count', guild=guild_id, count=msg_count)
        active_users = await db.fetch_val('SELECT COUNT(DISTINCT author_id) FROM messages WHERE guild_id = CAST(? AS INTEGER) AND created_at >= ?', (guild_id, cutoff_iso))
        log.debug('analytics.raw.active_users', guild=guild_id, count=active_users)
        voice_secs = await db.fetch_val('\n            SELECT COALESCE(SUM(duration_seconds), 0) FROM voice_sessions\n            WHERE guild_id = CAST(? AS INTEGER) AND joined_at >= ?\n            ', (guild_id, cutoff_iso))
        voice_mins = int((voice_secs or 0) / 60)
        log.debug('analytics.raw.voice_minutes', guild=guild_id, minutes=voice_mins)
        infraction_count = await db.fetch_val('SELECT COUNT(*) FROM infractions WHERE guild_id = CAST(? AS INTEGER) AND created_at >= ?', (guild_id, cutoff_iso))
        log.debug('analytics.raw.infractions', guild=guild_id, count=infraction_count)
        joins = await db.fetch_val("\n            SELECT COUNT(*) FROM member_events\n            WHERE guild_id = CAST(? AS INTEGER) AND event_type = 'join' AND occurred_at >= ?\n            ", (guild_id, cutoff_iso))
        leaves = await db.fetch_val("\n            SELECT COUNT(*) FROM member_events\n            WHERE guild_id = CAST(? AS INTEGER) AND event_type = 'leave' AND occurred_at >= ?\n            ", (guild_id, cutoff_iso))
        imported_count = await db.fetch_val("SELECT COUNT(*) FROM messages WHERE guild_id = CAST(? AS INTEGER) AND import_source = 'csv' AND created_at >= ?", (guild_id, cutoff_iso))
        log.debug('analytics.raw.imported_count', guild=guild_id, count=imported_count)
        bot_msg_count = await db.fetch_val('SELECT COUNT(*) FROM messages WHERE guild_id = CAST(? AS INTEGER) AND is_bot = 1 AND created_at >= ?', (guild_id, cutoff_iso))
        log.debug('analytics.raw.bot_msg_count', guild=guild_id, count=bot_msg_count)
        return {'messages': int(msg_count or 0), 'active_users': int(active_users or 0), 'voice_minutes': voice_mins, 'infractions': int(infraction_count or 0), 'joins': int(joins or 0), 'leaves': int(leaves or 0), 'imported': int(imported_count or 0), 'bot_messages': int(bot_msg_count or 0)}

    @app_commands.command(name='stats', description='Show guild activity statistics.')
    @app_commands.describe(days='Number of days to look back (default: 7)')
    async def stats(self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 90]=7) -> None:
        bot: KnowledgeBot = interaction.client
        if not await bot.resolver.can_use(interaction.user, 'stats', interaction.guild_id or 0):
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id or 0
        cutoff_hour = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H')
        cutoff_iso = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
        rows = await self.bot.db.fetch_all('\n            SELECT * FROM analytics_snapshots\n            WHERE guild_id = CAST(? AS INTEGER) AND snapshot_hour >= ?\n            ORDER BY snapshot_hour\n            ', (gid, cutoff_hour))
        log.debug('analytics.stats_snapshot_rows', guild=gid, count=len(rows))
        embed = discord.Embed(title=f'📊 Stats — Last {days} days', colour=5793266)
        if rows:
            embed.add_field(name='📋 Snapshot Messages', value=f"{sum((r['message_count'] for r in rows)):,}")
            embed.add_field(name='🎙️ Voice Minutes', value=f"{sum((r['voice_minutes'] for r in rows)):,}")
            embed.add_field(name='👥 Peak Active/Hour', value=str(max((r['active_users'] for r in rows), default=0)))
            embed.add_field(name='📑 Snapshot Records', value=str(len(rows)))
        raw = await self._raw_counts(gid, cutoff_iso)
        embed.add_field(name='📨 Messages (raw)', value=f"{raw['messages']:,}", inline=True)
        embed.add_field(name='🎤 Voice Min (raw)', value=f"{raw['voice_minutes']:,}", inline=True)
        embed.add_field(name='👤 Unique Authors', value=f"{raw['active_users']:,}", inline=True)
        embed.add_field(name='⚠️ Infractions', value=f"{raw['infractions']:,}", inline=True)
        embed.add_field(name='📥 Joins', value=f"{raw['joins']:,}", inline=True)
        embed.add_field(name='📤 Leaves', value=f"{raw['leaves']:,}", inline=True)
        embed.add_field(name='📦 Imported (hist)', value=f"{raw['imported']:,}", inline=True)
        embed.add_field(name='🤖 Bot Messages', value=f"{raw['bot_messages']:,}", inline=True)
        if not rows:
            embed.description = 'No hourly snapshots yet — showing raw table counts only.'
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text='nonon analytics')
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(embed=embed, ephemeral=eph)

    @app_commands.command(name='exportstats', description='Export analytics snapshots as CSV.')
    @app_commands.describe(days='Days to export (default: 30)')
    async def exportstats(self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 365]=30) -> None:
        bot: KnowledgeBot = interaction.client
        if not await bot.resolver.can_use(interaction.user, 'exportstats', interaction.guild_id or 0):
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id or 0
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H')
        rows = await self.bot.db.fetch_all('\n            SELECT * FROM analytics_snapshots\n            WHERE guild_id = CAST(? AS INTEGER) AND snapshot_hour >= ?\n            ORDER BY snapshot_hour\n            ', (gid, cutoff))
        log.debug('analytics.exportstats_rows', guild=gid, count=len(rows))
        fieldnames = ['snapshot_hour', 'message_count', 'member_count', 'active_users', 'voice_minutes', 'new_members', 'left_members']
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, 0) for k in fieldnames})
        buf.seek(0)
        fname = f'stats_{gid}_{days}d.csv'
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(f'📊 {len(rows)} snapshot rows exported.', file=discord.File(io.BytesIO(buf.read().encode()), filename=fname), ephemeral=eph)

    @app_commands.command(name='analyticsdebug', description='[Owner] Raw row-count dump from every analytics-relevant table.')
    async def analyticsdebug(self, interaction: discord.Interaction) -> None:
        bot: KnowledgeBot = interaction.client
        if interaction.user.id != bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild_id or 0
        db = self.bot.db

        async def gcount(table: str, extra_where: str='') -> str:
            try:
                q = f'SELECT COUNT(*) FROM {table} WHERE guild_id = CAST(? AS INTEGER)' + (f' AND {extra_where}' if extra_where else '')
                val = await db.fetch_val(q, (gid,))
                return str(int(val or 0))
            except Exception as exc:
                return f'*err: {exc}*'

        async def tcount(table: str) -> str:
            try:
                val = await db.fetch_val(f'SELECT COUNT(*) FROM {table}', ())
                return str(int(val or 0))
            except Exception as exc:
                return f'*err: {exc}*'
        lines = [f'**Analytics debug — guild `{gid}`**\n']
        for tbl in ['messages', 'message_edits', 'infractions', 'voice_sessions', 'voice_events', 'member_events', 'guild_events', 'audit_log', 'analytics_snapshots', 'permission_grants', 'scrape_jobs']:
            lines.append(f'`{tbl}`: {await gcount(tbl)}')
        for tbl in ['nickname_history', 'username_history', 'avatar_history', 'role_history']:
            lines.append(f'`{tbl}` (global): {await tcount(tbl)}')
        try:
            lines.append(f"`presence_events`: {await gcount('presence_events')}")
        except Exception:
            lines.append('`presence_events`: *table not yet created*')
        snap = await db.fetch_one('\n            SELECT snapshot_hour, message_count, member_count, active_users\n            FROM analytics_snapshots\n            WHERE guild_id = CAST(? AS INTEGER)\n            ORDER BY snapshot_hour DESC LIMIT 1\n            ', (gid,))
        if snap:
            lines.append(f"\n**Latest snapshot:** `{snap['snapshot_hour']}` — msgs={snap['message_count']} members={snap['member_count']} active={snap['active_users']}")
        else:
            lines.append('\n**Latest snapshot:** none yet.')
        text = '\n'.join(lines)
        if len(text) > 1980:
            text = text[:1977] + '…'
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(text, ephemeral=eph)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(AnalyticsTracker(_a))