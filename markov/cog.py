from __future__ import annotations
import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING
import discord
import structlog
from discord import app_commands
from discord.ext import commands
from core.permissions import require_permission, owner_only
from markov import engine as markov_engine
from markov.engine import GenerationError, ModelLoadError
from markov.webhook_manager import WebhookManager
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_MODEL_NAME_RE = re.compile('^[a-zA-Z0-9\\-]{1,40}$')
_PING_RE = re.compile('@(everyone|here)\\b', re.IGNORECASE)

def _valid_model_name(_a: str) -> bool:
    return bool(_MODEL_NAME_RE.match(_a))

def _sanitise_output(_a: str) -> str:
    return _PING_RE.sub('\\1', _a)

class MarkovCog(commands.Cog, name='markov'):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self.webhook_manager = WebhookManager(bot)
    markov_group = app_commands.Group(name='markov', description='markov chain text generation')
    persona_group = app_commands.Group(name='persona', description='manage markov personas', parent=markov_group)

    def _resolve_model_ref(self, model_ref: str, invoking_guild_id: int, is_owner: bool) -> tuple[int, str]:
        if is_owner and '/' in model_ref:
            parts = model_ref.split('/', 1)
            try:
                return (int(parts[0]), parts[1])
            except (ValueError, IndexError):
                raise ValueError(f'malformed model reference: {model_ref!r}')
        return (invoking_guild_id, model_ref)

    async def _model_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        is_owner = interaction.user.id == self.bot.config.owner_id
        if is_owner:
            rows = await self.bot.db.fetch_all('SELECT name, guild_id FROM markov_models ORDER BY guild_id, name', ())
            choices = []
            for row in rows:
                if current.lower() not in row['name'].lower():
                    continue
                guild_label = 'this guild' if row['guild_id'] == interaction.guild_id else str(row['guild_id'])
                choices.append(app_commands.Choice(name=f"{row['name']}  [{guild_label}]"[:100], value=f"{row['guild_id']}/{row['name']}"))
            return choices[:25]
        rows = await self.bot.db.fetch_all('SELECT name FROM markov_models WHERE guild_id = ? ORDER BY name', (interaction.guild_id,))
        return [app_commands.Choice(name=row['name'], value=row['name']) for row in rows if current.lower() in row['name'].lower()][:25]

    async def _persona_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        rows = await self.bot.db.fetch_all('\n            SELECT DISTINCT persona_name\n            FROM markov_webhooks\n            WHERE guild_id = ?\n            ORDER BY persona_name\n            ', (interaction.guild_id,))
        return [app_commands.Choice(name=row['persona_name'], value=row['persona_name']) for row in rows if current.lower() in row['persona_name'].lower()][:25]

    @markov_group.command(name='train', description='train a new markov model from message history')
    @app_commands.describe(name='model name — alphanumeric and hyphens only, max 40 characters', source_type='filter messages by channel, user, or whole guild', source_id='channel id, user id, or guild id to train on', state_size='chain order: 1 = more random, 3 = more coherent (default 2)')
    @app_commands.choices(source_type=[app_commands.Choice(name='channel', value='channel'), app_commands.Choice(name='user', value='user'), app_commands.Choice(name='guild', value='guild')], state_size=[app_commands.Choice(name='1', value=1), app_commands.Choice(name='2', value=2), app_commands.Choice(name='3', value=3)])
    @require_permission('markov train')
    async def train(self, interaction: discord.Interaction, name: str, source_type: str, source_id: str, state_size: int=2) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _valid_model_name(name):
            await interaction.followup.send('model name must be alphanumeric + hyphens only, max 40 characters.', ephemeral=True)
            return
        guild_id = interaction.guild_id
        count_row = await self.bot.db.fetch_val('SELECT COUNT(*) FROM markov_models WHERE guild_id = ?', (guild_id,))
        max_models = self.bot.config.markov.max_models_per_guild
        if (count_row or 0) >= max_models:
            await interaction.followup.send(f'this guild has reached the maximum of {max_models} models. delete one with /markov delete before training a new one.', ephemeral=True)
            return
        try:
            sid = int(source_id)
        except ValueError:
            await interaction.followup.send('source_id must be a valid discord snowflake (integer).', ephemeral=True)
            return
        base_filter = "content != '' AND is_bot = 0 AND content NOT LIKE '/%' AND content NOT LIKE '!%' AND content NOT LIKE 'http%'"
        if source_type == 'channel':
            query = f'SELECT content FROM messages WHERE guild_id = ? AND channel_id = ? AND {base_filter}'
            params = (guild_id, sid)
        elif source_type == 'user':
            query = f'SELECT content FROM messages WHERE guild_id = ? AND author_id = ? AND {base_filter}'
            params = (guild_id, sid)
        else:
            query = f'SELECT content FROM messages WHERE guild_id = ? AND {base_filter}'
            params = (guild_id,)
        rows = await self.bot.db.fetch_all(query, params)
        texts = [row['content'] for row in rows if row['content'].strip()]
        min_msgs = self.bot.config.markov.min_training_messages
        if len(texts) < min_msgs:
            await interaction.followup.send(f'not enough messages to train a model. found {len(texts)}, need at least {min_msgs}.', ephemeral=True)
            return
        try:
            model = markov_engine.build_model(texts, state_size=state_size)
        except Exception as exc:
            log.error('markov.train.build_failed', error=str(exc), exc_info=exc)
            await interaction.followup.send('failed to build model — check the logs for details.', ephemeral=True)
            return
        model_dir = Path(self.bot.config.markov.model_dir) / str(guild_id)
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / f'{name}.json'
        try:
            markov_engine.save_model(model, model_path)
        except Exception as exc:
            log.error('markov.train.save_failed', path=str(model_path), exc_info=exc)
            await interaction.followup.send('failed to save model to disk.', ephemeral=True)
            return
        trained_on_json = json.dumps({'type': source_type, 'ids': [sid]})
        await self.bot.db.execute("\n            INSERT INTO markov_models\n                (name, guild_id, state_size, trained_on, message_count, model_path,\n                 created_at, updated_at)\n            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))\n            ON CONFLICT(name, guild_id) DO UPDATE\n                SET state_size    = excluded.state_size,\n                    trained_on    = excluded.trained_on,\n                    message_count = excluded.message_count,\n                    model_path    = excluded.model_path,\n                    updated_at    = datetime('now')\n            ", (name, guild_id, state_size, trained_on_json, len(texts), str(model_path)))
        embed = discord.Embed(title='markov model trained', colour=discord.Colour.green())
        embed.add_field(name='name', value=name, inline=True)
        embed.add_field(name='messages used', value=str(len(texts)), inline=True)
        embed.add_field(name='state size', value=str(state_size), inline=True)
        embed.add_field(name='source', value=f'{source_type} / {sid}', inline=True)
        log.info('markov.model.trained', guild=guild_id, name=name, messages=len(texts), state_size=state_size)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @markov_group.command(name='generate', description='generate text from a trained model')
    @app_commands.describe(model='name of the model to generate from', channel='channel to send output to (defaults to current channel)', persona='persona name for webhook dispatch (optional)', seed='starting word or phrase for the generated sentence (optional)', count='number of sentences to generate (1-5, default 1)')
    @app_commands.autocomplete(model=_model_autocomplete, persona=_persona_autocomplete)
    @require_permission('markov generate')
    async def generate(self, interaction: discord.Interaction, model: str, channel: discord.TextChannel | None=None, persona: str | None=None, seed: str | None=None, count: int=1) -> None:
        await interaction.response.defer(ephemeral=True)
        max_count = self.bot.config.markov.max_generate_count
        count = max(1, min(count, max_count))
        target_channel: discord.TextChannel = channel or interaction.channel
        is_owner = interaction.user.id == self.bot.config.owner_id
        try:
            target_guild_id, model_name = self._resolve_model_ref(model, interaction.guild_id, is_owner)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if is_owner and target_guild_id != interaction.guild_id:
            log.info('markov.generate.cross_guild', invoking_guild=interaction.guild_id, target_guild=target_guild_id, model=model_name, user=interaction.user.id)
        row = await self.bot.db.fetch_one('SELECT model_path FROM markov_models WHERE name = ? AND guild_id = ?', (model_name, target_guild_id))
        if not row:
            await interaction.followup.send(f"no model named '{discord.utils.escape_markdown(model_name)}' found for this guild.", ephemeral=True)
            return
        model_path = Path(row['model_path'])
        try:
            markov_model = markov_engine.load_model(model_path)
        except ModelLoadError as exc:
            log.error('markov.generate.load_failed', model=model_name, error=str(exc))
            await interaction.followup.send(f"could not load model '{discord.utils.escape_markdown(model_name)}': {exc}", ephemeral=True)
            return
        avatar_url: str | None = None
        if persona:
            persona_row = await self.bot.db.fetch_one('\n                SELECT avatar_url FROM markov_webhooks\n                WHERE guild_id = ? AND persona_name = ?\n                LIMIT 1\n                ', (interaction.guild_id, persona))
            if persona_row:
                avatar_url = persona_row['avatar_url']
        sentences: list[str] = []
        for _ in range(count):
            try:
                raw = markov_engine.generate_sentence(markov_model, seed=seed)
                processed = _sanitise_output(raw.capitalize())
                sentences.append(processed)
            except GenerationError as exc:
                log.warning('markov.generate.failed', model=model_name, error=str(exc))
                await interaction.followup.send(f'generation failed: {exc}', ephemeral=True)
                return
        combined = '\n'.join(sentences)
        if persona and isinstance(target_channel, discord.TextChannel):
            try:
                await self.webhook_manager.send_as_persona(target_channel, persona, avatar_url, combined)
            except Exception as exc:
                log.error('markov.generate.webhook_failed', error=str(exc), exc_info=exc)
                await interaction.followup.send('failed to send via webhook — check bot permissions.', ephemeral=True)
                return
        else:
            await target_channel.send(combined)
        await interaction.followup.send(f'sent {count} sentence(s) to {target_channel.mention}.', ephemeral=True)

    @markov_group.command(name='list', description='list trained models')
    @app_commands.describe(guild_id='(owner only) show models from this guild id instead of the current guild')
    @require_permission('markov list')
    async def list_models(self, interaction: discord.Interaction, guild_id: str | None=None) -> None:
        await interaction.response.defer(ephemeral=True)
        is_owner = interaction.user.id == self.bot.config.owner_id
        if guild_id is not None and (not is_owner):
            await interaction.followup.send('the guild_id parameter is restricted to the bot owner.', ephemeral=True)
            return
        if guild_id is not None:
            try:
                target_guild_id = int(guild_id)
            except ValueError:
                await interaction.followup.send('guild_id must be a valid integer snowflake.', ephemeral=True)
                return
            rows = await self.bot.db.fetch_all('\n                SELECT name, state_size, message_count, trained_on, created_at\n                FROM markov_models\n                WHERE guild_id = ?\n                ORDER BY name\n                ', (target_guild_id,))
            if not rows:
                await interaction.followup.send(f'no models found for guild {target_guild_id}.', ephemeral=True)
                return
            embed = discord.Embed(title=f'markov models — guild {target_guild_id}', colour=discord.Colour.blurple())
            for row in rows:
                trained_meta = json.loads(row['trained_on'])
                value = f"state size: {row['state_size']}\nmessages: {row['message_count']}\nsource: {trained_meta.get('type', '?')}\ncreated: {row['created_at'][:10]}"
                embed.add_field(name=row['name'], value=value, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if is_owner:
            rows = await self.bot.db.fetch_all('\n                SELECT name, guild_id, state_size, message_count, trained_on, created_at\n                FROM markov_models\n                ORDER BY guild_id, name\n                ', ())
            if not rows:
                await interaction.followup.send('no models found across any guild.', ephemeral=True)
                return
            embed = discord.Embed(title='markov models — all guilds', colour=discord.Colour.blurple())
            for row in rows:
                trained_meta = json.loads(row['trained_on'])
                guild_label = 'this guild' if row['guild_id'] == interaction.guild_id else str(row['guild_id'])
                value = f"guild: {guild_label}\nstate size: {row['state_size']}\nmessages: {row['message_count']}\nsource: {trained_meta.get('type', '?')}\ncreated: {row['created_at'][:10]}"
                embed.add_field(name=row['name'], value=value, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        rows = await self.bot.db.fetch_all('\n            SELECT name, state_size, message_count, trained_on, created_at\n            FROM markov_models\n            WHERE guild_id = ?\n            ORDER BY name\n            ', (interaction.guild_id,))
        if not rows:
            await interaction.followup.send('no models trained for this guild yet.', ephemeral=True)
            return
        embed = discord.Embed(title='markov models', colour=discord.Colour.blurple())
        for row in rows:
            trained_meta = json.loads(row['trained_on'])
            value = f"state size: {row['state_size']}\nmessages: {row['message_count']}\nsource: {trained_meta.get('type', '?')}\ncreated: {row['created_at'][:10]}"
            embed.add_field(name=row['name'], value=value, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @markov_group.command(name='delete', description='delete a trained model')
    @app_commands.describe(model='name of the model to delete')
    @app_commands.autocomplete(model=_model_autocomplete)
    @owner_only()
    async def delete_model(self, interaction: discord.Interaction, model: str) -> None:
        await interaction.response.defer(ephemeral=True)
        target_guild_id, model_name = self._resolve_model_ref(model, interaction.guild_id, is_owner=True)
        row = await self.bot.db.fetch_one('SELECT model_path FROM markov_models WHERE name = ? AND guild_id = ?', (model_name, target_guild_id))
        if not row:
            await interaction.followup.send(f"no model named '{discord.utils.escape_markdown(model_name)}' found.", ephemeral=True)
            return
        model_path = Path(row['model_path'])
        if model_path.exists():
            model_path.unlink()
            log.info('markov.model.deleted_file', path=str(model_path))
        await self.bot.db.execute('DELETE FROM markov_models WHERE name = ? AND guild_id = ?', (model_name, target_guild_id))
        log.info('markov.model.deleted', guild=target_guild_id, invoking_guild=interaction.guild_id, model=model_name, user=interaction.user.id)
        await interaction.followup.send(f"model '{discord.utils.escape_markdown(model_name)}' deleted.", ephemeral=True)

    @persona_group.command(name='add', description='register a named persona for webhook dispatch')
    @app_commands.describe(name='display name for the persona', avatar_url='optional url to an avatar image')
    @require_permission('markov persona add')
    async def persona_add(self, interaction: discord.Interaction, name: str, avatar_url: str | None=None) -> None:
        await interaction.response.defer(ephemeral=True)
        safe_name = discord.utils.escape_markdown(name)
        await self.bot.db.execute("\n            INSERT INTO markov_webhooks\n                (guild_id, channel_id, webhook_id, webhook_token, persona_name, avatar_url)\n            VALUES (?, 0, 0, '', ?, ?)\n            ON CONFLICT(guild_id, channel_id, persona_name) DO UPDATE\n                SET avatar_url = excluded.avatar_url\n            ", (interaction.guild_id, name, avatar_url))
        await interaction.followup.send(f"persona '{safe_name}' registered.", ephemeral=True)

    @persona_group.command(name='list', description='list all registered personas for this guild')
    @require_permission('markov persona list')
    async def persona_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.fetch_all('\n            SELECT DISTINCT persona_name, avatar_url\n            FROM markov_webhooks\n            WHERE guild_id = ?\n            ORDER BY persona_name\n            ', (interaction.guild_id,))
        if not rows:
            await interaction.followup.send('no personas registered for this guild.', ephemeral=True)
            return
        embed = discord.Embed(title='markov personas', colour=discord.Colour.blurple())
        for row in rows:
            embed.add_field(name=row['persona_name'], value=row['avatar_url'] or 'no avatar set', inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @persona_group.command(name='loadfile', description='load personas from a json attachment')
    @app_commands.describe(attachment='a json file containing an array of {name, avatar_url} objects')
    @owner_only()
    async def persona_loadfile(self, interaction: discord.Interaction, attachment: discord.Attachment) -> None:
        await interaction.response.defer(ephemeral=True)
        if not attachment.filename.endswith('.json'):
            await interaction.followup.send('attachment must be a .json file.', ephemeral=True)
            return
        try:
            raw_bytes = await attachment.read()
        except discord.HTTPException as exc:
            log.error('markov.persona.loadfile.download_failed', error=str(exc), exc_info=exc)
            await interaction.followup.send('failed to download attachment.', ephemeral=True)
            return
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = Path(tmp.name)
        try:
            count = await self.webhook_manager.load_personas_from_file(tmp_path)
        except (ValueError, json.JSONDecodeError) as exc:
            await interaction.followup.send(f'failed to parse persona file: {exc}', ephemeral=True)
            return
        finally:
            tmp_path.unlink(missing_ok=True)
        await interaction.followup.send(f'loaded {count} persona(s) from file.', ephemeral=True)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(MarkovCog(_a))
    log.info('cog.setup.complete', cog='MarkovCog')