from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks
import structlog
from providers.base import BaseProvider, ProviderSnapshot, SourceFile
from core.config import SourceConfig
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
    from discord_layer.channel_manager import ChannelManager
    from discord_layer.permission_manager import PermissionManager
log: structlog.BoundLogger = structlog.get_logger(__name__)

def _build_provider(_a: SourceConfig) -> BaseProvider:
    _b = _a.type.lower()
    if _b == 'local':
        from providers.local import LocalProvider
        return LocalProvider(_a)
    if _b == 'github':
        from providers.github import GitHubProvider
        return GitHubProvider(_a)
    if _b == 'ftp':
        from providers.remote import FTPProvider
        return FTPProvider(_a)
    if _b == 'gdrive':
        from providers.remote import GDriveProvider
        return GDriveProvider(_a)
    if _b == 'onedrive':
        from providers.remote import OneDriveProvider
        return OneDriveProvider(_a)
    raise ValueError(f'Unknown provider type: {_b!r}')

class GuildSyncState:

    def __init__(self) -> None:
        self.provider: BaseProvider | None = None
        self.last_snapshot: ProviderSnapshot | None = None
        self.task: asyncio.Task | None = None
        self.status: str = 'idle'
        self.file_count: int = 0
        self.last_error: str | None = None

class SyncEngine(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._states: dict[int, GuildSyncState] = {}

    async def cog_load(self) -> None:
        await asyncio.sleep(2)
        for guild in self.bot.guilds:
            await self._start_guild_sync(guild.id)

    async def cog_unload(self) -> None:
        for state in self._states.values():
            if state.task and (not state.task.done()):
                state.task.cancel()
            if state.provider:
                await state.provider.close()

    async def _start_guild_sync(self, guild_id: int) -> None:
        state = self._states.get(guild_id) or GuildSyncState()
        self._states[guild_id] = state
        if state.task and (not state.task.done()):
            state.task.cancel()
        src_cfg = self.bot.config.get_guild_source(guild_id)
        state.provider = _build_provider(src_cfg)
        state.task = asyncio.create_task(self._sync_loop(guild_id, state), name=f'sync-{guild_id}')
        log.info('sync.started', guild=guild_id, provider=src_cfg.type)

    async def _sync_loop(self, guild_id: int, state: GuildSyncState) -> None:
        assert state.provider
        try:
            snap = await state.provider.fetch_snapshot()
            if snap.ok:
                await self._apply_snapshot(guild_id, state, snap)
            async for snap in state.provider.watch():
                if not snap.ok:
                    log.warning('sync.snapshot_error', guild=guild_id, error=snap.error)
                    state.last_error = snap.error
                    continue
                await self._apply_snapshot(guild_id, state, snap)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error('sync.loop_crashed', guild=guild_id, error=str(exc), exc_info=exc)
            state.status = 'error'
            state.last_error = str(exc)

    async def _apply_snapshot(self, guild_id: int, state: GuildSyncState, snap: ProviderSnapshot) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        cm: ChannelManager = self.bot.cogs.get('ChannelManager')
        pm: PermissionManager = self.bot.cogs.get('PermissionManager')
        if not cm:
            return
        cfg = self.bot.config.get_guild_discord(guild_id)
        state.status = 'syncing'
        old_by_path = state.last_snapshot.by_path() if state.last_snapshot else {}
        new_by_path = snap.by_path()
        added = {p: f for p, f in new_by_path.items() if p not in old_by_path}
        deleted = {p: f for p, f in old_by_path.items() if p not in new_by_path}
        changed = {p: f for p, f in new_by_path.items() if p in old_by_path and f.content != old_by_path[p].content}
        log.info('sync.diff', guild=guild_id, added=len(added), changed=len(changed), deleted=len(deleted))
        folders: dict[str, list[SourceFile]] = {}
        for f in snap.files:
            folders.setdefault(f.folder or 'root', []).append(f)
        for folder_name, files in folders.items():
            cat = await cm.get_or_create_category(guild, folder_name)
            for f in files:
                if f.path in added or f.path in changed:
                    ch = await cm.get_or_create_channel(guild, cat, f.name)
                    await cm.update_channel_content(ch, f.content)
                    if pm:
                        await pm.apply_channel_permissions(ch, folder_name)
                    await asyncio.sleep(0.3)
        if cfg.delete_orphaned_channels:
            for path, f in deleted.items():
                safe_name = cm._sanitise_channel_name(f.name)
                for ch in guild.text_channels:
                    if ch.name == safe_name:
                        await cm.delete_channel(ch, reason='Orphaned sync channel')
                        break
        state.last_snapshot = snap
        state.file_count = len(snap.files)
        state.status = 'idle'
        await self.bot.db.execute("\n            INSERT INTO sync_state (guild_id, last_sync_at, file_count, status)\n            VALUES (?, datetime('now'), ?, 'idle')\n            ON CONFLICT(guild_id) DO UPDATE SET\n                last_sync_at = excluded.last_sync_at,\n                file_count = excluded.file_count,\n                status = 'idle',\n                last_error = null,\n                updated_at = datetime('now')\n            ", (guild_id, len(snap.files)))

    async def force_sync(self, guild_id: int) -> str:
        state = self._states.get(guild_id)
        if not state or not state.provider:
            await self._start_guild_sync(guild_id)
            return 'Sync started.'
        snap = await state.provider.fetch_snapshot()
        if not snap.ok:
            return f'Provider error: {snap.error}'
        await self._apply_snapshot(guild_id, state, snap)
        return f'Sync complete. {state.file_count} files processed.'

    def get_status(self, guild_id: int) -> dict:
        state = self._states.get(guild_id)
        if not state:
            return {'status': 'not_started', 'file_count': 0}
        return {'status': state.status, 'file_count': state.file_count, 'last_error': state.last_error}
    sync_group = discord.app_commands.Group(name='sync', description='Knowledge base sync commands')

    @sync_group.command(name='run', description='Force a full knowledge base sync for this guild.')
    async def sync_run(self, interaction: discord.Interaction) -> None:
        from core.permissions import require_permission
        bot: KnowledgeBot = interaction.client
        if not await bot.resolver.can_use(interaction.user, 'sync', interaction.guild_id or 0):
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await self.force_sync(interaction.guild_id or 0)
        await interaction.followup.send(f'✅ {result}', ephemeral=True)

    @sync_group.command(name='status', description='Show current sync state and file count.')
    async def sync_status(self, interaction: discord.Interaction) -> None:
        bot: KnowledgeBot = interaction.client
        if not await bot.resolver.can_use(interaction.user, 'syncstatus', interaction.guild_id or 0):
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        info = self.get_status(interaction.guild_id or 0)
        embed = discord.Embed(title='📡 Sync Status', colour=5793266)
        embed.add_field(name='Status', value=info['status'])
        embed.add_field(name='Files', value=str(info['file_count']))
        if info.get('last_error'):
            embed.add_field(name='Last Error', value=info['last_error'][:200], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @sync_group.command(name='perms', description='Re-apply all channel permission overwrites.')
    async def sync_perms(self, interaction: discord.Interaction) -> None:
        bot: KnowledgeBot = interaction.client
        if not await bot.resolver.can_use(interaction.user, 'syncperms', interaction.guild_id or 0):
            await interaction.response.send_message('❌ Permission denied.', ephemeral=True)
            return
        pm: PermissionManager = bot.cogs.get('PermissionManager')
        if pm and interaction.guild:
            await pm.apply_all_guild_permissions(interaction.guild)
        await interaction.response.send_message('✅ Permissions re-applied.', ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        guild_id = message.guild.id
        src_cfg = self.bot.config.get_guild_source(guild_id)
        if not src_cfg.sync_allow_replies:
            return
        if src_cfg.type.lower() != 'local':
            return
        state = self._states.get(guild_id)
        if not state or not state.last_snapshot:
            return
        source_file = self._channel_to_file(message.channel.name, state)
        if source_file is None:
            return
        quote = self._format_quote(message)
        root = src_cfg.path
        file_path = __import__('pathlib').Path(root) / source_file.path
        try:
            import aiofiles
            async with aiofiles.open(file_path, 'a', encoding='utf-8') as f:
                await f.write(quote)
            log.info('sync.reply.appended', guild=guild_id, channel=message.channel.name, file=source_file.path, author=str(message.author))
        except Exception as exc:
            log.error('sync.reply.write_failed', file=str(file_path), error=str(exc))
            return
        try:
            await message.add_reaction('✅')
        except discord.HTTPException:
            pass
        delete_after = src_cfg.sync_reply_delete_after
        if delete_after > 0:
            await asyncio.sleep(delete_after)
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    def _channel_to_file(self, channel_name: str, state: 'GuildSyncState') -> 'SourceFile | None':
        from discord_layer.channel_manager import ChannelManager
        cm: ChannelManager | None = self.bot.cogs.get('ChannelManager')
        if cm is None:
            return None
        for f in state.last_snapshot.files:
            if cm._sanitise_channel_name(f.name) == channel_name:
                return f
        return None

    @staticmethod
    def _format_quote(message: discord.Message) -> str:
        import datetime as _dt
        ts = message.created_at.strftime('%Y-%m-%d %H:%M UTC')
        author = str(message.author.display_name)
        header = f'> **{author}** · {ts}'
        body_lines = (message.content or '').split('\n')
        quoted = '\n'.join((f'> {line}' if line.strip() else '>' for line in body_lines))
        attachment_note = ''
        if message.attachments:
            names = ', '.join((f'`{a.filename}`' for a in message.attachments))
            attachment_note = f'\n> 📎 {names}'
        return f'\n\n---\n{header}\n{quoted}{attachment_note}\n'

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(SyncEngine(_a))