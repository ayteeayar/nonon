from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING
import discord
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)

class WebhookManager:

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._cache: dict[tuple[int, str], discord.Webhook] = {}

    async def get_or_create_webhook(self, channel: discord.TextChannel, persona_name: str, avatar_url: str | None) -> discord.Webhook:
        cache_key = (channel.id, persona_name)
        if cache_key in self._cache:
            return self._cache[cache_key]
        row = await self.bot.db.fetch_one('\n            SELECT webhook_id, webhook_token\n            FROM markov_webhooks\n            WHERE guild_id = ? AND channel_id = ? AND persona_name = ?\n            ', (channel.guild.id, channel.id, persona_name))
        if row:
            webhook = discord.Webhook.partial(id=row['webhook_id'], token=row['webhook_token'], session=self.bot.http._HTTPClient__session)
            self._cache[cache_key] = webhook
            return webhook
        webhook = await self._create_and_store(channel, persona_name, avatar_url)
        self._cache[cache_key] = webhook
        return webhook

    async def send_as_persona(self, channel: discord.TextChannel, persona_name: str, avatar_url: str | None, content: str) -> None:
        webhook = await self.get_or_create_webhook(channel, persona_name, avatar_url)
        cache_key = (channel.id, persona_name)
        try:
            await webhook.send(content=content, username=persona_name, avatar_url=avatar_url)
        except discord.NotFound:
            log.warning('markov.webhook.not_found', channel=channel.id, persona=persona_name)
            self._cache.pop(cache_key, None)
            webhook = await self._create_and_store(channel, persona_name, avatar_url)
            self._cache[cache_key] = webhook
            await webhook.send(content=content, username=persona_name, avatar_url=avatar_url)

    async def load_personas_from_file(self, path: Path) -> int:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, list):
            raise ValueError('persona file must contain a JSON array')
        count = 0
        for item in raw:
            name = item.get('name', '').strip()
            avatar_url = item.get('avatar_url') or None
            if not name:
                log.warning('markov.persona.skip_empty_name')
                continue
            await self.bot.db.execute("\n                INSERT INTO markov_webhooks\n                    (guild_id, channel_id, webhook_id, webhook_token, persona_name, avatar_url)\n                VALUES (0, 0, 0, '', ?, ?)\n                ON CONFLICT(guild_id, channel_id, persona_name) DO UPDATE\n                    SET avatar_url = excluded.avatar_url\n                ", (name, avatar_url))
            count += 1
        log.info('markov.personas.loaded', count=count, path=str(path))
        return count

    async def _create_and_store(self, channel: discord.TextChannel, persona_name: str, avatar_url: str | None) -> discord.Webhook:
        webhook = await channel.create_webhook(name=persona_name)
        await self.bot.db.execute('\n            INSERT INTO markov_webhooks\n                (guild_id, channel_id, webhook_id, webhook_token, persona_name, avatar_url)\n            VALUES (?, ?, ?, ?, ?, ?)\n            ON CONFLICT(guild_id, channel_id, persona_name) DO UPDATE\n                SET webhook_id    = excluded.webhook_id,\n                    webhook_token = excluded.webhook_token,\n                    avatar_url    = excluded.avatar_url\n            ', (channel.guild.id, channel.id, webhook.id, webhook.token, persona_name, avatar_url))
        log.info('markov.webhook.created', guild=channel.guild.id, channel=channel.id, persona=persona_name)
        return webhook