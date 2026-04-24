from __future__ import annotations
import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING
import discord
import structlog
from discord import app_commands
from discord.ext import commands
from core.permissions import owner_only
from setup.models import Preset, PresetCategory, PresetOverwrite, PresetRole, PresetTextChannel, PresetVoiceChannel
from setup.preset_loader import PresetLoader
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_VALID_NAME_RE = re.compile('^[a-zA-Z0-9_-]{1,64}$')
_NUMERIC_NAME_RE = re.compile('^\\d+$')

def _build_overwrites(_d: list[PresetOverwrite], _b: discord.Guild, _f: dict[str, discord.Role]) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    _e: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}
    for _g in _d:
        if _g.role == '@everyone':
            _h = _b.default_role
        elif _g.role in _f:
            _h = _f[_g.role]
        else:
            _a = discord.utils.get(_b.roles, name=_g.role)
            if _a:
                _h = _a
            else:
                log.warning('setup.overwrite.unknown_role', role=_g.role)
                continue
        _c = discord.PermissionOverwrite(**_g.to_discord_overwrite())
        _e[_h] = _c
    return _e

def _sanitise_filename(_b: str) -> str:
    _a = _b.lower().replace(' ', '-')
    return re.sub('[^a-z0-9_-]', '', _a)

async def _snapshot_guild(_e: discord.Guild) -> tuple[list[PresetRole], list[PresetCategory], dict[str, int]]:
    _g = set(PresetOverwrite.model_fields.keys()) - {'role'}
    _n: dict[str, int] = {'roles_skipped': 0, 'uncategorised_channels': 0, 'member_overwrites_skipped': 0}
    _m: list[PresetRole] = []
    for _k in reversed(_e.roles):
        if _k.is_default():
            continue
        if _k.managed:
            _n['roles_skipped'] += 1
            log.debug('setup.save.role_skipped_managed', name=_k.name)
            continue
        if _NUMERIC_NAME_RE.match(_k.name):
            _n['roles_skipped'] += 1
            log.debug('setup.save.role_skipped_numeric', name=_k.name)
            continue
        _m.append(PresetRole(name=_k.name, color=_k.color.value, hoist=_k.hoist, mentionable=_k.mentionable, permissions=_k.permissions.value))

    def _serialise_overwrites(overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite]) -> list[PresetOverwrite]:
        _j: list[PresetOverwrite] = []
        for _o, _h in overwrites.items():
            if not isinstance(_o, discord.Role):
                _n['member_overwrites_skipped'] += 1
                log.debug('setup.save.overwrite_member_skipped', target=str(_o))
                continue
            _l = '@everyone' if _o.is_default() else _o.name
            _i: dict[str, bool | None] = getattr(_h, '_values', {})
            _d = {_f: _q for _f, _q in _i.items() if _f in _g}
            if not _d:
                continue
            _j.append(PresetOverwrite(role=_l, **_d))
        return _j
    _b: list[PresetCategory] = []
    for _a in sorted(_e.categories, key=lambda c: c.position):
        _p: list[PresetTextChannel] = []
        _r: list[PresetVoiceChannel] = []
        for _c in sorted(_a.channels, key=lambda c: c.position):
            if isinstance(_c, discord.TextChannel):
                _p.append(PresetTextChannel(name=_c.name, topic=_c.topic or '', nsfw=_c.nsfw, slowmode_delay=_c.slowmode_delay, position=_c.position, overwrites=_serialise_overwrites(_c.overwrites)))
            elif isinstance(_c, discord.VoiceChannel):
                _r.append(PresetVoiceChannel(name=_c.name, bitrate=_c.bitrate, user_limit=_c.user_limit, video_quality_mode=getattr(_c.video_quality_mode, 'value', 1), position=_c.position, overwrites=_serialise_overwrites(_c.overwrites)))
            else:
                log.warning('setup.save.unsupported_channel_type', name=_c.name, type=type(_c).__name__)
        _b.append(PresetCategory(name=_a.name, position=_a.position, overwrites=_serialise_overwrites(_a.overwrites), text_channels=_p, voice_channels=_r))
    for _c in _e.channels:
        if _c.category is None and (not isinstance(_c, discord.CategoryChannel)):
            _n['uncategorised_channels'] += 1
            log.warning('setup.save.uncategorised_channel', name=_c.name)
    return (_m, _b, _n)

