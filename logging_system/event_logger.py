from __future__ import annotations
import asyncio
import datetime
import json
from typing import TYPE_CHECKING, Optional
import discord
import aiohttp
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

class EventLogger(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._voice_sessions: dict[tuple[int, int], dict] = {}

    async def _send_embed(self, guild_id: int, channel_attr: str, embed: discord.Embed) -> None:
        cfg = self.bot.config.get_guild_discord(guild_id)
        resolve_guild_id = cfg.log_to_guild_id if cfg.log_to_guild_id else guild_id
        cfg = self.bot.config.get_guild_discord(resolve_guild_id)
        ch_id: int | None = getattr(cfg, channel_attr, None)
        if not ch_id:
            return
        channel = self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ch_id)
            except discord.NotFound:
                log.warning('event_log.channel_not_found', channel_id=ch_id, attr=channel_attr, guild=guild_id)
                return
            except discord.Forbidden:
                log.warning('event_log.channel_forbidden', channel_id=ch_id, attr=channel_attr, guild=guild_id)
                return
            except discord.HTTPException as exc:
                log.warning('event_log.channel_fetch_failed', channel_id=ch_id, attr=channel_attr, guild=guild_id, error=str(exc))
                return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning('event_log.send_forbidden', channel_id=ch_id, attr=channel_attr, guild=guild_id)
        except discord.HTTPException as exc:
            log.warning('event_log.send_failed', channel_id=ch_id, attr=channel_attr, guild=guild_id, error=str(exc))

    async def _log_to_db(self, table: str, **kwargs) -> None:
        cols = ', '.join(kwargs.keys())
        placeholders = ', '.join(('?' for _ in kwargs))
        try:
            await self.bot.db.execute(f'INSERT INTO {table} ({cols}) VALUES ({placeholders})', tuple(kwargs.values()))
        except Exception as exc:
            log.warning('event_log.db_insert_failed', table=table, error=str(exc))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        attachment_urls = json.dumps([a.url for a in message.attachments])
        await self._log_to_db('messages', message_id=message.id, guild_id=message.guild.id, channel_id=message.channel.id, author_id=message.author.id, content=message.content or '', has_attachment=int(bool(message.attachments)), attachment_urls=attachment_urls, embed_count=len(message.embeds), created_at=message.created_at.isoformat())

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not after.guild or after.author.bot:
            return
        cfg = self.bot.config.get_guild_logging(after.guild.id)
        if not cfg.log_message_edits:
            return
        if before.content == after.content:
            return
        await self._log_to_db('message_edits', message_id=before.id, guild_id=after.guild.id, channel_id=after.channel.id, author_id=after.author.id, before=before.content or '', after=after.content or '')
        embed = discord.Embed(title='✏️ Message Edited', colour=16753920, url=after.jump_url)
        embed.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        embed.add_field(name='Before', value=(before.content or '*empty*')[:1020], inline=False)
        embed.add_field(name='After', value=(after.content or '*empty*')[:1020], inline=False)
        embed.add_field(name='Channel', value=after.channel.mention)
        embed.set_footer(text=f'User ID: {after.author.id} | Message ID: {after.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(after.guild.id, 'log_channel_id', embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        cfg = self.bot.config.get_guild_logging(message.guild.id)
        if not cfg.log_message_deletes:
            return
        try:
            await self.bot.db.execute("UPDATE messages\n                   SET is_deleted = 1, deleted_at = datetime('now')\n                   WHERE message_id = ?", (message.id,))
        except Exception as exc:
            log.warning('event_log.db_insert_failed', table='messages', error=str(exc))
        embed = discord.Embed(title='🗑️ Message Deleted', colour=16711680)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name='Content', value=(message.content or '*empty*')[:1020], inline=False)
        embed.add_field(name='Channel', value=message.channel.mention)
        embed.set_footer(text=f'Message ID: {message.id}')
        embed.timestamp = discord.utils.utcnow()
        if message.attachments and cfg.reupload_deleted_attachments:
            asyncio.create_task(self._archive_attachments(message))
        await self._send_embed(message.guild.id, 'log_channel_id', embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages:
            return
        guild = messages[0].guild
        if not guild:
            return
        cfg = self.bot.config.get_guild_logging(guild.id)
        if not cfg.log_bulk_deletes:
            return
        embed = discord.Embed(title='🗑️🗑️ Bulk Delete', colour=16711680, description=f'{len(messages)} messages deleted in {messages[0].channel.mention}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(guild.id, 'log_channel_id', embed)

    async def _archive_attachments(self, message: discord.Message) -> None:
        if not message.guild:
            return
        cfg_discord = self.bot.config.get_guild_discord(message.guild.id)
        cfg_log = self.bot.config.get_guild_logging(message.guild.id)
        if not cfg_discord.archive_channel_id:
            return
        channel = self.bot.get_channel(cfg_discord.archive_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(cfg_discord.archive_channel_id)
            except discord.HTTPException:
                return
        max_bytes = cfg_log.max_attachment_size_mb * 1024 * 1024
        async with aiohttp.ClientSession() as session:
            for att in message.attachments:
                if att.size > max_bytes:
                    continue
                try:
                    async with session.get(att.url) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                    import io
                    file = discord.File(io.BytesIO(data), filename=att.filename)
                    embed = discord.Embed(title='📦 Archived Attachment', description=f'From {message.author.mention} in {message.channel.mention}', colour=8421504)
                    await channel.send(embed=embed, file=file)
                except Exception as exc:
                    log.warning('archive.upload_failed', error=str(exc))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self.bot.config.get_guild_logging(member.guild.id)
        if not cfg.log_member_joins:
            return
        await self._log_to_db('member_events', guild_id=member.guild.id, user_id=member.id, username=str(member), event_type='join', account_created=member.created_at.isoformat(), member_count=member.guild.member_count)
        embed = discord.Embed(title='📥 Member Joined', colour=65280, description=f'{member.mention} (`{member.id}`)')
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name='Account Created', value=discord.utils.format_dt(member.created_at, 'R'))
        embed.add_field(name='Member Count', value=str(member.guild.member_count))
        embed.set_footer(text=f'User ID: {member.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(member.guild.id, 'log_channel_id', embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = self.bot.config.get_guild_logging(member.guild.id)
        if not cfg.log_member_leaves:
            return
        role_ids = json.dumps([r.id for r in member.roles if r != member.guild.default_role])
        await self._log_to_db('member_events', guild_id=member.guild.id, user_id=member.id, username=str(member), event_type='leave', roles_at_leave=role_ids)
        embed = discord.Embed(title='📤 Member Left', colour=16737792, description=f'**{member}** (`{member.id}`)')
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.joined_at:
            embed.add_field(name='Joined', value=discord.utils.format_dt(member.joined_at, 'R'))
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        if roles:
            embed.add_field(name='Roles', value=' '.join(roles)[:1020], inline=False)
        embed.set_footer(text=f'User ID: {member.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(member.guild.id, 'log_channel_id', embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        cfg = self.bot.config.get_guild_logging(after.guild.id)
        if cfg.log_nickname_changes and before.nick != after.nick:
            await self._log_to_db('nickname_history', guild_id=after.guild.id, user_id=after.id, old_nick=before.nick, new_nick=after.nick)
            embed = discord.Embed(title='📝 Nickname Changed', colour=43775)
            embed.set_author(name=str(after), icon_url=after.display_avatar.url)
            embed.add_field(name='Before', value=before.nick or '*none*')
            embed.add_field(name='After', value=after.nick or '*cleared*')
            embed.set_footer(text=f'User ID: {after.id}')
            embed.timestamp = discord.utils.utcnow()
            await self._send_embed(after.guild.id, 'log_channel_id', embed)
        if cfg.log_role_changes:
            added_roles = [r for r in after.roles if r not in before.roles]
            removed_roles = [r for r in before.roles if r not in after.roles]
            for role in added_roles:
                await self._log_to_db('role_history', guild_id=after.guild.id, user_id=after.id, role_id=role.id, role_name=role.name, action='add')
            for role in removed_roles:
                await self._log_to_db('role_history', guild_id=after.guild.id, user_id=after.id, role_id=role.id, role_name=role.name, action='remove')
            if added_roles or removed_roles:
                embed = discord.Embed(title='🎭 Roles Updated', colour=11141375)
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                if added_roles:
                    embed.add_field(name='Added', value=' '.join((r.mention for r in added_roles)))
                if removed_roles:
                    embed.add_field(name='Removed', value=' '.join((r.mention for r in removed_roles)))
                embed.set_footer(text=f'User ID: {after.id}')
                embed.timestamp = discord.utils.utcnow()
                await self._send_embed(after.guild.id, 'log_channel_id', embed)
        if cfg.log_boost_events:
            was_boosting = before.premium_since is not None
            now_boosting = after.premium_since is not None
            if not was_boosting and now_boosting:
                embed = discord.Embed(title='🚀 Server Boost Started', colour=16741370)
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.set_footer(text=f'User ID: {after.id}')
                embed.timestamp = discord.utils.utcnow()
                await self._send_embed(after.guild.id, 'log_channel_id', embed)
            elif was_boosting and (not now_boosting):
                embed = discord.Embed(title='💔 Server Boost Ended', colour=11141290)
                embed.set_author(name=str(after), icon_url=after.display_avatar.url)
                embed.set_footer(text=f'User ID: {after.id}')
                embed.timestamp = discord.utils.utcnow()
                await self._send_embed(after.guild.id, 'log_channel_id', embed)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        if before.name != after.name:
            await self._log_to_db('username_history', user_id=after.id, old_name=before.name, new_name=after.name)
        if before.avatar != after.avatar and after.avatar:
            await self._log_to_db('avatar_history', user_id=after.id, avatar_hash=after.avatar.key, avatar_url=after.display_avatar.url)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        cfg = self.bot.config.get_guild_logging(member.guild.id)
        if not cfg.log_voice_state:
            return
        guild_id = member.guild.id
        user_id = member.id
        now_iso = datetime.datetime.utcnow().isoformat()
        key = (guild_id, user_id)
        if before.channel != after.channel:
            if before.channel and (not after.channel):
                session = self._voice_sessions.pop(key, None)
                duration = 0
                if session:
                    duration = int((datetime.datetime.utcnow() - session['joined_at']).total_seconds())
                    await self._log_to_db('voice_sessions', guild_id=guild_id, user_id=user_id, channel_id=before.channel.id, channel_name=before.channel.name, joined_at=session['joined_at'].isoformat(), left_at=now_iso, duration_seconds=duration)
                await self._voice_event(guild_id, user_id, before.channel, 'leave', duration_seconds=duration, occurred_at=now_iso)
                await self._send_voice_embed(guild_id, 'leave', member, before.channel, duration=duration)
            elif after.channel and (not before.channel):
                self._voice_sessions[key] = {'channel_id': after.channel.id, 'joined_at': datetime.datetime.utcnow()}
                await self._voice_event(guild_id, user_id, after.channel, 'join', occurred_at=now_iso)
                await self._send_voice_embed(guild_id, 'join', member, after.channel)
            elif before.channel and after.channel:
                session = self._voice_sessions.get(key)
                if session:
                    session['channel_id'] = after.channel.id
                extra = json.dumps({'from': before.channel.id, 'to': after.channel.id})
                await self._voice_event(guild_id, user_id, after.channel, 'move', extra=extra, occurred_at=now_iso)
                await self._send_voice_embed(guild_id, 'move', member, after.channel, from_channel=before.channel)
        if cfg.log_voice_mute_deafen:
            if before.mute != after.mute:
                evt = 'mute' if after.mute else 'unmute'
                await self._voice_event(guild_id, user_id, after.channel, evt, occurred_at=now_iso)
                await self._send_voice_embed(guild_id, evt, member, after.channel)
            if before.deaf != after.deaf:
                evt = 'deafen' if after.deaf else 'undeafen'
                await self._voice_event(guild_id, user_id, after.channel, evt, occurred_at=now_iso)
            if before.self_mute != after.self_mute:
                await self._voice_event(guild_id, user_id, after.channel, 'self_mute', occurred_at=now_iso)
            if before.self_deaf != after.self_deaf:
                await self._voice_event(guild_id, user_id, after.channel, 'self_deafen', occurred_at=now_iso)
        if before.self_stream != after.self_stream:
            evt = 'stream_start' if after.self_stream else 'stream_end'
            await self._voice_event(guild_id, user_id, after.channel, evt, occurred_at=now_iso)
        if before.self_video != after.self_video:
            evt = 'video_start' if after.self_video else 'video_end'
            await self._voice_event(guild_id, user_id, after.channel, evt, occurred_at=now_iso)

    async def _voice_event(self, guild_id: int, user_id: int, channel: discord.VoiceChannel | None, event_type: str, extra: str | None=None, duration_seconds: int | None=None, occurred_at: str | None=None) -> None:
        await self._log_to_db('voice_events', guild_id=guild_id, user_id=user_id, channel_id=channel.id if channel else None, channel_name=channel.name if channel else None, event_type=event_type, extra=extra, duration_seconds=duration_seconds, occurred_at=occurred_at or datetime.datetime.utcnow().isoformat())

    async def _send_voice_embed(self, guild_id: int, event_type: str, member: discord.Member, channel: discord.VoiceChannel | None, duration: int=0, from_channel: discord.VoiceChannel | None=None) -> None:
        ICONS = {'join': ('🔊 Joined Voice', 65280), 'leave': ('🔇 Left Voice', 16737792), 'move': ('🔀 Moved Voice', 43775), 'mute': ('🔴 Server Muted', 16711680), 'unmute': ('🟢 Server Unmuted', 65280), 'deafen': ('🔴 Server Deafened', 16711680), 'undeafen': ('🟢 Server Undeafened', 65280)}
        title, colour = ICONS.get(event_type, (f'🎙️ {event_type.title()}', 8421504))
        embed = discord.Embed(title=title, colour=colour)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        if channel:
            embed.add_field(name='Channel', value=f'**{channel.name}**')
        if from_channel:
            embed.add_field(name='From', value=from_channel.name)
        if duration:
            m, s = divmod(duration, 60)
            h, m = divmod(m, 60)
            embed.add_field(name='Duration', value=f'{h:02d}:{m:02d}:{s:02d}')
        embed.set_footer(text=f'User ID: {member.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(guild_id, 'voice_log_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        cfg = self.bot.config.get_guild_logging(channel.guild.id)
        if not cfg.log_channel_changes:
            return
        await self._guild_event(channel.guild.id, 'channel_create', channel.id, channel.name)
        embed = discord.Embed(title='📢 Channel Created', colour=65280)
        embed.add_field(name='Name', value=channel.mention if isinstance(channel, discord.TextChannel) else channel.name)
        embed.add_field(name='Type', value=str(channel.type))
        embed.set_footer(text=f'Channel ID: {channel.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(channel.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        cfg = self.bot.config.get_guild_logging(channel.guild.id)
        if not cfg.log_channel_changes:
            return
        await self._guild_event(channel.guild.id, 'channel_delete', channel.id, channel.name)
        embed = discord.Embed(title='🗑️ Channel Deleted', colour=16711680)
        embed.add_field(name='Name', value=channel.name)
        embed.add_field(name='Type', value=str(channel.type))
        embed.set_footer(text=f'Channel ID: {channel.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(channel.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        cfg = self.bot.config.get_guild_logging(after.guild.id)
        if not cfg.log_channel_changes:
            return
        changes: dict[str, tuple] = {}
        if before.name != after.name:
            changes['name'] = (before.name, after.name)
        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            if before.topic != after.topic:
                changes['topic'] = (before.topic or '', after.topic or '')
            if before.slowmode_delay != after.slowmode_delay:
                changes['slowmode'] = (f'{before.slowmode_delay}s', f'{after.slowmode_delay}s')
        if not changes:
            return
        await self._guild_event(after.guild.id, 'channel_edit', after.id, after.name, extra=json.dumps({k: {'before': v[0], 'after': v[1]} for k, v in changes.items()}))
        embed = discord.Embed(title='✏️ Channel Updated', colour=16753920)
        embed.add_field(name='Channel', value=after.mention if isinstance(after, discord.TextChannel) else after.name)
        for key, (old, new) in changes.items():
            embed.add_field(name=key.capitalize(), value=f'{old} → {new}', inline=False)
        embed.set_footer(text=f'Channel ID: {after.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(after.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        cfg = self.bot.config.get_guild_logging(role.guild.id)
        if not cfg.log_role_structure_changes:
            return
        await self._guild_event(role.guild.id, 'role_create', role.id, role.name)
        embed = discord.Embed(title='🎭 Role Created', colour=role.colour.value or 8421504)
        embed.add_field(name='Name', value=role.mention)
        embed.set_footer(text=f'Role ID: {role.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(role.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        cfg = self.bot.config.get_guild_logging(role.guild.id)
        if not cfg.log_role_structure_changes:
            return
        await self._guild_event(role.guild.id, 'role_delete', role.id, role.name)
        embed = discord.Embed(title='🗑️ Role Deleted', colour=16711680)
        embed.add_field(name='Name', value=role.name)
        embed.add_field(name='Colour', value=str(role.colour))
        embed.set_footer(text=f'Role ID: {role.id}')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(role.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before: list[discord.Emoji], after: list[discord.Emoji]) -> None:
        cfg = self.bot.config.get_guild_logging(guild.id)
        if not cfg.log_emoji_changes:
            return
        before_ids = {e.id for e in before}
        after_ids = {e.id for e in after}
        added = [e for e in after if e.id not in before_ids]
        removed = [e for e in before if e.id not in after_ids]
        for e in added:
            await self._guild_event(guild.id, 'emoji_add', e.id, e.name)
        for e in removed:
            await self._guild_event(guild.id, 'emoji_remove', e.id, e.name)
        if added or removed:
            embed = discord.Embed(title='😀 Emojis Updated', colour=16766720)
            if added:
                embed.add_field(name='Added', value=' '.join((str(e) for e in added))[:1020])
            if removed:
                embed.add_field(name='Removed', value=' '.join((e.name for e in removed))[:1020])
            embed.timestamp = discord.utils.utcnow()
            await self._send_embed(guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if not invite.guild:
            return
        cfg = self.bot.config.get_guild_logging(invite.guild.id)
        if not cfg.log_invite_changes:
            return
        extra = json.dumps({'code': invite.code, 'max_uses': invite.max_uses, 'expires_at': invite.expires_at.isoformat() if invite.expires_at else None, 'creator': invite.inviter.id if invite.inviter else None})
        await self._guild_event(invite.guild.id, 'invite_create', entity_name=invite.code, extra=extra)
        embed = discord.Embed(title='🔗 Invite Created', colour=65280)
        embed.add_field(name='Code', value=f'`{invite.code}`')
        embed.add_field(name='Created By', value=str(invite.inviter) if invite.inviter else 'unknown')
        embed.add_field(name='Max Uses', value=str(invite.max_uses or '∞'))
        if invite.expires_at:
            embed.add_field(name='Expires', value=discord.utils.format_dt(invite.expires_at, 'R'))
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(invite.guild.id, 'audit_channel_id', embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if not invite.guild:
            return
        cfg = self.bot.config.get_guild_logging(invite.guild.id)
        if not cfg.log_invite_changes:
            return
        await self._guild_event(invite.guild.id, 'invite_delete', entity_name=invite.code)
        embed = discord.Embed(title='🔗 Invite Deleted', colour=16711680)
        embed.add_field(name='Code', value=f'`{invite.code}`')
        embed.timestamp = discord.utils.utcnow()
        await self._send_embed(invite.guild.id, 'audit_channel_id', embed)

    async def _guild_event(self, guild_id: int, event_type: str, entity_id: int | None=None, entity_name: str | None=None, extra: str | None=None) -> None:
        await self._log_to_db('guild_events', guild_id=guild_id, event_type=event_type, entity_id=entity_id, entity_name=entity_name, extra=extra)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    key = (guild.id, member.id)
                    if key not in self._voice_sessions:
                        self._voice_sessions[key] = {'channel_id': vc.id, 'joined_at': datetime.datetime.utcnow()}
        log.info('voice.sessions.reconciled', count=len(self._voice_sessions))

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(EventLogger(_a))