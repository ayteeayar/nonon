from __future__ import annotations
import asyncio
import datetime
import json
import random
import re
import time
from typing import TYPE_CHECKING, Literal, Optional
import discord
import structlog
from discord import app_commands
from discord.ext import commands
from core.permissions import require_permission
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_WORDLIST: list[str] = ['apple', 'river', 'cloud', 'forest', 'stone', 'window', 'bottle', 'garden', 'shadow', 'candle', 'silver', 'falcon', 'bridge', 'copper', 'velvet', 'puzzle', 'marble', 'anchor', 'jungle', 'mirror', 'winter', 'lantern', 'coffee', 'pebble', 'violet', 'ladder', 'tunnel', 'basket', 'cradle', 'breeze', 'crystal', 'dancer', 'ember', 'feather', 'glimmer', 'harbour', 'island', 'jewel', 'kettle', 'lemon', 'meadow', 'needle', 'oyster', 'pillow', 'quartz', 'ribbon', 'salmon', 'timber', 'umbrella', 'vortex', 'walnut', 'xenon', 'yellow', 'zephyr', 'arrow', 'butter', 'cactus', 'desert', 'engine', 'ferret', 'goblin', 'hammer', 'insect', 'jacket', 'kitten', 'lizard', 'monkey', 'napkin', 'oracle', 'parrot', 'quiver', 'rocket', 'statue', 'tundra', 'urchin', 'vendor', 'wizard', 'yonder', 'zenith', 'bandit', 'carpet', 'donkey', 'falcon', 'gravel', 'helmet', 'ivory', 'jungle', 'kindle', 'locket', 'muffin', 'noodle', 'obelisk']

def _generate_phrase(_a: int) -> str:
    return ' '.join(random.choices(_WORDLIST, k=_a))
_COUNT_DIFFICULTY: dict[str, tuple[int, int]] = {'easy': (5, 15), 'medium': (20, 50), 'hard': (75, 150)}
_PHRASE_DIFFICULTY: dict[str, tuple[int, int]] = {'easy': (3, 5), 'medium': (7, 12), 'hard': (15, 25)}
_DIFFICULTY_LABEL: dict[str, str] = {'easy': 'easy', 'medium': 'medium', 'hard': 'hard 💀'}

