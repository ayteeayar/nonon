from __future__ import annotations
import datetime
import math
from typing import TYPE_CHECKING, Optional
import discord
from discord import app_commands, ui
from discord.ext import commands
import structlog
from core.permissions import SCOPE_COMMANDS
from core.vanish import ephemeral_for
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

async def _check_lookup(_c: discord.Interaction) -> bool:
    _b: KnowledgeBot = _c.client
    _a = await _b.resolver.can_use(_c.user, 'lookup', _c.guild_id or 0)
    if not _a:
        await _c.response.send_message('❌ Permission denied.', ephemeral=True)
    return _a

class MessagePaginator(ui.View):

    def __init__(self, pages: list[discord.Embed], *, timeout: float=120) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.idx = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.idx == 0
        self.next_btn.disabled = self.idx >= len(self.pages) - 1

    @ui.button(label='◀ Prev', style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.idx = max(0, self.idx - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.idx], view=self)

    @ui.button(label='Next ▶', style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.idx = min(len(self.pages) - 1, self.idx + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.idx], view=self)

async def _resolve_user(_a: 'KnowledgeBot', _b: discord.Guild | None, _d: int) -> discord.User | discord.Member | None:
    if _b:
        _c = _b.get_member(_d)
        if _c:
            return _c
    try:
        return await _a.fetch_user(_d)
    except discord.NotFound:
        return None

def _paginate_lines(_f: list[str], _j: str, _b: int, _g: int=3800) -> list[discord.Embed]:
    _i: list[discord.Embed] = []
    _a: list[str] = []
    _c = 0
    _h = 1

    def flush() -> None:
        nonlocal page_num
        _d = discord.Embed(title=f'{_j} (page {_h})', description='\n'.join(_a), colour=_b)
        _i.append(_d)
        _h += 1
        _a.clear()
    for _e in _f:
        if _c + len(_e) + 1 > _g and _a:
            flush()
            _c = 0
        _a.append(_e)
        _c += len(_e) + 1
    if _a:
        flush()
    return _i or [discord.Embed(title=_j, description='No data.', colour=_b)]

