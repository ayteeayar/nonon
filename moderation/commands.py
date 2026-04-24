from __future__ import annotations
import asyncio
import datetime
import os
import platform
from typing import TYPE_CHECKING, Optional
import discord
import psutil
from discord import app_commands
from discord.ext import commands
import structlog
from core.permissions import owner_only, require_permission, SCOPE_COMMANDS
from core.vanish import ephemeral_for
from moderation.infractions import InfractionManager
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

async def _check_perm(_c: discord.Interaction, _b: str) -> bool:
    _a: KnowledgeBot = _c.client
    if not await _a.resolver.can_use(_c.user, _b, _c.guild_id or 0):
        await _c.response.send_message('❌ Permission denied.', ephemeral=True)
        return False
    return True

async def _mod_log(_a: 'KnowledgeBot', _e: int, _d: discord.Embed) -> None:
    _b = _a.config.get_guild_discord(_e)
    if _b.mod_log_channel_id:
        _c = _a.get_channel(_b.mod_log_channel_id)
        if _c:
            try:
                await _c.send(embed=_d)
            except discord.HTTPException:
                pass

def _action_embed(_a: str, _f: discord.Member | discord.User, _d: discord.Member | discord.User, _e: str, _b: int=16729344) -> discord.Embed:
    _c = discord.Embed(title=f'🔨 {_a}', colour=_b)
    _c.add_field(name='User', value=f'{_f.mention} (`{_f.id}`)')
    _c.add_field(name='Moderator', value=f'{_d.mention}')
    _c.add_field(name='Reason', value=_e or 'No reason provided', inline=False)
    _c.timestamp = discord.utils.utcnow()
    return _c

