from __future__ import annotations
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

class PermissionManager(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    async def apply_channel_permissions(self, channel: discord.TextChannel, folder_key: str) -> None:
        guild = channel.guild
        perm_cfg = self.bot.config.get_guild_permissions(guild.id)
        folder_spec = perm_cfg.overrides.get(folder_key) or perm_cfg.default
        allow_roles: list[str] = folder_spec.get('roles', ['@everyone'])
        deny_roles: list[str] = folder_spec.get('deny_roles', [])
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}
        for role_name in deny_roles:
            role = self._resolve_role(guild, role_name)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=False, send_messages=False)
        for role_name in allow_roles:
            role = self._resolve_role(guild, role_name)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
        if not overwrites:
            return
        cfg = self.bot.config.get_guild_discord(guild.id)
        if cfg.dry_run:
            log.info('dry_run.permissions.apply', channel=channel.id, folder=folder_key)
            return
        try:
            await channel.edit(overwrites=overwrites)
            log.info('permissions.applied', channel=channel.id, folder=folder_key)
        except discord.HTTPException as exc:
            log.error('permissions.apply_failed', channel=channel.id, error=str(exc))

    async def apply_all_guild_permissions(self, guild: discord.Guild) -> None:
        perm_cfg = self.bot.config.get_guild_permissions(guild.id)
        prefix = self.bot.config.get_guild_discord(guild.id).sync_category_prefix
        for channel in guild.text_channels:
            if channel.category and channel.category.name.startswith(prefix):
                folder_key = channel.category.name.removeprefix(prefix).lower()
                await self.apply_channel_permissions(channel, folder_key)

    @staticmethod
    def _resolve_role(guild: discord.Guild, name: str) -> discord.Role | None:
        if name == '@everyone':
            return guild.default_role
        return discord.utils.get(guild.roles, name=name)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(PermissionManager(_a))