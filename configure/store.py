from __future__ import annotations
import json
from typing import Any, TYPE_CHECKING
import structlog
if TYPE_CHECKING:
    from database.connection import DatabasePool
from core.config import DiscordConfig, LoggingConfig, ModerationConfig, AnalyticsConfig, MarkovConfig, CaptchaConfig, SourceConfig, MediaConfig, CasinoConfig
log: structlog.BoundLogger = structlog.get_logger(__name__)
SECTION_MODELS: dict[str, type] = {'discord': DiscordConfig, 'logging': LoggingConfig, 'moderation': ModerationConfig, 'analytics': AnalyticsConfig, 'markov': MarkovConfig, 'captcha': CaptchaConfig, 'source': SourceConfig, 'media': MediaConfig, 'casino': CasinoConfig}

class GuildConfigStore:

    def __init__(self, db: 'DatabasePool') -> None:
        self.db = db

    async def get(self, guild_id: int, section: str, key: str) -> Any | None:
        row = await self.db.fetch_val('SELECT value FROM guild_config WHERE guild_id=? AND section=? AND key=?', (guild_id, section, key))
        if row is None:
            return None
        return json.loads(row)

    async def get_section(self, guild_id: int, section: str) -> dict[str, Any]:
        rows = await self.db.fetch_all('SELECT key, value FROM guild_config WHERE guild_id=? AND section=?', (guild_id, section))
        return {row['key']: json.loads(row['value']) for row in rows}

    async def get_all(self, guild_id: int) -> dict[str, dict[str, Any]]:
        rows = await self.db.fetch_all('SELECT section, key, value FROM guild_config WHERE guild_id=?', (guild_id,))
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            s = row['section']
            if s not in result:
                result[s] = {}
            result[s][row['key']] = json.loads(row['value'])
        return result

    async def set(self, guild_id: int, section: str, key: str, value: Any, updated_by: int) -> None:
        model_cls = SECTION_MODELS.get(section)
        if model_cls is None:
            raise ValueError(f"unknown section {section!r}. valid sections: {', '.join(SECTION_MODELS)}")
        if key not in model_cls.model_fields:
            valid = ', '.join(model_cls.model_fields.keys())
            raise ValueError(f'unknown key {key!r} for section {section!r}. valid keys: {valid}')
        encoded = json.dumps(value)
        await self.db.execute("\n            INSERT OR REPLACE INTO guild_config\n                (guild_id, section, key, value, updated_by, updated_at)\n            VALUES (?, ?, ?, ?, ?, datetime('now'))\n            ", (guild_id, section, key, encoded, updated_by))
        log.debug('guild_config.set', guild_id=guild_id, section=section, key=key, updated_by=updated_by)

    async def delete(self, guild_id: int, section: str, key: str) -> bool:
        existing = await self.db.fetch_val('SELECT id FROM guild_config WHERE guild_id=? AND section=? AND key=?', (guild_id, section, key))
        if existing is None:
            return False
        await self.db.execute('DELETE FROM guild_config WHERE guild_id=? AND section=? AND key=?', (guild_id, section, key))
        return True

    async def reset_section(self, guild_id: int, section: str) -> int:
        rows = await self.db.fetch_all('SELECT id FROM guild_config WHERE guild_id=? AND section=?', (guild_id, section))
        count = len(rows)
        if count:
            await self.db.execute('DELETE FROM guild_config WHERE guild_id=? AND section=?', (guild_id, section))
        return count

    async def reset_all(self, guild_id: int) -> int:
        rows = await self.db.fetch_all('SELECT id FROM guild_config WHERE guild_id=?', (guild_id,))
        count = len(rows)
        if count:
            await self.db.execute('DELETE FROM guild_config WHERE guild_id=?', (guild_id,))
        return count