class ModerationCommands(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._infractions = InfractionManager(bot)

    @app_commands.command(name='ban', description='Ban a member.')
    @app_commands.describe(member='The member to ban', reason='Reason for the ban', delete_days='Days of messages to delete (0-7)')
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str='No reason provided', delete_days: app_commands.Range[int, 0, 7]=0) -> None:
        if not await _check_perm(interaction, 'ban'):
            return
        try:
            await member.ban(reason=reason, delete_message_days=delete_days)
            await self._infractions.add(interaction.guild_id, member.id, interaction.user.id, 'ban', reason)
            embed = _action_embed('Ban', member, interaction.user, reason, 16711680)
            await interaction.response.send_message(embed=embed)
            await _mod_log(self.bot, interaction.guild_id, embed)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='kick', description='Kick a member.')
    @app_commands.describe(member='The member to kick', reason='Reason for the kick')
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str='No reason provided') -> None:
        if not await _check_perm(interaction, 'kick'):
            return
        try:
            await member.kick(reason=reason)
            await self._infractions.add(interaction.guild_id, member.id, interaction.user.id, 'kick', reason)
            embed = _action_embed('Kick', member, interaction.user, reason, 16737792)
            await interaction.response.send_message(embed=embed)
            await _mod_log(self.bot, interaction.guild_id, embed)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='mute', description='Timeout a member.')
    @app_commands.describe(member='The member to mute', duration='Duration in minutes', reason='Reason')
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: app_commands.Range[int, 1, 40320]=60, reason: str='No reason provided') -> None:
        if not await _check_perm(interaction, 'mute'):
            return
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=duration)
            await member.timeout(until, reason=reason)
            await self._infractions.add(interaction.guild_id, member.id, interaction.user.id, 'mute', reason, duration_seconds=duration * 60)
            embed = _action_embed('Mute', member, interaction.user, reason, 16776960)
            embed.add_field(name='Duration', value=f'{duration} minutes')
            await interaction.response.send_message(embed=embed)
            await _mod_log(self.bot, interaction.guild_id, embed)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='warn', description='Issue a formal warning.')
    @app_commands.describe(member='The member to warn', reason='Reason for the warning')
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        if not await _check_perm(interaction, 'warn'):
            return
        inf_id = await self._infractions.add(interaction.guild_id, member.id, interaction.user.id, 'warn', reason)
        count = await self._infractions.count_active_warnings(interaction.guild_id, member.id)
        embed = _action_embed('Warning', member, interaction.user, reason, 16753920)
        embed.add_field(name='Warning #', value=str(inf_id))
        embed.add_field(name='Total Warnings', value=str(count))
        await interaction.response.send_message(embed=embed)
        await _mod_log(self.bot, interaction.guild_id, embed)
        try:
            await member.send(f'⚠️ You have received a warning in **{interaction.guild}**: {reason}')
        except discord.HTTPException:
            pass

    @app_commands.command(name='softban', description='Ban and immediately unban to clear recent messages.')
    @app_commands.describe(member='The member to softban', reason='Reason')
    async def softban(self, interaction: discord.Interaction, member: discord.Member, reason: str='No reason provided') -> None:
        if not await _check_perm(interaction, 'softban'):
            return
        try:
            await member.ban(reason=f'[Softban] {reason}', delete_message_days=7)
            await member.unban(reason='Softban — immediate unban')
            await self._infractions.add(interaction.guild_id, member.id, interaction.user.id, 'softban', reason)
            embed = _action_embed('Softban', member, interaction.user, reason, 16724736)
            await interaction.response.send_message(embed=embed)
            await _mod_log(self.bot, interaction.guild_id, embed)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='lock', description='Lock a channel (prevent @everyone from sending).')
    @app_commands.describe(channel='Channel to lock (defaults to current)', reason='Reason')
    async def lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]=None, reason: str='No reason provided') -> None:
        if not await _check_perm(interaction, 'lock'):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message('❌ Invalid channel.', ephemeral=True)
            return
        overwrite = target.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await interaction.response.send_message(f'🔒 {target.mention} locked. Reason: {reason}')

    @app_commands.command(name='unlock', description='Unlock a channel.')
    @app_commands.describe(channel='Channel to unlock (defaults to current)')
    async def unlock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]=None) -> None:
        if not await _check_perm(interaction, 'unlock'):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message('❌ Invalid channel.', ephemeral=True)
            return
        overwrite = target.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f'🔓 {target.mention} unlocked.')

    @app_commands.command(name='purge', description='Bulk delete messages (up to 500).')
    @app_commands.describe(amount='Number of messages to delete (1-500)', user='Only delete messages from this user')
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500], user: Optional[discord.Member]=None) -> None:
        if not await _check_perm(interaction, 'purge'):
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send('❌ Invalid channel.', ephemeral=True)
            return
        check = (lambda m: m.author == user) if user else None
        deleted = await channel.purge(limit=amount, check=check, bulk=True)
        await interaction.followup.send(f'🗑️ Deleted {len(deleted)} messages.', ephemeral=True)

    @app_commands.command(name='slowmode', description='Set channel slowmode delay.')
    @app_commands.describe(seconds='Slowmode in seconds (0 = disable)', channel='Target channel')
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600], channel: Optional[discord.TextChannel]=None) -> None:
        if not await _check_perm(interaction, 'slowmode'):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message('❌ Invalid channel.', ephemeral=True)
            return
        await target.edit(slowmode_delay=seconds)
        label = f'{seconds}s' if seconds else 'disabled'
        await interaction.response.send_message(f'⏱️ Slowmode for {target.mention} set to {label}.')

    @app_commands.command(name='nickname', description="Change or clear a member's nickname.")
    @app_commands.describe(member='Target member', nick='New nickname (leave empty to clear)')
    async def nickname(self, interaction: discord.Interaction, member: discord.Member, nick: Optional[str]=None) -> None:
        if not await _check_perm(interaction, 'nickname'):
            return
        try:
            await member.edit(nick=nick)
            action = f'set to `{nick}`' if nick else 'cleared'
            await interaction.response.send_message(f'✅ Nickname {action} for {member.mention}.')
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)
    role_group = app_commands.Group(name='role', description='Role management commands')

    @role_group.command(name='add', description='Assign a role to a member.')
    @app_commands.describe(member='Target member', role='Role to assign')
    async def role_add(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
        if not await _check_perm(interaction, 'role'):
            return
        try:
            await member.add_roles(role, reason=f'By {interaction.user}')
            await interaction.response.send_message(f'✅ Added {role.mention} to {member.mention}.')
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @role_group.command(name='remove', description='Remove a role from a member.')
    @app_commands.describe(member='Target member', role='Role to remove')
    async def role_remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
        if not await _check_perm(interaction, 'role'):
            return
        try:
            await member.remove_roles(role, reason=f'By {interaction.user}')
            await interaction.response.send_message(f'✅ Removed {role.mention} from {member.mention}.')
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)
    infraction_group = app_commands.Group(name='infractions', description='Infraction management')

    @infraction_group.command(name='list', description='List infractions for a user.')
    @app_commands.describe(user='Target user')
    async def infractions_list(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await _check_perm(interaction, 'infractions'):
            return
        records = await self._infractions.get_user_infractions(interaction.guild_id, user.id)
        embed = self._infractions.build_embed(records, user)
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=eph)

    @infraction_group.command(name='remove', description='Remove an infraction by ID.')
    @app_commands.describe(infraction_id='The infraction ID to remove')
    async def infractions_remove(self, interaction: discord.Interaction, infraction_id: int) -> None:
        if not await _check_perm(interaction, 'infractions'):
            return
        ok = await self._infractions.remove(infraction_id, interaction.user.id)
        eph = ephemeral_for(interaction.user.id)
        if ok:
            await interaction.response.send_message(f'✅ Infraction #{infraction_id} removed.', ephemeral=eph)
        else:
            await interaction.response.send_message(f'❌ Infraction #{infraction_id} not found or already resolved.', ephemeral=eph)

    @app_commands.command(name='massban', description='[Owner] Ban up to 50 users by space-separated IDs.')
    @app_commands.describe(user_ids='Space-separated user IDs', reason='Ban reason')
    async def massban(self, interaction: discord.Interaction, user_ids: str, reason: str='Mass ban') -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        ids = [int(x) for x in user_ids.split() if x.isdigit()][:50]
        banned, failed = (0, 0)
        for uid in ids:
            try:
                await interaction.guild.ban(discord.Object(id=uid), reason=reason)
                banned += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                failed += 1
        await interaction.followup.send(f'✅ Banned {banned} users. {failed} failed.', ephemeral=eph)

