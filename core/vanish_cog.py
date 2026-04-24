from __future__ import annotations
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands
import structlog
import core.vanish as vanish_state
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

class VanishCog(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    @app_commands.command(name='vanish', description='Toggle whether your command responses are visible to the channel.')
    async def vanish(self, interaction: discord.Interaction) -> None:
        bot: KnowledgeBot = interaction.client
        if interaction.user.id != bot.config.owner_id:
            await interaction.response.send_message('❌ Only the bot owner can toggle vanish mode.', ephemeral=True)
            return
        now_vanished = vanish_state.toggle(interaction.user.id)
        if now_vanished:
            icon = '🫥'
            state_label = '**vanish ON** — your responses are now ephemeral again.'
        else:
            icon = '👁️'
            state_label = '**vanish OFF** — your responses will be posted publicly.'
        log.info('vanish.toggled', user=interaction.user.id, vanished=now_vanished, guild=interaction.guild_id)
        await interaction.response.send_message(f'{icon} {state_label}', ephemeral=True)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(VanishCog(_a))