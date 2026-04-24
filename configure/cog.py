from __future__ import annotations
import os
from typing import Any, TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands
import structlog
from configure.store import GuildConfigStore, SECTION_MODELS
from configure.merger import apply_guild_db_overrides
from configure.coerce import coerce_value
from core.permissions import owner_only
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
COLOR_SUCCESS = 5763719
COLOR_INFO = 5793266
COLOR_WARNING = 16705372
COLOR_ERROR = 15548997
COLOR_RESET = 15418782

async def _check(_c: discord.Interaction) -> bool:
    _b: 'KnowledgeBot' = _c.client
    _a = await _b.resolver.can_use(_c.user, 'configure', _c.guild_id or 0)
    if not _a:
        await _c.response.send_message('you need the `configuration` scope or be the bot owner.', ephemeral=True)
        return False
    return True

def _base_embed(_g: str, _a: int, _f: discord.Interaction) -> discord.Embed:
    _b = discord.Embed(title=_g, color=_a)
    _b.timestamp = discord.utils.utcnow()
    _c = _f.guild
    _e = _c.name if _c else 'unknown'
    _d = _f.guild_id or 0
    _b.set_footer(text=f'guild: {_e} • {_d} | updated by: {_f.user}')
    return _b

class ConfirmView(discord.ui.View):

    def __init__(self) -> None:
        super().__init__(timeout=30)
        self.confirmed: bool | None = None

    @discord.ui.button(label='confirm', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label='cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()

def _make_key_autocomplete(_d: str):

    async def autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        _c = SECTION_MODELS.get(_d)
        if _c is None:
            return []
        _b = list(_c.model_fields.keys())
        return [app_commands.Choice(name=_a, value=_a) for _a in _b if current.lower() in _a.lower()][:25]
    return autocomplete

def _make_bool_key_autocomplete(_e: str):
    import inspect

    async def autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        _d = SECTION_MODELS.get(_e)
        if _d is None:
            return []
        _c = [_b for _b, _a in _d.model_fields.items() if _a.annotation is bool]
        return [app_commands.Choice(name=_b, value=_b) for _b in _c if current.lower() in _b.lower()][:25]
    return autocomplete

def _make_nonbool_key_autocomplete(_e: str):

    async def autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        _d = SECTION_MODELS.get(_e)
        if _d is None:
            return []
        _c = [_b for _b, _a in _d.model_fields.items() if _a.annotation is not bool]
        return [app_commands.Choice(name=_b, value=_b) for _b in _c if current.lower() in _b.lower()][:25]
    return autocomplete

async def _section_autocomplete(_b: discord.Interaction, _a: str) -> list[app_commands.Choice[str]]:
    _d = list(SECTION_MODELS.keys())
    return [app_commands.Choice(name=_c, value=_c) for _c in _d if _a.lower() in _c.lower()][:25]

class ConfigureCog(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self.store = GuildConfigStore(bot.db)
    configure_group = app_commands.Group(name='configure', description='configure nonon for this guild.', guild_only=True)
    channels_group = app_commands.Group(name='channels', description='assign log channels for this guild.', parent=configure_group)

    async def _set_channel(self, interaction: discord.Interaction, field: str, channel: discord.TextChannel, label: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        try:
            await self.store.set(guild_id, 'discord', field, channel.id, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error setting channel', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            log.error('configure.channel.set_failed', guild_id=guild_id, field=field, error=str(exc))
            return
        embed = _base_embed(f'channel set — {label}', COLOR_SUCCESS, interaction)
        embed.add_field(name='field', value=f'`{field}`', inline=True)
        embed.add_field(name='channel', value=channel.mention, inline=True)
        embed.add_field(name='id', value=str(channel.id), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.channel.set', guild_id=guild_id, section='discord', key=field, value=channel.id, updated_by=interaction.user.id)

    @channels_group.command(name='log', description='general event log channel')
    async def channels_log(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'log_channel_id', channel, 'general log')

    @channels_group.command(name='audit', description='audit trail channel')
    async def channels_audit(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'audit_channel_id', channel, 'audit')

    @channels_group.command(name='mod-log', description='moderation actions channel')
    async def channels_mod_log(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'mod_log_channel_id', channel, 'mod log')

    @channels_group.command(name='archive', description='deleted attachment re-upload channel')
    async def channels_archive(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'archive_channel_id', channel, 'archive')

    @channels_group.command(name='voice-log', description='voice join/leave/move channel')
    async def channels_voice_log(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'voice_log_channel_id', channel, 'voice log')

    @channels_group.command(name='status', description='weekly analytics summaries channel')
    async def channels_status(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'status_channel_id', channel, 'status')

    @channels_group.command(name='console', description='live console relay channel (read-only)')
    async def channels_console(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self._set_channel(interaction, 'console_channel_id', channel, 'console relay')

    @channels_group.command(name='forward-to', description="forward all logs to another guild's channels")
    async def channels_forward_to(self, interaction: discord.Interaction, guild_id: str) -> None:
        if not await _check(interaction):
            return
        try:
            target_id = int(guild_id)
        except ValueError:
            embed = _base_embed('invalid guild id', COLOR_ERROR, interaction)
            embed.description = f'`{guild_id}` is not a valid integer.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if target_id == 0:
            embed = _base_embed('invalid guild id', COLOR_ERROR, interaction)
            embed.description = 'guild id must be non-zero.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if self.bot.get_guild(target_id) is None:
            embed = _base_embed('guild not found', COLOR_ERROR, interaction)
            embed.description = f'bot is not a member of guild `{target_id}`.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        current_guild_id = interaction.guild_id or 0
        try:
            await self.store.set(current_guild_id, 'discord', 'log_to_guild_id', target_id, interaction.user.id)
            await apply_guild_db_overrides(self.bot, current_guild_id)
        except Exception as exc:
            embed = _base_embed('error setting log forwarding', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        target_guild = self.bot.get_guild(target_id)
        embed = _base_embed('log forwarding set', COLOR_SUCCESS, interaction)
        embed.add_field(name='forwarding to', value=f'{target_guild.name} (`{target_id}`)', inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.channel.forward_to', guild_id=current_guild_id, target_guild_id=target_id, updated_by=interaction.user.id)

    @channels_group.command(name='show', description='list all configured log channels for this guild')
    async def channels_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import DiscordConfig
        channel_fields = [('log_channel_id', 'general log'), ('audit_channel_id', 'audit'), ('mod_log_channel_id', 'mod log'), ('archive_channel_id', 'archive'), ('voice_log_channel_id', 'voice log'), ('status_channel_id', 'status'), ('console_channel_id', 'console relay'), ('log_to_guild_id', 'forward-to guild')]
        discord_cfg = self.bot.config.get_guild_discord(guild_id)
        embed = _base_embed('configured channels', COLOR_INFO, interaction)
        for field_name, label in channel_fields:
            raw_id = getattr(discord_cfg, field_name, None)
            if raw_id is None:
                embed.add_field(name=label, value='*(not set)*', inline=True)
            else:
                ch = self.bot.get_channel(raw_id)
                if ch is not None:
                    display = f'{ch.mention} (`{raw_id}`)'
                else:
                    display = f'`{raw_id}` ⚠️'
                embed.add_field(name=label, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    logging_group = app_commands.Group(name='logging', description='configure event logging for this guild.', parent=configure_group)

    @logging_group.command(name='toggle', description='enable or disable a logging event type')
    @app_commands.autocomplete(event=_make_bool_key_autocomplete('logging'))
    async def logging_toggle(self, interaction: discord.Interaction, event: str, enabled: bool) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import LoggingConfig
        if event not in LoggingConfig.model_fields:
            embed = _base_embed('unknown event', COLOR_ERROR, interaction)
            embed.description = f'`{event}` is not a valid logging field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = LoggingConfig.model_fields[event]
        if field_info.annotation is not bool:
            embed = _base_embed('not a toggle', COLOR_ERROR, interaction)
            embed.description = f'`{event}` is not a boolean field. use `/configure logging set` instead.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'logging', event, enabled, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        icon = '✅' if enabled else '❌'
        embed = _base_embed('logging toggle updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='event', value=f'`{event}`', inline=True)
        embed.add_field(name='state', value=f'{icon} `{enabled}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.logging.toggle', guild_id=guild_id, key=event, value=enabled, updated_by=interaction.user.id)

    @logging_group.command(name='set', description='set a non-boolean logging parameter')
    @app_commands.autocomplete(key=_make_nonbool_key_autocomplete('logging'))
    async def logging_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import LoggingConfig
        if key not in LoggingConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid logging field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = LoggingConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'logging', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('logging config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.logging.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @logging_group.command(name='show', description='display all logging config for this guild')
    async def logging_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_logging(guild_id)
        embed = _base_embed('logging config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            if isinstance(v, bool):
                display = '✅' if v else '❌'
            else:
                display = f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    moderation_group = app_commands.Group(name='moderation', description='configure moderation settings for this guild.', parent=configure_group)

    @moderation_group.command(name='set', description='set a moderation threshold or parameter')
    @app_commands.autocomplete(key=_make_nonbool_key_autocomplete('moderation'))
    async def moderation_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import ModerationConfig
        if key not in ModerationConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid moderation field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = ModerationConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'moderation', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('moderation config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.moderation.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @moderation_group.command(name='toggle', description='enable or disable an automod check')
    @app_commands.autocomplete(check=_make_bool_key_autocomplete('moderation'))
    async def moderation_toggle(self, interaction: discord.Interaction, check: str, enabled: bool) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import ModerationConfig
        if check not in ModerationConfig.model_fields:
            embed = _base_embed('unknown check', COLOR_ERROR, interaction)
            embed.description = f'`{check}` is not a valid moderation field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = ModerationConfig.model_fields[check]
        if field_info.annotation is not bool:
            embed = _base_embed('not a toggle', COLOR_ERROR, interaction)
            embed.description = f'`{check}` is not a boolean field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'moderation', check, enabled, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        icon = '✅' if enabled else '❌'
        embed = _base_embed('automod toggle updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='check', value=f'`{check}`', inline=True)
        embed.add_field(name='state', value=f'{icon} `{enabled}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.moderation.toggle', guild_id=guild_id, key=check, value=enabled, updated_by=interaction.user.id)

    async def _list_command(self, interaction: discord.Interaction, section: str, key: str, action: str, item: str | None, label: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        try:
            current = await self.store.get(guild_id, section, key)
            if current is None:
                cfg = getattr(self.bot.config, f'get_guild_{section}', None)
                if cfg:
                    current = getattr(cfg(guild_id), key, [])
                else:
                    current = getattr(self.bot.config.moderation, key, [])
            current = list(current) if current else []
        except Exception as exc:
            embed = _base_embed('error reading config', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if action == 'list':
            embed = _base_embed(f'{label} list', COLOR_INFO, interaction)
            embed.description = '\n'.join((f'`{w}`' for w in current)) if current else '*(empty)*'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if item is None:
            embed = _base_embed('missing argument', COLOR_ERROR, interaction)
            embed.description = f'`item` is required for `{action}`.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if action == 'add':
            if item not in current:
                current.append(item)
        elif action == 'remove':
            if item in current:
                current.remove(item)
            else:
                embed = _base_embed('not found', COLOR_WARNING, interaction)
                embed.description = f'`{item}` was not in the {label} list.'
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        try:
            await self.store.set(guild_id, section, key, current, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error saving config', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed(f'{label} {action}ed', COLOR_SUCCESS, interaction)
        embed.add_field(name='item', value=f'`{item}`', inline=True)
        embed.add_field(name='total entries', value=str(len(current)), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info(f'configure.moderation.{key}.{action}', guild_id=guild_id, item=item, updated_by=interaction.user.id)

    @moderation_group.command(name='banned-words', description='manage the banned words list')
    @app_commands.choices(action=[app_commands.Choice(name='add', value='add'), app_commands.Choice(name='remove', value='remove'), app_commands.Choice(name='list', value='list')])
    async def moderation_banned_words(self, interaction: discord.Interaction, action: str, word: str | None=None) -> None:
        await self._list_command(interaction, 'moderation', 'banned_words', action, word, 'banned words')

    @moderation_group.command(name='banned-patterns', description='manage the banned regex patterns list')
    @app_commands.choices(action=[app_commands.Choice(name='add', value='add'), app_commands.Choice(name='remove', value='remove'), app_commands.Choice(name='list', value='list')])
    async def moderation_banned_patterns(self, interaction: discord.Interaction, action: str, pattern: str | None=None) -> None:
        await self._list_command(interaction, 'moderation', 'banned_patterns', action, pattern, 'banned patterns')

    @moderation_group.command(name='link-whitelist', description='manage the link whitelist')
    @app_commands.choices(action=[app_commands.Choice(name='add', value='add'), app_commands.Choice(name='remove', value='remove'), app_commands.Choice(name='list', value='list')])
    async def moderation_link_whitelist(self, interaction: discord.Interaction, action: str, domain: str | None=None) -> None:
        await self._list_command(interaction, 'moderation', 'link_whitelist', action, domain, 'link whitelist')

    async def _roles_command(self, interaction: discord.Interaction, key: str, action: str, role: discord.Role | None, label: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        try:
            current = await self.store.get(guild_id, 'moderation', key)
            if current is None:
                current = list(getattr(self.bot.config.get_guild_moderation(guild_id), key, []))
            current = list(current) if current else []
        except Exception as exc:
            embed = _base_embed('error reading config', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if action == 'list':
            embed = _base_embed(f'{label}', COLOR_INFO, interaction)
            embed.description = '\n'.join((f'`{r}`' for r in current)) if current else '*(empty)*'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if role is None:
            embed = _base_embed('missing argument', COLOR_ERROR, interaction)
            embed.description = '`role` is required for add/remove.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if action == 'add':
            if role.name not in current:
                current.append(role.name)
        elif action == 'remove':
            if role.name in current:
                current.remove(role.name)
            else:
                embed = _base_embed('not found', COLOR_WARNING, interaction)
                embed.description = f'`{role.name}` was not in {label}.'
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        try:
            await self.store.set(guild_id, 'moderation', key, current, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error saving config', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed(f'{label} {action}ed', COLOR_SUCCESS, interaction)
        embed.add_field(name='role', value=role.mention, inline=True)
        embed.add_field(name='total entries', value=str(len(current)), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info(f'configure.moderation.{key}.{action}', guild_id=guild_id, role=role.name, updated_by=interaction.user.id)

    @moderation_group.command(name='mod-roles', description='manage the list of moderator role names')
    @app_commands.choices(action=[app_commands.Choice(name='add', value='add'), app_commands.Choice(name='remove', value='remove'), app_commands.Choice(name='list', value='list')])
    async def moderation_mod_roles(self, interaction: discord.Interaction, action: str, role: discord.Role | None=None) -> None:
        await self._roles_command(interaction, 'mod_roles', action, role, 'mod roles')

    @moderation_group.command(name='admin-roles', description='manage the list of admin role names')
    @app_commands.choices(action=[app_commands.Choice(name='add', value='add'), app_commands.Choice(name='remove', value='remove'), app_commands.Choice(name='list', value='list')])
    async def moderation_admin_roles(self, interaction: discord.Interaction, action: str, role: discord.Role | None=None) -> None:
        await self._roles_command(interaction, 'admin_roles', action, role, 'admin roles')

    @moderation_group.command(name='show', description='display all moderation config for this guild')
    async def moderation_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_moderation(guild_id)
        embed = _base_embed('moderation config', COLOR_INFO, interaction)
        toggle_lines = []
        scalar_lines = []
        list_lines = []
        for k, v in cfg.model_dump().items():
            if isinstance(v, bool):
                toggle_lines.append(f"{('✅' if v else '❌')} `{k}`")
            elif isinstance(v, list):
                preview = ', '.join((str(x) for x in v[:5]))
                if len(v) > 5:
                    preview += f'… (+{len(v) - 5})'
                list_lines.append(f"`{k}`: {preview or '*(empty)*'}")
            else:
                scalar_lines.append(f'`{k}`: `{v}`')
        if toggle_lines:
            embed.add_field(name='automod toggles', value='\n'.join(toggle_lines), inline=False)
        if scalar_lines:
            embed.add_field(name='thresholds', value='\n'.join(scalar_lines), inline=False)
        if list_lines:
            embed.add_field(name='lists', value='\n'.join(list_lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    analytics_group = app_commands.Group(name='analytics', description='configure analytics for this guild.', parent=configure_group)

    @analytics_group.command(name='set', description='set an analytics config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('analytics'))
    async def analytics_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import AnalyticsConfig
        if key not in AnalyticsConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid analytics field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = AnalyticsConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'analytics', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('analytics config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.analytics.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @analytics_group.command(name='show', description='display analytics config for this guild')
    async def analytics_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_analytics(guild_id)
        embed = _base_embed('analytics config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            display = ('✅' if v else '❌') if isinstance(v, bool) else f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    markov_group = app_commands.Group(name='markov', description='configure markov chain generation for this guild.', parent=configure_group)

    @markov_group.command(name='set', description='set a markov config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('markov'))
    async def markov_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import MarkovConfig
        if key not in MarkovConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid markov field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = MarkovConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'markov', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('markov config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.markov.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @markov_group.command(name='show', description='display markov config for this guild')
    async def markov_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        override = self.bot.config.guilds.get(str(guild_id))
        cfg = (override.markov if override and override.markov else None) or self.bot.config.markov
        embed = _base_embed('markov config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            display = ('✅' if v else '❌') if isinstance(v, bool) else f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    captcha_group = app_commands.Group(name='captcha', description='configure captcha challenge parameters for this guild.', parent=configure_group)

    @captcha_group.command(name='set', description='set a captcha config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('captcha'))
    async def captcha_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import CaptchaConfig
        if key not in CaptchaConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid captcha field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = CaptchaConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        current_cfg_override = self.bot.config.guilds.get(str(guild_id))
        current_cfg = (current_cfg_override.captcha if current_cfg_override and current_cfg_override.captcha else None) or self.bot.config.captcha
        pending = current_cfg.model_dump()
        pending[key] = coerced
        if pending.get('count_min', 0) >= pending.get('count_max', 1):
            embed = _base_embed('validation error', COLOR_ERROR, interaction)
            embed.description = '`count_min` must be less than `count_max`.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if pending.get('phrase_min_words', 0) >= pending.get('phrase_max_words', 1):
            embed = _base_embed('validation error', COLOR_ERROR, interaction)
            embed.description = '`phrase_min_words` must be less than `phrase_max_words`.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'captcha', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('captcha config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.captcha.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @captcha_group.command(name='show', description='display captcha config for this guild')
    async def captcha_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        override = self.bot.config.guilds.get(str(guild_id))
        cfg = (override.captcha if override and override.captcha else None) or self.bot.config.captcha
        embed = _base_embed('captcha config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            display = ('✅' if v else '❌') if isinstance(v, bool) else f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    source_group = app_commands.Group(name='source', description='configure the knowledge sync source for this guild.', parent=configure_group)
    _SOURCE_CREDENTIAL_FIELDS = frozenset({'github_token_env', 'ftp_user_env', 'ftp_pass_env', 'gdrive_credentials_env', 'onedrive_client_id_env', 'onedrive_client_secret_env', 'onedrive_tenant_id_env'})

    @source_group.command(name='set', description='set a source config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('source'))
    async def source_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import SourceConfig
        if key not in SourceConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid source field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = SourceConfig.model_fields[key]
        if key == 'type' and value not in ('local', 'github', 'ftp', 'gdrive', 'onedrive'):
            embed = _base_embed('invalid type', COLOR_ERROR, interaction)
            embed.description = 'source type must be one of: `local`, `github`, `ftp`, `gdrive`, `onedrive`.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if key == 'poll_interval_seconds':
            try:
                iv = int(value)
            except ValueError:
                iv = -1
            if iv < 10:
                embed = _base_embed('validation error', COLOR_ERROR, interaction)
                embed.description = '`poll_interval_seconds` must be >= 10.'
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        if key == 'debounce_seconds':
            try:
                dv = int(value)
            except ValueError:
                dv = -1
            if dv < 1:
                embed = _base_embed('validation error', COLOR_ERROR, interaction)
                embed.description = '`debounce_seconds` must be >= 1.'
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'source', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('source config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.source.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @source_group.command(name='show', description='display source config for this guild')
    async def source_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_source(guild_id)
        embed = _base_embed('source config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            if k in self._SOURCE_CREDENTIAL_FIELDS:
                display = f'`{v}` *(env var name — value masked)*'
            elif isinstance(v, bool):
                display = '✅' if v else '❌'
            else:
                display = f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    media_group = app_commands.Group(name='media', description='configure media command settings for this guild.', parent=configure_group)

    @media_group.command(name='set', description='set a media config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('media'))
    async def media_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import MediaConfig
        if key not in MediaConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid media field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = MediaConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'media', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('media config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.media.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @media_group.command(name='show', description='display media config for this guild')
    async def media_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_media(guild_id)
        embed = _base_embed('media config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            display = ('✅' if v else '❌') if isinstance(v, bool) else f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    casino_group = app_commands.Group(name='casino', description='configure casino chip economy and slots settings for this guild.', parent=configure_group)

    @casino_group.command(name='set', description='set a casino config value')
    @app_commands.autocomplete(key=_make_key_autocomplete('casino'))
    async def casino_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import CasinoConfig
        if key not in CasinoConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid casino field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        field_info = CasinoConfig.model_fields[key]
        try:
            coerced = coerce_value(field_info.annotation, value)
        except ValueError as exc:
            embed = _base_embed('type error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        try:
            await self.store.set(guild_id, 'casino', key, coerced, interaction.user.id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('casino config updated', COLOR_SUCCESS, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        embed.add_field(name='value', value=f'`{coerced}`', inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        log.info('configure.casino.set', guild_id=guild_id, key=key, value=coerced, updated_by=interaction.user.id)

    @casino_group.command(name='get', description='get the current value of a casino config key')
    @app_commands.autocomplete(key=_make_key_autocomplete('casino'))
    async def casino_get(self, interaction: discord.Interaction, key: str) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        from core.config import CasinoConfig
        if key not in CasinoConfig.model_fields:
            embed = _base_embed('unknown key', COLOR_ERROR, interaction)
            embed.description = f'`{key}` is not a valid casino field.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        cfg = self.bot.config.get_guild_casino(guild_id)
        value = getattr(cfg, key)
        embed = _base_embed('casino config — get', COLOR_INFO, interaction)
        embed.add_field(name='key', value=f'`{key}`', inline=True)
        display = ('✅' if value else '❌') if isinstance(value, bool) else f'`{value}`'
        embed.add_field(name='value', value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @casino_group.command(name='reset', description='reset all casino config overrides to yaml defaults')
    async def casino_reset(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        view = ConfirmView()
        embed = _base_embed('confirm casino reset', COLOR_RESET, interaction)
        embed.description = 'this will reset **casino** settings to global yaml defaults for this guild.\nall db overrides for the casino section will be deleted.\n\nuse the buttons below to confirm or cancel.'
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            cancel_embed = _base_embed('reset cancelled', COLOR_INFO, interaction)
            await interaction.edit_original_response(embed=cancel_embed, view=None)
            return
        guild_id = interaction.guild_id or 0
        try:
            deleted = await self.store.reset_section(guild_id, 'casino')
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            err_embed = _base_embed('error during reset', COLOR_ERROR, interaction)
            err_embed.description = str(exc)
            await interaction.edit_original_response(embed=err_embed, view=None)
            return
        done_embed = _base_embed('casino config reset', COLOR_SUCCESS, interaction)
        done_embed.description = f'{deleted} override row(s) removed. using yaml defaults.'
        await interaction.edit_original_response(embed=done_embed, view=None)
        log.info('configure.casino.reset', guild_id=guild_id, deleted=deleted, updated_by=interaction.user.id)

    @casino_group.command(name='list', description='show all current casino config values for this guild')
    async def casino_list(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        cfg = self.bot.config.get_guild_casino(guild_id)
        embed = _base_embed('casino config', COLOR_INFO, interaction)
        for k, v in cfg.model_dump().items():
            if isinstance(v, bool):
                display = '✅' if v else '❌'
            else:
                display = f'`{v}`'
            embed.add_field(name=k, value=display, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @configure_group.command(name='show', description='display all configured overrides for this guild')
    async def configure_show(self, interaction: discord.Interaction) -> None:
        if not await _check(interaction):
            return
        guild_id = interaction.guild_id or 0
        try:
            all_kv = await self.store.get_all(guild_id)
        except Exception as exc:
            embed = _base_embed('error', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = _base_embed('guild configuration overrides', COLOR_INFO, interaction)
        if not all_kv:
            embed.description = 'no overrides set — all sections using global yaml defaults.'
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        for section in SECTION_MODELS:
            kv = all_kv.get(section)
            if not kv:
                embed.add_field(name=section, value='*(using global defaults)*', inline=False)
                continue
            lines = []
            for k, v in kv.items():
                if isinstance(v, bool):
                    lines.append(f"{('✅' if v else '❌')} `{k}`")
                elif isinstance(v, list):
                    lines.append(f"`{k}`: [{', '.join((str(x) for x in v[:3]))}{('…' if len(v) > 3 else '')}]")
                else:
                    lines.append(f'`{k}`: `{v}`')
            field_val = '\n'.join(lines)
            if len(field_val) > 200:
                field_val = field_val[:197] + '…'
            embed.add_field(name=section, value=field_val, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @configure_group.command(name='reset', description='reset one section (or all) to yaml defaults')
    @app_commands.autocomplete(section=_section_autocomplete)
    async def configure_reset(self, interaction: discord.Interaction, section: str | None=None) -> None:
        if not await _check(interaction):
            return
        target = section or 'all sections'
        view = ConfirmView()
        embed = _base_embed('confirm reset', COLOR_RESET, interaction)
        embed.description = f'this will reset **{target}** to global yaml defaults for this guild.\nall db overrides for the selected section(s) will be deleted.\n\nuse the buttons below to confirm or cancel.'
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        timed_out = not await view.wait()
        if not view.confirmed:
            cancel_embed = _base_embed('reset cancelled', COLOR_INFO, interaction)
            await interaction.edit_original_response(embed=cancel_embed, view=None)
            return
        guild_id = interaction.guild_id or 0
        try:
            if section:
                deleted = await self.store.reset_section(guild_id, section)
            else:
                deleted = await self.store.reset_all(guild_id)
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            err_embed = _base_embed('error during reset', COLOR_ERROR, interaction)
            err_embed.description = str(exc)
            await interaction.edit_original_response(embed=err_embed, view=None)
            return
        done_embed = _base_embed('reset complete', COLOR_SUCCESS, interaction)
        done_embed.description = f'reset **{target}** for this guild. {deleted} override row(s) removed.'
        await interaction.edit_original_response(embed=done_embed, view=None)
        log.info('configure.reset', guild_id=guild_id, section=section or 'all', deleted=deleted, updated_by=interaction.user.id)

    @configure_group.command(name='save-to-file', description='(owner only) write all db overrides back to config/config.yml')
    async def configure_save_to_file(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('this command is restricted to the bot owner.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            try:
                from ruamel.yaml import YAML
                yaml_lib = 'ruamel'
            except ImportError:
                import yaml as _yaml
                yaml_lib = 'pyyaml'
            config_path = 'config/config.yml'
            if yaml_lib == 'ruamel':
                from ruamel.yaml import YAML as RuamelYAML
                ry = RuamelYAML()
                ry.preserve_quotes = True
                with open(config_path, 'r', encoding='utf-8') as f:
                    doc = ry.load(f)
                if doc is None:
                    doc = {}
            else:
                import yaml as _yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    doc = _yaml.safe_load(f) or {}
            if 'guilds' not in doc:
                doc['guilds'] = {}
            guilds_written: list[str] = []
            for guild in self.bot.guilds:
                gid = guild.id
                all_kv = await self.store.get_all(gid)
                if not all_kv:
                    continue
                gid_str = str(gid)
                if gid_str not in doc['guilds']:
                    doc['guilds'][gid_str] = {}
                for section, kv in all_kv.items():
                    if section not in doc['guilds'][gid_str]:
                        doc['guilds'][gid_str][section] = {}
                    for k, v in kv.items():
                        doc['guilds'][gid_str][section][k] = v
                guilds_written.append(f'{guild.name} (`{gid}`)')
            tmp_path = config_path + '.tmp'
            if yaml_lib == 'ruamel':
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    ry.dump(doc, f)
            else:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    _yaml.dump(doc, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, config_path)
        except Exception as exc:
            embed = _base_embed('save failed', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.followup.send(embed=embed, ephemeral=True)
            log.error('configure.save_to_file.failed', error=str(exc), exc_info=exc)
            return
        embed = _base_embed('config saved to file', COLOR_SUCCESS, interaction)
        if guilds_written:
            embed.add_field(name='guilds written', value='\n'.join(guilds_written), inline=False)
        else:
            embed.description = 'no db overrides found — config.yml unchanged.'
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info('configure.save_to_file.success', guilds=guilds_written, updated_by=interaction.user.id)

    @configure_group.command(name='import-from-file', description="(owner only) import this guild's yaml block into the db override store")
    async def configure_import_from_file(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.bot.config.owner_id:
            await interaction.response.send_message('this command is restricted to the bot owner.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id or 0
        try:
            import yaml as _yaml
            with open('config/config.yml', 'r', encoding='utf-8') as f:
                doc = _yaml.safe_load(f) or {}
            guilds_raw = doc.get('guilds', {}) or {}
            guild_block = guilds_raw.get(str(guild_id)) or {}
            imported: list[str] = []
            for section, kv in guild_block.items():
                if section not in SECTION_MODELS or not isinstance(kv, dict):
                    continue
                model_cls = SECTION_MODELS[section]
                defaults = model_cls().model_dump()
                for k, v in kv.items():
                    if v is None:
                        continue
                    if v == defaults.get(k):
                        continue
                    await self.store.set(guild_id, section, k, v, interaction.user.id)
                    imported.append(f'`{section}.{k}` = `{v}`')
            await apply_guild_db_overrides(self.bot, guild_id)
        except Exception as exc:
            embed = _base_embed('import failed', COLOR_ERROR, interaction)
            embed.description = str(exc)
            await interaction.followup.send(embed=embed, ephemeral=True)
            log.error('configure.import_from_file.failed', guild_id=guild_id, error=str(exc))
            return
        embed = _base_embed('import complete', COLOR_SUCCESS, interaction)
        if imported:
            lines = '\n'.join(imported)
            if len(lines) > 900:
                lines = lines[:897] + '…'
            embed.add_field(name='imported values', value=lines, inline=False)
        else:
            embed.description = 'no non-default values found in yaml block for this guild.'
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info('configure.import_from_file.success', guild_id=guild_id, count=len(imported), updated_by=interaction.user.id)