class LookupCommands(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
    lookup_group = app_commands.Group(name='lookup', description='User intelligence lookups (owner / lookup scope).')

    @lookup_group.command(name='profile', description='Comprehensive user profile report.')
    @app_commands.describe(user_id='Discord user ID to look up')
    async def profile(self, interaction: discord.Interaction, user_id: str) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send('❌ Invalid user ID.', ephemeral=True)
            return
        guild = interaction.guild
        gid = interaction.guild_id or 0
        db = self.bot.db
        user = await _resolve_user(self.bot, guild, uid)
        member = guild.get_member(uid) if guild else None
        embed = discord.Embed(title=f'👤 Profile — {user or uid}', colour=5793266)
        if user:
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name='Account', value=f'**ID:** `{user.id}`\n**Created:** <t:{int(user.created_at.timestamp())}:R>', inline=False)
        if member:
            embed.add_field(name='Server', value=f"**Joined:** <t:{int(member.joined_at.timestamp())}:R>\n**Roles:** {', '.join((r.mention for r in member.roles[1:])) or 'None'}", inline=False)
        nicks = await db.fetch_all('SELECT new_nick, changed_at FROM nickname_history WHERE guild_id = ? AND user_id = ? ORDER BY changed_at DESC LIMIT 10', (gid, uid))
        if nicks:
            nick_lines = [f"`{r['new_nick'] or '(cleared)'}` — {r['changed_at'][:10]}" for r in nicks]
            embed.add_field(name='Nickname History', value='\n'.join(nick_lines[:5]), inline=False)
        unames = await db.fetch_all('SELECT new_name, changed_at FROM username_history WHERE user_id = ? ORDER BY changed_at DESC LIMIT 10', (uid,))
        if unames:
            uname_lines = [f"`{r['new_name']}` — {r['changed_at'][:10]}" for r in unames]
            embed.add_field(name='Username History', value='\n'.join(uname_lines[:5]), inline=False)
        inf_rows = await db.fetch_all('SELECT infraction_type, active, created_at FROM infractions WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC', (gid, uid))
        if inf_rows:
            counts: dict[str, int] = {}
            for r in inf_rows:
                counts[r['infraction_type']] = counts.get(r['infraction_type'], 0) + 1
            inf_summary = ' | '.join((f'{k}: {v}' for k, v in counts.items()))
            last_inf = inf_rows[0]['created_at'][:10] if inf_rows else '—'
            embed.add_field(name=f'Infractions ({len(inf_rows)} total)', value=f'{inf_summary}\nLast: {last_inf}', inline=False)
        msg_total = await db.fetch_val('SELECT COUNT(*) FROM messages WHERE guild_id = ? AND author_id = ?', (gid, uid))
        msg_30d_cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat()
        msg_30d = await db.fetch_val('SELECT COUNT(*) FROM messages WHERE guild_id = ? AND author_id = ? AND created_at >= ?', (gid, uid, msg_30d_cutoff))
        embed.add_field(name='Messages', value=f'**Total:** {int(msg_total or 0):,}\n**Last 30d:** {int(msg_30d or 0):,}', inline=True)
        active_ch = await db.fetch_one('\n            SELECT channel_id, COUNT(*) AS cnt FROM messages\n            WHERE guild_id = ? AND author_id = ?\n            GROUP BY channel_id ORDER BY cnt DESC LIMIT 1\n            ', (gid, uid))
        if active_ch:
            ch = self.bot.get_channel(active_ch['channel_id'])
            ch_mention = ch.mention if ch else f"`{active_ch['channel_id']}`"
            embed.add_field(name='Most Active Channel', value=f"{ch_mention} ({active_ch['cnt']:,} msgs)", inline=True)
        voice_secs = await db.fetch_val('SELECT COALESCE(SUM(duration_seconds), 0) FROM voice_sessions WHERE guild_id = ? AND user_id = ?', (gid, uid))
        voice_hours = round((voice_secs or 0) / 3600, 1)
        embed.add_field(name='Voice Time', value=f'{voice_hours}h', inline=True)
        last_presence = await db.fetch_val('SELECT MAX(recorded_at) FROM presence_events WHERE guild_id = ? AND user_id = ?', (gid, uid))
        last_msg_ts = await db.fetch_val('SELECT MAX(created_at) FROM messages WHERE guild_id = ? AND author_id = ?', (gid, uid))
        last_seen = last_presence or last_msg_ts or '—'
        if last_seen != '—':
            last_seen = last_seen[:16].replace('T', ' ') + ' UTC'
        embed.add_field(name='Last Seen', value=last_seen, inline=True)
        log.info('lookup.profile', guild=gid, target=uid, requester=interaction.user.id)
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(embed=embed, ephemeral=eph)

    @lookup_group.command(name='messages', description='Paginated message history for a user.')
    @app_commands.describe(user_id='Discord user ID (optional if keyword provided)', channel='Optional channel filter', limit='Max messages to retrieve (default 25)', before='Only show messages before this date (YYYY-MM-DD)', after='Only show messages after this date (YYYY-MM-DD)', keyword='Search message content or attachment URLs')
    async def messages(self, interaction: discord.Interaction, user_id: Optional[str]=None, channel: Optional[discord.TextChannel]=None, limit: app_commands.Range[int, 1, 200]=25, before: Optional[str]=None, after: Optional[str]=None, keyword: Optional[str]=None) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        if user_id is None and keyword is None:
            await interaction.followup.send('❌ Provide at least one of `user_id` or `keyword`.', ephemeral=True)
            return
        uid: Optional[int] = None
        if user_id is not None:
            try:
                uid = int(user_id)
            except ValueError:
                await interaction.followup.send('❌ Invalid user ID.', ephemeral=True)
                return
        gid = interaction.guild_id or 0
        db = self.bot.db
        conditions = ['guild_id = CAST(? AS INTEGER)']
        params: list = [gid]
        if uid is not None:
            conditions.append('author_id = CAST(? AS INTEGER)')
            params.append(uid)
        if keyword is not None:
            conditions.append('(content LIKE ? OR attachment_urls LIKE ?)')
            like_pattern = f'%{keyword}%'
            params.extend([like_pattern, like_pattern])
        if channel:
            conditions.append('channel_id = CAST(? AS INTEGER)')
            params.append(channel.id)
        if after:
            conditions.append('created_at >= ?')
            params.append(after)
        if before:
            conditions.append('created_at <= ?')
            params.append(before + 'T23:59:59')
        params.append(limit)
        query = f"SELECT message_id, channel_id, author_id, content, created_at, reaction_summary, has_attachment, reply_to_id, pinned, import_source FROM messages WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ?"
        rows = await db.fetch_all(query, tuple(params))
        log.debug('lookup.messages.rows', guild=gid, user=uid, keyword=keyword, count=len(rows))
        if not rows:
            if uid is not None and keyword is not None:
                msg = f'No messages found for user `{uid}` matching `{keyword}`.'
            elif uid is not None:
                msg = f'No messages found for user `{uid}`.'
            else:
                msg = f'No messages found matching `{keyword}`.'
            await interaction.followup.send(msg, ephemeral=True)
            return
        if uid is not None and keyword is not None:
            search_label = f'User {uid} | "{keyword}"'
        elif uid is not None:
            search_label = f'User {uid}'
        else:
            search_label = f'"{keyword}"'
        per_page = 10
        page_count = math.ceil(len(rows) / per_page)
        pages: list[discord.Embed] = []
        for p in range(page_count):
            chunk = rows[p * per_page:(p + 1) * per_page]
            embed = discord.Embed(title=f'💬 Messages — {search_label} (page {p + 1}/{page_count})', colour=5763719)
            for r in chunk:
                ch = self.bot.get_channel(r['channel_id'])
                ch_name = f'#{ch.name}' if ch else str(r['channel_id'])
                preview = (r['content'] or '')[:100]
                if len(r.get('content', '') or '') > 100:
                    preview += '…'
                ts = r['created_at'][:16].replace('T', ' ')
                field_name = f'`{ts}` in {ch_name}'
                if r.get('pinned'):
                    field_name = '📌 ' + field_name
                field_value = preview or '*(empty)*'
                if uid is None:
                    field_value = f"<@{r['author_id']}> — {field_value}"
                extras: list[str] = []
                if r.get('reaction_summary'):
                    extras.append(f"🔁 {r['reaction_summary']}")
                if r.get('has_attachment'):
                    extras.append('📎 (attachment)')
                if r.get('reply_to_id'):
                    extras.append(f"↩️ reply to {r['reply_to_id']}")
                if r.get('import_source') == 'csv':
                    extras.append('[hist]')
                if extras:
                    field_value += '  ' + '  '.join(extras)
                embed.add_field(name=field_name, value=field_value, inline=False)
            pages.append(embed)
        view = MessagePaginator(pages)
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=eph)

    @lookup_group.command(name='infractions', description='Full infraction history for a user.')
    @app_commands.describe(user_id='Discord user ID')
    async def infractions(self, interaction: discord.Interaction, user_id: str) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send('❌ Invalid user ID.', ephemeral=True)
            return
        gid = interaction.guild_id or 0
        db = self.bot.db
        rows = await db.fetch_all('\n            SELECT id, infraction_type, reason, moderator_id, created_at, active, resolved_at\n            FROM infractions\n            WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER)\n            ORDER BY created_at DESC\n            ', (gid, uid))
        if not rows:
            await interaction.followup.send(f'No infractions found for `{uid}`.', ephemeral=True)
            return
        lines: list[str] = []
        for r in rows:
            status = '✅ Active' if r['active'] else f"☑️ Resolved {r.get('resolved_at', '')[:10]}"
            mod = f"<@{r['moderator_id']}>"
            lines.append(f"**#{r['id']}** `{r['infraction_type'].upper()}` — {r['created_at'][:10]}\nMod: {mod} | {status}\n> {r['reason'] or 'No reason'}\n")
        pages = _paginate_lines(lines, f'⚠️ Infractions — {uid}', 16705372)
        eph = ephemeral_for(interaction.user.id)
        view = MessagePaginator(pages) if len(pages) > 1 else None
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=eph)

    @lookup_group.command(name='voice', description='Voice session history for a user.')
    @app_commands.describe(user_id='Discord user ID', days='Days to look back (default 30)')
    async def voice(self, interaction: discord.Interaction, user_id: str, days: app_commands.Range[int, 1, 365]=30) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send('❌ Invalid user ID.', ephemeral=True)
            return
        gid = interaction.guild_id or 0
        db = self.bot.db
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
        rows = await db.fetch_all('\n            SELECT channel_name, joined_at, left_at, duration_seconds\n            FROM voice_sessions\n            WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER) AND joined_at >= ?\n            ORDER BY joined_at DESC\n            ', (gid, uid, cutoff))
        if not rows:
            await interaction.followup.send(f'No voice sessions found for `{uid}` in the last {days} days.', ephemeral=True)
            return
        total_secs = sum((r['duration_seconds'] or 0 for r in rows))
        total_hours = round(total_secs / 3600, 2)
        avg_mins = round(total_secs / len(rows) / 60, 1) if rows else 0
        channel_counts: dict[str, int] = {}
        for r in rows:
            channel_counts[r['channel_name']] = channel_counts.get(r['channel_name'], 0) + 1
        top_channel = max(channel_counts, key=channel_counts.__getitem__) if channel_counts else '—'
        summary = f'**Sessions:** {len(rows)} | **Total:** {total_hours}h | **Avg:** {avg_mins}m | **Top Channel:** {top_channel}'
        lines: list[str] = [summary, '']
        for r in rows[:50]:
            dur = f"{round((r['duration_seconds'] or 0) / 60, 1)}m"
            joined = r['joined_at'][:16].replace('T', ' ')
            lines.append(f"`{joined}` **{r['channel_name']}** — {dur}")
        pages = _paginate_lines(lines, f'🎙️ Voice — {uid} (last {days}d)', 15418782)
        eph = ephemeral_for(interaction.user.id)
        view = MessagePaginator(pages) if len(pages) > 1 else None
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=eph)

    @lookup_group.command(name='presence', description='Presence / status history for a user.')
    @app_commands.describe(user_id='Discord user ID', days='Days to look back (default 7)')
    async def presence(self, interaction: discord.Interaction, user_id: str, days: app_commands.Range[int, 1, 90]=7) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.followup.send('❌ Invalid user ID.', ephemeral=True)
            return
        gid = interaction.guild_id or 0
        db = self.bot.db
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
        rows = await db.fetch_all('\n            SELECT status, activity_type, activity_name, recorded_at\n            FROM presence_events\n            WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER) AND recorded_at >= ?\n            ORDER BY recorded_at DESC\n            LIMIT 200\n            ', (gid, uid, cutoff))
        if not rows:
            await interaction.followup.send(f'No presence data for `{uid}` in the last {days} days.\n*(Presence tracking must be enabled and the user must have been seen online.)*', ephemeral=True)
            return
        online_hours: dict[int, int] = {}
        for r in rows:
            if r['status'] in ('online', 'dnd', 'idle'):
                try:
                    hour = int(r['recorded_at'][11:13])
                    online_hours[hour] = online_hours.get(hour, 0) + 1
                except (ValueError, IndexError):
                    pass
        if online_hours:
            sorted_hours = sorted(online_hours, key=online_hours.__getitem__, reverse=True)
            top3 = sorted_hours[:3]
            active_window = ', '.join((f'{h:02d}:00 UTC' for h in sorted(top3)))
            summary = f'Usually active around **{active_window}**'
        else:
            summary = 'Not enough data for activity window.'
        lines = [summary, '']
        for r in rows[:60]:
            ts = r['recorded_at'][:16].replace('T', ' ')
            act = f" — {r['activity_type']}: {r['activity_name']}" if r['activity_name'] else ''
            lines.append(f"`{ts}` **{r['status']}**{act}")
        pages = _paginate_lines(lines, f'🟢 Presence — {uid} (last {days}d)', 5763719)
        eph = ephemeral_for(interaction.user.id)
        view = MessagePaginator(pages) if len(pages) > 1 else None
        await interaction.followup.send(embed=pages[0], view=view, ephemeral=eph)

    @lookup_group.command(name='mutual', description='Cross-reference two users (channels, voice overlap, infractions).')
    @app_commands.describe(user_id_a='First user ID', user_id_b='Second user ID')
    async def mutual(self, interaction: discord.Interaction, user_id_a: str, user_id_b: str) -> None:
        if not await _check_lookup(interaction):
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        try:
            uid_a = int(user_id_a)
            uid_b = int(user_id_b)
        except ValueError:
            await interaction.followup.send('❌ Invalid user ID(s).', ephemeral=True)
            return
        gid = interaction.guild_id or 0
        db = self.bot.db
        embed = discord.Embed(title=f'🔗 Mutual — {uid_a} × {uid_b}', colour=15548997)
        ch_a = await db.fetch_all('SELECT DISTINCT channel_id FROM messages WHERE guild_id = CAST(? AS INTEGER) AND author_id = CAST(? AS INTEGER)', (gid, uid_a))
        ch_b = await db.fetch_all('SELECT DISTINCT channel_id FROM messages WHERE guild_id = CAST(? AS INTEGER) AND author_id = CAST(? AS INTEGER)', (gid, uid_b))
        set_a = {r['channel_id'] for r in ch_a}
        set_b = {r['channel_id'] for r in ch_b}
        shared_ch = set_a & set_b
        if shared_ch:
            ch_mentions = []
            for cid in list(shared_ch)[:10]:
                ch = self.bot.get_channel(cid)
                ch_mentions.append(ch.mention if ch else f'`{cid}`')
            embed.add_field(name=f'📢 Shared Channels ({len(shared_ch)})', value=', '.join(ch_mentions), inline=False)
        else:
            embed.add_field(name='📢 Shared Channels', value='None', inline=False)
        sessions_a = await db.fetch_all('\n            SELECT channel_id, joined_at, left_at FROM voice_sessions\n            WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER) AND left_at IS NOT NULL\n            ', (gid, uid_a))
        sessions_b = await db.fetch_all('\n            SELECT channel_id, joined_at, left_at FROM voice_sessions\n            WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER) AND left_at IS NOT NULL\n            ', (gid, uid_b))
        overlap_count = 0
        for sa in sessions_a:
            for sb in sessions_b:
                if sa['channel_id'] != sb['channel_id']:
                    continue
                start = max(sa['joined_at'], sb['joined_at'])
                end = min(sa['left_at'], sb['left_at'])
                if start < end:
                    overlap_count += 1
        embed.add_field(name='🎙️ Voice Overlaps', value=str(overlap_count), inline=True)
        infs_a = await db.fetch_all('SELECT created_at FROM infractions WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER)', (gid, uid_a))
        infs_b = await db.fetch_all('SELECT created_at FROM infractions WHERE guild_id = CAST(? AS INTEGER) AND user_id = CAST(? AS INTEGER)', (gid, uid_b))
        incident_pairs = 0
        for ia in infs_a:
            for ib in infs_b:
                try:
                    dt_a = datetime.datetime.fromisoformat(ia['created_at'])
                    dt_b = datetime.datetime.fromisoformat(ib['created_at'])
                    if abs((dt_a - dt_b).total_seconds()) <= 86400:
                        incident_pairs += 1
                except ValueError:
                    pass
        embed.add_field(name='⚠️ Shared Incident Windows', value=str(incident_pairs), inline=True)
        log.info('lookup.mutual', guild=gid, a=uid_a, b=uid_b, requester=interaction.user.id)
        eph = ephemeral_for(interaction.user.id)
        await interaction.followup.send(embed=embed, ephemeral=eph)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(LookupCommands(_a))