class CaptchaCog(commands.Cog, name='captcha'):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._count_state: dict[int, int] = {}
        self._active_channels: dict[int, int] = {}
        self._phrase_start: dict[int, float] = {}

    async def cog_load(self) -> None:
        rows = await self.bot.db.fetch_all('SELECT id, channel_id, challenge_type FROM captcha_sessions WHERE released = 0', ())
        for row in rows:
            self._active_channels[row['channel_id']] = row['id']
            if row['challenge_type'] == 'count':
                self._count_state.setdefault(row['id'], 0)
        log.info('captcha.sessions.restored', count=len(rows))
    captcha_group = app_commands.Group(name='captcha', description='captcha mute mechanic')

    @captcha_group.command(name='lock', description='lock a user in a captcha channel until they complete a challenge')
    @app_commands.describe(user='the member to captcha', mode='challenge type (default: random)', difficulty='how hard the challenge is (default: medium)')
    @require_permission('captcha lock')
    async def lock(self, interaction: discord.Interaction, user: discord.Member, mode: Optional[Literal['random', 'count', 'phrase']]='random', difficulty: Optional[Literal['easy', 'medium', 'hard']]='medium') -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None
        cfg = self.bot.config.captcha
        existing = await self.bot.db.fetch_val('SELECT id FROM captcha_sessions WHERE guild_id = ? AND user_id = ? AND released = 0', (guild.id, user.id))
        if existing:
            await interaction.followup.send(f'{user.mention} already has an active captcha session.', ephemeral=True)
            return
        resolved_mode: str = mode
        if resolved_mode == 'random':
            resolved_mode = random.choice(['count', 'phrase'])
        if difficulty not in _COUNT_DIFFICULTY:
            difficulty = 'medium'
        captcha_role = await guild.create_role(name=f'captcha-{user.id}', permissions=discord.Permissions.none(), reason=f'captcha initiated by {interaction.user}')
        all_channels = list(guild.channels)
        for i in range(0, len(all_channels), 5):
            batch = all_channels[i:i + 5]
            for ch in batch:
                try:
                    await ch.set_permissions(captcha_role, view_channel=False, reason='captcha lockdown')
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning('captcha.overwrite.failed', channel=ch.id, error=str(exc))
            await asyncio.sleep(0.5)
        category: discord.CategoryChannel | None = discord.utils.get(guild.categories, name=cfg.category_name)
        if category is None:
            category = await guild.create_category(cfg.category_name, overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)})
        safe_name = re.sub('[^a-z0-9\\-]', '', user.name.lower()) or str(user.id)
        channel_name = f'captcha-{safe_name}'
        captcha_channel = await guild.create_text_channel(channel_name, category=category, overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False), captcha_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_roles=True)})
        await user.add_roles(captcha_role, reason='captcha lockdown')
        challenge_type: str
        challenge_data: dict
        diff_label = _DIFFICULTY_LABEL[difficulty]
        if resolved_mode == 'count':
            count_min, count_max = _COUNT_DIFFICULTY[difficulty]
            target = random.randint(count_min, count_max)
            challenge_type = 'count'
            challenge_data = {'target': target, 'difficulty': difficulty}
            challenge_text = f"{user.mention} you have been captcha'd.  [{diff_label}]\n\ncount from **1** to **{target}** by sending one number per message. if you send the wrong number your progress resets."
        else:
            phrase_min, phrase_max = _PHRASE_DIFFICULTY[difficulty]
            word_count = random.randint(phrase_min, phrase_max)
            phrase = _generate_phrase(word_count)
            challenge_type = 'phrase'
            challenge_data = {'phrase': phrase, 'difficulty': difficulty}
            challenge_text = f"{user.mention} you have been captcha'd.  [{diff_label}]\n\ntype the following phrase exactly (case-insensitive):\n\n**{discord.utils.escape_markdown(phrase)}**"
        await captcha_channel.send(challenge_text)
        session_id = await self.bot.db.execute_returning('\n            INSERT INTO captcha_sessions\n                (guild_id, user_id, moderator_id, channel_id, role_id,\n                 challenge_type, challenge_data)\n            VALUES (?, ?, ?, ?, ?, ?, ?)\n            ', (guild.id, user.id, interaction.user.id, captcha_channel.id, captcha_role.id, challenge_type, json.dumps(challenge_data)))
        self._active_channels[captcha_channel.id] = session_id
        if challenge_type == 'count':
            self._count_state[session_id] = 0
        log.info('captcha.session.started', guild=guild.id, user=user.id, session=session_id, challenge=challenge_type, difficulty=difficulty)
        await interaction.followup.send(f"{user.mention} has been captcha'd in {captcha_channel.mention}. mode: **{challenge_type}** — difficulty: **{diff_label}**", ephemeral=True)

    @captcha_group.command(name='release', description='manually release a user from captcha')
    @app_commands.describe(user='the member to release')
    @require_permission('captcha release')
    async def release(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None
        row = await self.bot.db.fetch_one('\n            SELECT id, channel_id, role_id\n            FROM captcha_sessions\n            WHERE guild_id = ? AND user_id = ? AND released = 0\n            ', (guild.id, user.id))
        if not row:
            await interaction.followup.send(f'{user.mention} has no active captcha session.', ephemeral=True)
            return
        await self._release_user(guild=guild, user=user, session_id=row['id'], channel_id=row['channel_id'], role_id=row['role_id'], reason='manually released by moderator')
        await interaction.followup.send(f'{user.mention} has been released from captcha.', ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id not in self._active_channels:
            return
        session_id = self._active_channels[message.channel.id]
        row = await self.bot.db.fetch_one('\n            SELECT id, guild_id, user_id, role_id, challenge_type, challenge_data\n            FROM captcha_sessions\n            WHERE id = ? AND released = 0\n            ', (session_id,))
        if not row:
            return
        if message.author.id != row['user_id']:
            return
        guild = message.guild
        assert guild is not None
        member = guild.get_member(row['user_id'])
        if member is None:
            return
        challenge_data = json.loads(row['challenge_data'])
        if row['challenge_type'] == 'count':
            await self._handle_count(message, row, challenge_data, guild, member)
        elif row['challenge_type'] == 'phrase':
            await self._handle_phrase(message, row, challenge_data, guild, member)

    @commands.Cog.listener()
    async def on_typing(self, channel: discord.abc.Messageable, user: discord.abc.User, when: datetime.datetime) -> None:
        if not hasattr(channel, 'id') or channel.id not in self._active_channels:
            return
        session_id = self._active_channels[channel.id]
        row = await self.bot.db.fetch_one('SELECT id, user_id, challenge_type FROM captcha_sessions WHERE id = ? AND released = 0', (session_id,))
        if not row or row['challenge_type'] != 'phrase':
            return
        if user.id != row['user_id']:
            return
        if session_id not in self._phrase_start:
            self._phrase_start[session_id] = time.monotonic()
            log.info('captcha.phrase.typing_started', session=session_id, user=user.id)

    async def _handle_count(self, message: discord.Message, row: dict, challenge_data: dict, guild: discord.Guild, member: discord.Member) -> None:
        session_id = row['id']
        target = challenge_data['target']
        current = self._count_state.get(session_id, 0)
        expected = current + 1
        try:
            submitted = int(message.content.strip())
        except ValueError:
            await message.reply(f"that's not a number. send {expected} to continue.")
            return
        if submitted == expected:
            self._count_state[session_id] = submitted
            if submitted == target:
                await message.reply(f'correct! releasing you now.')
                await asyncio.sleep(1)
                await self._release_user(guild=guild, user=member, session_id=session_id, channel_id=message.channel.id, role_id=row['role_id'], reason='captcha completed')
            else:
                await message.add_reaction('✅')
        else:
            self._count_state[session_id] = 0
            await message.reply(f'wrong number. progress reset. start again from 1.')

    async def _handle_phrase(self, message: discord.Message, row: dict, challenge_data: dict, guild: discord.Guild, member: discord.Member) -> None:
        session_id = row['id']
        target_phrase: str = challenge_data['phrase']
        if message.content.strip().lower() == target_phrase.lower():
            start = self._phrase_start.get(session_id)
            if start is None:
                wpm_note = ' (speed unavailable — no typing event detected)'
            else:
                elapsed = time.monotonic() - start
                words = len(target_phrase.split())
                minutes = elapsed / 60
                wpm = round(words / minutes) if minutes > 0 else 0
                wpm_note = f' your typing speed: {wpm} wpm.'
            await message.reply(f'correct!{wpm_note} releasing you now.')
            await asyncio.sleep(1)
            await self._release_user(guild=guild, user=member, session_id=session_id, channel_id=message.channel.id, role_id=row['role_id'], reason='captcha completed')
        else:
            await message.reply('incorrect. try again.')

    async def _release_user(self, guild: discord.Guild, user: discord.Member, session_id: int, channel_id: int, role_id: int, reason: str) -> None:
        captcha_role = guild.get_role(role_id)
        captcha_channel = guild.get_channel(channel_id)
        if captcha_role and captcha_role in user.roles:
            try:
                await user.remove_roles(captcha_role, reason=reason)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning('captcha.release.remove_role_failed', user=user.id, error=str(exc))
        if captcha_role:
            channels_to_clean = list(guild.channels)
            for i in range(0, len(channels_to_clean), 10):
                batch = channels_to_clean[i:i + 10]
                for ch in batch:
                    if captcha_role in ch.overwrites:
                        try:
                            await ch.set_permissions(captcha_role, overwrite=None)
                        except (discord.Forbidden, discord.HTTPException) as exc:
                            log.warning('captcha.release.overwrite_cleanup_failed', channel=ch.id, error=str(exc))
                await asyncio.sleep(0.5)
            try:
                await captcha_role.delete(reason=reason)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning('captcha.release.delete_role_failed', role=role_id, error=str(exc))
        if captcha_channel and isinstance(captcha_channel, discord.TextChannel):
            try:
                await captcha_channel.send('captcha complete. this channel will be deleted in 3 seconds.')
                await asyncio.sleep(3)
                await captcha_channel.delete(reason=reason)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning('captcha.release.channel_delete_failed', channel=channel_id, error=str(exc))
        await self.bot.db.execute("\n            UPDATE captcha_sessions\n            SET released = 1, completed_at = datetime('now')\n            WHERE id = ?\n            ", (session_id,))
        self._active_channels.pop(channel_id, None)
        self._count_state.pop(session_id, None)
        self._phrase_start.pop(session_id, None)
        log.info('captcha.session.released', session=session_id, user=user.id, reason=reason)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(CaptchaCog(_a))
    log.info('cog.setup.complete', cog='CaptchaCog')