class OwnerCommands(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
    permit_group = app_commands.Group(name='permit', description='[Owner] Grant command access.')
    revoke_group = app_commands.Group(name='revoke', description='[Owner] Revoke command access.')
    grants_group = app_commands.Group(name='grants', description='[Owner] View active grants.')

    @permit_group.command(name='user', description='[Owner] Grant a user access to a command or scope.')
    @app_commands.describe(user_id='Target user ID', command='Command name', scope='Scope name', guild_id='Guild ID (default: current)')
    async def permit_user(self, interaction: discord.Interaction, user_id: str, command: Optional[str]=None, scope: Optional[str]=None, guild_id: Optional[str]=None) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        if not command and (not scope):
            await interaction.response.send_message('❌ Provide --command or --scope.', ephemeral=True)
            return
        g_id = int(guild_id) if guild_id else interaction.guild_id or 0
        grant_type = 'command' if command else 'scope'
        grant_value = command or scope or ''
        await self.bot.resolver.add_grant(g_id, 'user', int(user_id), grant_type, grant_value, interaction.user.id)
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(f'✅ Granted {grant_type} `{grant_value}` to <@{user_id}> in guild `{g_id}`.', ephemeral=eph)

    @permit_group.command(name='role', description='[Owner] Grant a role a scope.')
    @app_commands.describe(role_id='Target role ID', scope='Scope name', guild_id='Guild ID')
    async def permit_role(self, interaction: discord.Interaction, role_id: str, scope: str, guild_id: Optional[str]=None) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        g_id = int(guild_id) if guild_id else interaction.guild_id or 0
        await self.bot.resolver.add_grant(g_id, 'role', int(role_id), 'scope', scope, interaction.user.id)
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(f'✅ Granted scope `{scope}` to role `{role_id}` in guild `{g_id}`.', ephemeral=eph)

    @revoke_group.command(name='user', description="[Owner] Revoke a user's grant.")
    @app_commands.describe(user_id='Target user ID', command='Command name', scope='Scope name', guild_id='Guild ID')
    async def revoke_user(self, interaction: discord.Interaction, user_id: str, command: Optional[str]=None, scope: Optional[str]=None, guild_id: Optional[str]=None) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        g_id = int(guild_id) if guild_id else interaction.guild_id or 0
        grant_type = 'command' if command else 'scope'
        grant_value = command or scope or ''
        ok = await self.bot.resolver.remove_grant(g_id, 'user', int(user_id), grant_type, grant_value)
        eph = ephemeral_for(interaction.user.id)
        msg = f'✅ Revoked `{grant_value}` from <@{user_id}>.' if ok else '❌ Grant not found.'
        await interaction.response.send_message(msg, ephemeral=eph)

    @grants_group.command(name='list', description='[Owner] List active grants in a guild.')
    @app_commands.describe(guild_id='Guild ID (default: current)')
    async def grants_list(self, interaction: discord.Interaction, guild_id: Optional[str]=None) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        g_id = int(guild_id) if guild_id else interaction.guild_id or 0
        rows = await self.bot.resolver.list_grants(g_id)
        eph = ephemeral_for(interaction.user.id)
        if not rows:
            await interaction.response.send_message('No grants found.', ephemeral=eph)
            return
        lines = []
        for r in rows:
            lines.append(f"`{r['target_type']}:{r['target_id']}` → {r['grant_type']}:`{r['grant_value']}`")
        embed = discord.Embed(title=f'Grants — Guild {g_id}', description='\n'.join(lines[:25]), colour=5793266)
        await interaction.response.send_message(embed=embed, ephemeral=eph)

    @grants_group.command(name='check', description='[Owner] Check what a user has access to.')
    @app_commands.describe(user_id='Target user ID', guild_id='Guild ID')
    async def grants_check(self, interaction: discord.Interaction, user_id: str, guild_id: Optional[str]=None) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        g_id = int(guild_id) if guild_id else interaction.guild_id or 0
        grants = await self.bot.resolver.user_grants(g_id, int(user_id))
        eph = ephemeral_for(interaction.user.id)
        if not grants:
            await interaction.response.send_message(f'No grants for <@{user_id}>.', ephemeral=eph)
            return
        lines = [f"{r['grant_type']}:`{r['grant_value']}`" for r in grants]
        await interaction.response.send_message(f'Grants for <@{user_id}>:\n' + '\n'.join(lines), ephemeral=eph)

    @app_commands.command(name='guildlist', description='[Owner] List all guilds the bot is in.')
    async def guildlist(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        lines = [f'**{g.name}** (`{g.id}`) — {g.member_count} members' for g in self.bot.guilds]
        embed = discord.Embed(title=f'Guilds ({len(self.bot.guilds)})', description='\n'.join(lines[:20]), colour=5793266)
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=eph)

    @app_commands.command(name='guildleave', description='[Owner] Force the bot to leave a guild.')
    @app_commands.describe(guild_id='Guild ID to leave')
    async def guildleave(self, interaction: discord.Interaction, guild_id: str) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            await interaction.response.send_message('❌ Guild not found.', ephemeral=True)
            return
        await guild.leave()
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(f'✅ Left guild `{guild_id}`.', ephemeral=eph)

    @app_commands.command(name='globalban', description='[Owner] Ban a user from all guilds.')
    @app_commands.describe(user_id='Target user ID', reason='Ban reason')
    async def globalban(self, interaction: discord.Interaction, user_id: str, reason: str='Global ban') -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        uid = int(user_id)
        count = 0
        for guild in self.bot.guilds:
            try:
                await guild.ban(discord.Object(id=uid), reason=f'[GlobalBan] {reason}')
                count += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                pass
        await interaction.followup.send(f'✅ Banned from {count}/{len(self.bot.guilds)} guilds.', ephemeral=eph)

    @app_commands.command(name='shutdown', description='[Owner] Graceful shutdown.')
    async def shutdown(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message('👋 Shutting down...', ephemeral=eph)
        await self.bot.close()

    @app_commands.command(name='reload', description='[Owner] Hot-reload a cog.')
    @app_commands.describe(cog='Cog module path (e.g. moderation.commands)')
    async def reload(self, interaction: discord.Interaction, cog: str) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        try:
            await self.bot.reload_extension(cog)
            eph = ephemeral_for(interaction.user.id)
            await interaction.response.send_message(f'✅ Reloaded `{cog}`.', ephemeral=eph)
        except Exception as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='configreload', description='[Owner] Reload config.yml from disk.')
    async def configreload(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('❌ Owner only.', ephemeral=True)
            return
        try:
            from core.config import reload_config
            from configure.merger import apply_guild_db_overrides
            new_cfg = reload_config()
            self.bot.config = new_cfg
            for guild in self.bot.guilds:
                await apply_guild_db_overrides(self.bot, guild.id)
            eph = ephemeral_for(interaction.user.id)
            await interaction.response.send_message('✅ Config reloaded.', ephemeral=eph)
        except Exception as exc:
            await interaction.response.send_message(f'❌ Failed: {exc}', ephemeral=True)

    @app_commands.command(name='botinfo', description='[Owner] Detailed system and runtime diagnostics.')
    async def botinfo(self, interaction: discord.Interaction) -> None:
        if not await _check_perm(interaction, 'botinfo'):
            return
        proc = psutil.Process(os.getpid())
        with proc.oneshot():
            mem_info = proc.memory_info()
            cpu_pct = proc.cpu_percent(interval=None)
            threads = proc.num_threads()
            fds = proc.num_fds() if hasattr(proc, 'num_fds') else 0
            create_ts = proc.create_time()
            io = proc.io_counters() if hasattr(proc, 'io_counters') else None
        uptime_s = self.bot.uptime_seconds
        days, rem = divmod(int(uptime_s), 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = (f'{days}d ' if days else '') + f'{hours:02d}h {mins:02d}m {secs:02d}s'
        started_at = datetime.datetime.fromtimestamp(create_ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        rss_mb = mem_info.rss / 1024 / 1024
        vms_mb = mem_info.vms / 1024 / 1024
        sys_vm = psutil.virtual_memory()
        sys_used_gb = (sys_vm.total - sys_vm.available) / 1024 ** 3
        sys_total_gb = sys_vm.total / 1024 ** 3
        mem_pct = sys_vm.percent
        sys_cpu_pct = psutil.cpu_percent(interval=None)
        cpu_count_l = psutil.cpu_count(logical=True)
        cpu_count_p = psutil.cpu_count(logical=False)
        cpu_freq = psutil.cpu_freq()
        freq_str = f'{cpu_freq.current:.0f} MHz  (max {cpu_freq.max:.0f} MHz)' if cpu_freq else 'n/a'
        disk = psutil.disk_usage('/')
        disk_used_gb = disk.used / 1024 ** 3
        disk_total_gb = disk.total / 1024 ** 3
        net = psutil.net_io_counters()
        net_sent_mb = net.bytes_sent / 1024 / 1024
        net_recv_mb = net.bytes_recv / 1024 / 1024
        total_members = sum((g.member_count or 0 for g in self.bot.guilds))
        total_channels = sum((len(g.channels) for g in self.bot.guilds))
        total_roles = sum((len(g.roles) for g in self.bot.guilds))
        cached_msgs = len(self.bot.cached_messages)
        loaded_cogs = len(self.bot.cogs)
        latency_ms = round(self.bot.latency * 1000, 2)
        db = self.bot.db
        try:
            pool_size = db._backend._pool.maxsize if db and hasattr(db, '_backend') else 0
        except Exception:
            pool_size = 0
        e = discord.Embed(title='nonon', description='production discord bot — knowledge sync, moderation, logging, analytics\ndeveloped by **atar**', colour=1710638, timestamp=discord.utils.utcnow())
        e.add_field(name='process', value=f'```\npid       {os.getpid()}\nuptime    {uptime_str}\nstarted   {started_at}\nthreads   {threads}\nfds       {fds}\n```', inline=False)
        e.add_field(name='memory', value=f'```\nrss       {rss_mb:.1f} MB\nvms       {vms_mb:.1f} MB\ncpu%      {cpu_pct:.1f}%\nsys used  {sys_used_gb:.2f} / {sys_total_gb:.2f} GB  ({mem_pct:.1f}%)\n```', inline=True)
        e.add_field(name='cpu', value=f'```\ncores     {cpu_count_p}p / {cpu_count_l}l\nfreq      {freq_str}\nsys cpu%  {sys_cpu_pct:.1f}%\n```', inline=True)
        e.add_field(name='disk & network', value=f'```\ndisk      {disk_used_gb:.1f} / {disk_total_gb:.1f} GB  ({disk.percent:.1f}%)\nnet tx    {net_sent_mb:.1f} MB\nnet rx    {net_recv_mb:.1f} MB\n```', inline=False)
        e.add_field(name='discord', value=f'```\nguilds    {len(self.bot.guilds)}\nmembers   {total_members}\nchannels  {total_channels}\nroles     {total_roles}\nlatency   {latency_ms} ms\ncache     {cached_msgs} messages\n```', inline=True)
        e.add_field(name='runtime', value=f'```\npython    {platform.python_version()}\ndiscord   {discord.__version__}\ncogs      {loaded_cogs}\ndb pool   {pool_size}\nhost      {platform.node()}\nos        {platform.system()} {platform.release()}\narch      {platform.machine()}\n```', inline=True)
        if io:
            e.add_field(name='process i/o', value=f'```\nread      {io.read_bytes / 1024 / 1024:.1f} MB\nwritten   {io.write_bytes / 1024 / 1024:.1f} MB\n```', inline=False)
        e.set_footer(text='nonon  ·  developed by atar')
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.send_message(embed=e, ephemeral=eph)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(ModerationCommands(_a))
    await _a.add_cog(OwnerCommands(_a))