class SetupCog(commands.Cog, name='setup'):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        preset_dir = Path(bot.config.setup.preset_dir)
        self.loader = PresetLoader(preset_dir)
    setup_group = app_commands.Group(name='setup', description='server setup and scaffolding from presets')

    async def _preset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n, value=n) for n in self.loader.names() if current.lower() in n.lower()][:25]

    @setup_group.command(name='apply', description='apply a preset — creates roles, categories, and channels')
    @app_commands.describe(preset='name of the preset to apply')
    @app_commands.autocomplete(preset=_preset_autocomplete)
    @owner_only()
    async def apply(self, interaction: discord.Interaction, preset: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None
        loaded_preset = self.loader.get(preset)
        if not loaded_preset:
            await interaction.followup.send(f"preset '{discord.utils.escape_markdown(preset)}' not found. use /setup list to see available presets.", ephemeral=True)
            return
        created_roles: list[str] = []
        skipped_roles: list[str] = []
        created_categories: list[str] = []
        created_channels: list[str] = []
        skipped_channels: list[str] = []
        role_map: dict[str, discord.Role] = {}
        for role_spec in loaded_preset.roles:
            existing = discord.utils.get(guild.roles, name=role_spec.name)
            if existing:
                role_map[role_spec.name] = existing
                skipped_roles.append(role_spec.name)
                log.info('setup.role.exists', name=role_spec.name)
                continue
            try:
                new_role = await guild.create_role(name=role_spec.name, colour=discord.Colour(role_spec.color), hoist=role_spec.hoist, mentionable=role_spec.mentionable, permissions=discord.Permissions(role_spec.permissions), reason=f'setup preset: {preset}')
                role_map[role_spec.name] = new_role
                created_roles.append(role_spec.name)
                log.info('setup.role.created', name=role_spec.name)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning('setup.role.create_failed', name=role_spec.name, error=str(exc))
                skipped_roles.append(role_spec.name)
        channel_batch_count = 0
        for cat_spec in loaded_preset.categories:
            cat_overwrites = _build_overwrites(cat_spec.overwrites, guild, role_map)
            existing_cat = discord.utils.get(guild.categories, name=cat_spec.name)
            if existing_cat is None:
                try:
                    existing_cat = await guild.create_category(cat_spec.name, overwrites=cat_overwrites, reason=f'setup preset: {preset}')
                    created_categories.append(cat_spec.name)
                    log.info('setup.category.created', name=cat_spec.name)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning('setup.category.create_failed', name=cat_spec.name, error=str(exc))
                    continue
            else:
                created_categories.append(f'{cat_spec.name} (existing)')
            for tc_spec in cat_spec.text_channels:
                existing_tc = discord.utils.get(guild.text_channels, name=tc_spec.name, category=existing_cat)
                if existing_tc:
                    skipped_channels.append(f'#{tc_spec.name}')
                    continue
                tc_overwrites = _build_overwrites(tc_spec.overwrites, guild, role_map)
                try:
                    await existing_cat.create_text_channel(name=tc_spec.name, topic=tc_spec.topic or None, nsfw=tc_spec.nsfw, slowmode_delay=tc_spec.slowmode_delay, overwrites=tc_overwrites, reason=f'setup preset: {preset}')
                    created_channels.append(f'#{tc_spec.name}')
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning('setup.channel.create_failed', name=tc_spec.name, error=str(exc))
                    skipped_channels.append(f'#{tc_spec.name}')
                channel_batch_count += 1
                if channel_batch_count % 10 == 0:
                    await asyncio.sleep(0.5)
            for vc_spec in cat_spec.voice_channels:
                existing_vc = discord.utils.get(guild.voice_channels, name=vc_spec.name, category=existing_cat)
                if existing_vc:
                    skipped_channels.append(f'🔊{vc_spec.name}')
                    continue
                vc_overwrites = _build_overwrites(vc_spec.overwrites, guild, role_map)
                try:
                    await existing_cat.create_voice_channel(name=vc_spec.name, bitrate=vc_spec.bitrate, user_limit=vc_spec.user_limit, overwrites=vc_overwrites, reason=f'setup preset: {preset}')
                    created_channels.append(f'🔊{vc_spec.name}')
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning('setup.voice.create_failed', name=vc_spec.name, error=str(exc))
                    skipped_channels.append(f'🔊{vc_spec.name}')
                channel_batch_count += 1
                if channel_batch_count % 10 == 0:
                    await asyncio.sleep(0.5)
        lines: list[str] = [f'**preset applied: {discord.utils.escape_markdown(preset)}**\n']
        if created_roles:
            lines.append(f"roles created: {', '.join(created_roles)}")
        if skipped_roles:
            lines.append(f"roles skipped (already exist): {', '.join(skipped_roles)}")
        if created_categories:
            lines.append(f"categories: {', '.join(created_categories)}")
        if created_channels:
            lines.append(f"channels created: {', '.join(created_channels)}")
        if skipped_channels:
            lines.append(f"channels skipped: {', '.join(skipped_channels)}")
        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @setup_group.command(name='save', description='snapshot the current server structure and save it as a preset')
    @app_commands.describe(preset_name='name for the new preset (alphanumeric, hyphens, underscores — max 64 chars)', description='optional human-readable description stored in the preset file', overwrite='if true, replace an existing file with the same name')
    @owner_only()
    async def save(self, interaction: discord.Interaction, preset_name: str, description: str='', overwrite: bool=False) -> None:
        if not _VALID_NAME_RE.match(preset_name):
            await interaction.response.send_message('invalid preset name. use only letters, numbers, hyphens, and underscores (max 64 characters). spaces are not allowed.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None
        roles, categories, stats = await _snapshot_guild(guild)
        preset = Preset(name=preset_name, description=description, roles=roles, categories=categories)
        filename = _sanitise_filename(preset_name) + '.yml'
        try:
            written_path = self.loader.save(preset, filename, overwrite=overwrite)
        except FileExistsError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except (PermissionError, OSError) as exc:
            log.error('setup.save.write_failed', filename=filename, error=str(exc))
            await interaction.followup.send(f"failed to write preset file '{filename}': {exc}", ephemeral=True)
            return
        self.loader.reload()
        total_text = sum((len(cat.text_channels) for cat in categories))
        total_voice = sum((len(cat.voice_channels) for cat in categories))
        lines: list[str] = [f'**preset saved: {discord.utils.escape_markdown(preset_name)}**\n']
        lines.append(f'file written: {written_path.name}')
        lines.append(f"roles captured: {len(roles)} (skipped managed/bot roles: {stats['roles_skipped']})")
        lines.append(f'categories: {len(categories)}')
        lines.append(f'text channels: {total_text}')
        lines.append(f'voice channels: {total_voice}')
        if stats['uncategorised_channels']:
            lines.append(f"uncategorised channels skipped: {stats['uncategorised_channels']} (channels with no category are not captured)")
        if stats['member_overwrites_skipped']:
            lines.append(f"member-specific overwrites skipped: {stats['member_overwrites_skipped']}")
        lines.append('\nuse /setup apply to re-apply this preset to any server.')
        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @setup_group.command(name='list', description='list all available presets')
    @owner_only()
    async def list_presets(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        names = self.loader.names()
        if not names:
            await interaction.followup.send('no presets loaded.', ephemeral=True)
            return
        embed = discord.Embed(title='available presets', colour=discord.Colour.blurple())
        for name in names:
            p = self.loader.get(name)
            if p:
                embed.add_field(name=name, value=p.description or 'no description', inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @setup_group.command(name='reload', description='reload all preset files from disk without restarting')
    @owner_only()
    async def reload_presets(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        count = self.loader.reload()
        await interaction.followup.send(f'reloaded presets from disk. {count} preset(s) loaded.', ephemeral=True)

    @setup_group.command(name='preview', description='show what a preset would create without applying it')
    @app_commands.describe(preset='name of the preset to preview')
    @app_commands.autocomplete(preset=_preset_autocomplete)
    @owner_only()
    async def preview(self, interaction: discord.Interaction, preset: str) -> None:
        await interaction.response.defer(ephemeral=True)
        loaded_preset = self.loader.get(preset)
        if not loaded_preset:
            await interaction.followup.send(f"preset '{discord.utils.escape_markdown(preset)}' not found.", ephemeral=True)
            return
        lines: list[str] = [f'**preview: {discord.utils.escape_markdown(preset)}**', f'_{loaded_preset.description}_\n' if loaded_preset.description else '']
        if loaded_preset.roles:
            lines.append('roles: ' + ', '.join((r.name for r in loaded_preset.roles)))
        for cat in loaded_preset.categories:
            lines.append(f'\ncategory: **{discord.utils.escape_markdown(cat.name)}**')
            for tc in cat.text_channels:
                lines.append(f'  text: #{tc.name}')
            for vc in cat.voice_channels:
                lines.append(f"  voice: {vc.name} (limit: {vc.user_limit or 'unlimited'})")
        await interaction.followup.send('\n'.join(filter(None, lines)), ephemeral=True)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(SetupCog(_a))
    log.info('cog.setup.complete', cog='SetupCog')