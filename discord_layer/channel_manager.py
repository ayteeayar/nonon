from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
MAX_MSG_LEN = 1990

class ChannelManager(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot

    async def get_or_create_category(self, guild: discord.Guild, name: str) -> discord.CategoryChannel:
        prefixed = (self.bot.config.get_guild_discord(guild.id).sync_category_prefix + name)[:99]
        existing = discord.utils.find(lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() == prefixed.lower(), guild.channels)
        if existing:
            return existing
        cfg = self.bot.config.get_guild_discord(guild.id)
        if cfg.dry_run:
            log.info('dry_run.category.create', guild=guild.id, name=prefixed)
            return guild.categories[0] if guild.categories else None
        cat: discord.CategoryChannel = await guild.create_category(prefixed)
        log.info('category.created', guild=guild.id, name=prefixed, id=cat.id)
        return cat

    async def delete_category(self, category: discord.CategoryChannel) -> None:
        cfg = self.bot.config.get_guild_discord(category.guild.id)
        if cfg.dry_run:
            log.info('dry_run.category.delete', id=category.id, name=category.name)
            return
        try:
            await category.delete()
            log.info('category.deleted', id=category.id, name=category.name)
        except discord.HTTPException as exc:
            log.error('category.delete_failed', id=category.id, error=str(exc))

    async def get_or_create_channel(self, guild: discord.Guild, category: discord.CategoryChannel, name: str, topic: str='') -> discord.TextChannel:
        safe_name = self._sanitise_channel_name(name)
        existing = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and c.name == safe_name and (c.category_id == category.id), guild.channels)
        if existing:
            return existing
        cfg = self.bot.config.get_guild_discord(guild.id)
        if cfg.dry_run:
            log.info('dry_run.channel.create', guild=guild.id, name=safe_name)
            return guild.text_channels[0] if guild.text_channels else None
        ch = await guild.create_text_channel(safe_name, category=category, topic=topic[:1023])
        log.info('channel.created', guild=guild.id, name=safe_name, id=ch.id)
        return ch

    async def update_channel_content(self, channel: discord.TextChannel, content: str) -> None:
        cfg = self.bot.config.get_guild_discord(channel.guild.id)
        if cfg.dry_run:
            log.info('dry_run.channel.update', id=channel.id)
            return
        chunks = self._split_content(content)
        new_bodies = chunks
        try:
            history = [m async for m in channel.history(limit=50) if m.author == self.bot.user]
            history.reverse()
        except discord.HTTPException as exc:
            log.error('channel.history_failed', id=channel.id, error=str(exc))
            history = []
        for i, body in enumerate(new_bodies):
            if i < len(history):
                existing_msg = history[i]
                if existing_msg.content != body:
                    try:
                        await existing_msg.edit(content=body)
                        await asyncio.sleep(0.3)
                    except discord.HTTPException as exc:
                        log.error('channel.edit_failed', id=channel.id, error=str(exc))
            else:
                try:
                    msg = await channel.send(body)
                    history.append(msg)
                    await asyncio.sleep(0.5)
                except discord.HTTPException as exc:
                    log.error('channel.send_failed', id=channel.id, error=str(exc))
        surplus = history[len(new_bodies):]
        for msg in surplus:
            try:
                await msg.delete()
                await asyncio.sleep(0.3)
            except discord.HTTPException:
                pass
        if history and new_bodies:
            first = history[0]
            try:
                pins = await channel.pins()
                if first not in pins:
                    await first.pin()
            except discord.HTTPException:
                pass
        log.info('channel.content_updated', id=channel.id, chunks=len(new_bodies), edited=min(len(new_bodies), len(history)), sent=max(0, len(new_bodies) - len(history)), deleted=len(surplus))

    async def delete_channel(self, channel: discord.TextChannel, reason: str='') -> None:
        cfg = self.bot.config.get_guild_discord(channel.guild.id)
        if cfg.dry_run:
            log.info('dry_run.channel.delete', id=channel.id)
            return
        try:
            await channel.delete(reason=reason)
            log.info('channel.deleted', id=channel.id, name=channel.name)
        except discord.HTTPException as exc:
            log.error('channel.delete_failed', id=channel.id, error=str(exc))

    @staticmethod
    def _sanitise_channel_name(name: str) -> str:
        name = name.lower()
        if '.' in name:
            name = name.rsplit('.', 1)[0]
        safe = ''
        for ch in name:
            if ch.isalnum() or ch in '-_':
                safe += ch
            elif ch in ' /\\._':
                safe += '-'
        return safe.strip('-')[:99] or 'unnamed'

    @staticmethod
    def _split_content(content: str, max_len: int=MAX_MSG_LEN) -> list[str]:
        if len(content) <= max_len:
            return [content]
        chunks: list[str] = []
        while content:
            if len(content) <= max_len:
                chunks.append(content)
                break
            split_at = content.rfind('\n', 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(content[:split_at])
            content = content[split_at:].lstrip('\n')
        return chunks

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(ChannelManager(_a))