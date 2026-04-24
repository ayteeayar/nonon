from __future__ import annotations
import functools
from typing import TYPE_CHECKING, Callable, Any
import discord
import structlog
from discord.ext import commands
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
SCOPE_COMMANDS: dict[str, list[str]] = {'moderation': ['ban', 'kick', 'mute', 'warn', 'softban', 'lock', 'unlock', 'purge', 'slowmode', 'role', 'nickname', 'captcha lock', 'captcha release'], 'infractions': ['infractions'], 'userinfo': ['userhistory', 'userstats', 'usernames', 'useravatars'], 'analytics': ['stats', 'exportstats', 'analyticsdebug'], 'scraping': ['scrape', 'scrapestatus', 'scrapecancel'], 'sync': ['sync', 'syncstatus', 'syncperms'], 'media': ['reel', 'frame', 'song'], 'lookup': ['lookup', 'lookup profile', 'lookup messages', 'lookup infractions', 'lookup voice', 'lookup presence', 'lookup mutual', 'lookup import'], 'markov': ['markov train', 'markov generate', 'markov list', 'markov delete', 'markov persona add', 'markov persona list', 'markov persona loadfile'], 'setup': ['setup apply', 'setup list', 'setup reload', 'setup preview'], 'casino': ['chips', 'blackjack', 'roulette', 'slots', 'leaderboard', 'gamestats'], 'configuration': ['configure', 'configure channels log', 'configure channels audit', 'configure channels mod-log', 'configure channels archive', 'configure channels voice-log', 'configure channels status', 'configure channels console', 'configure channels forward-to', 'configure channels show', 'configure logging toggle', 'configure logging set', 'configure logging show', 'configure moderation set', 'configure moderation toggle', 'configure moderation banned-words', 'configure moderation banned-patterns', 'configure moderation link-whitelist', 'configure moderation mod-roles', 'configure moderation admin-roles', 'configure moderation show', 'configure analytics set', 'configure analytics show', 'configure markov set', 'configure markov show', 'configure captcha set', 'configure captcha show', 'configure source set', 'configure source show', 'configure media set', 'configure media show', 'configure show', 'configure reset']}
SCOPE_COMMANDS['admin'] = SCOPE_COMMANDS['moderation'] + SCOPE_COMMANDS['infractions'] + SCOPE_COMMANDS['userinfo'] + SCOPE_COMMANDS['analytics'] + SCOPE_COMMANDS['scraping'] + SCOPE_COMMANDS['sync'] + SCOPE_COMMANDS['media'] + SCOPE_COMMANDS['lookup'] + SCOPE_COMMANDS['markov'] + SCOPE_COMMANDS['setup'] + SCOPE_COMMANDS['configuration'] + ['permit', 'revoke', 'grants', 'guildconfig']
SCOPE_COMMANDS['all'] = list({cmd for cmds in SCOPE_COMMANDS.values() for cmd in cmds})
OWNER_ONLY_COMMANDS: frozenset[str] = frozenset({'permit', 'revoke', 'guildlist', 'guildleave', 'globalban', 'shutdown', 'reload', 'configreload', 'massban', 'markov delete', 'markov persona loadfile', 'setup apply', 'setup reload', 'configure save-to-file', 'configure import-from-file'})

class PermissionResolver:

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._cache: dict[int, list[dict]] = {}

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    async def _load_grants(self, guild_id: int) -> list[dict]:
        if guild_id in self._cache:
            return self._cache[guild_id]
        rows = await self.bot.db.fetch_all('SELECT * FROM permission_grants WHERE guild_id = ?', (guild_id,))
        grants = [dict(r) for r in rows]
        self._cache[guild_id] = grants
        return grants

    async def can_use(self, user: discord.Member | discord.User, command_name: str, guild_id: int) -> bool:
        owner_id: int = self.bot.config.owner_id
        if user.id == owner_id:
            return True
        if command_name in OWNER_ONLY_COMMANDS:
            return False
        grants = await self._load_grants(guild_id)
        for grant in grants:
            if grant['target_type'] == 'user' and grant['target_id'] == user.id:
                if self._grant_covers(grant, command_name):
                    return True
        if isinstance(user, discord.Member):
            member_role_ids = {r.id for r in user.roles}
            for grant in grants:
                if grant['target_type'] == 'role' and grant['target_id'] in member_role_ids:
                    if self._grant_covers(grant, command_name):
                        return True
        return False

    @staticmethod
    def _grant_covers(grant: dict, command_name: str) -> bool:
        if grant['grant_type'] == 'command':
            return grant['grant_value'] == command_name
        if grant['grant_type'] == 'scope':
            scope = grant['grant_value']
            if scope == 'all':
                return True
            return command_name in SCOPE_COMMANDS.get(scope, [])
        return False

    async def add_grant(self, guild_id: int, target_type: str, target_id: int, grant_type: str, grant_value: str, granted_by: int) -> None:
        await self.bot.db.execute('\n            INSERT OR IGNORE INTO permission_grants\n                (guild_id, target_type, target_id, grant_type, grant_value, granted_by)\n            VALUES (?, ?, ?, ?, ?, ?)\n            ', (guild_id, target_type, target_id, grant_type, grant_value, granted_by))
        self.invalidate(guild_id)
        log.info('grant.added', guild=guild_id, target_type=target_type, target_id=target_id, grant_type=grant_type, value=grant_value)

    async def remove_grant(self, guild_id: int, target_type: str, target_id: int, grant_type: str, grant_value: str) -> bool:
        result = await self.bot.db.fetch_val('\n            SELECT id FROM permission_grants\n            WHERE guild_id=? AND target_type=? AND target_id=?\n              AND grant_type=? AND grant_value=?\n            ', (guild_id, target_type, target_id, grant_type, grant_value))
        if result is None:
            return False
        await self.bot.db.execute('\n            DELETE FROM permission_grants\n            WHERE guild_id=? AND target_type=? AND target_id=?\n              AND grant_type=? AND grant_value=?\n            ', (guild_id, target_type, target_id, grant_type, grant_value))
        self.invalidate(guild_id)
        return True

    async def list_grants(self, guild_id: int) -> list[dict]:
        return await self._load_grants(guild_id)

    async def user_grants(self, guild_id: int, user_id: int) -> list[dict]:
        grants = await self._load_grants(guild_id)
        return [g for g in grants if g['target_type'] == 'user' and g['target_id'] == user_id]

def require_permission(_c: str) -> Callable:

    async def predicate(interaction: discord.Interaction) -> bool:
        _b: KnowledgeBot = interaction.client
        _e: PermissionResolver = _b.resolver
        _d = interaction.guild_id or 0
        _a = await _e.can_use(interaction.user, _c, _d)
        if not _a:
            await interaction.response.send_message("you don't have permission to use this command.", ephemeral=True)
            return False
        return True
    return discord.app_commands.check(predicate)

def owner_only() -> Callable:

    async def predicate(interaction: discord.Interaction) -> bool:
        _a: KnowledgeBot = interaction.client
        if interaction.user.id != _a.config.owner_id:
            await interaction.response.send_message('this command is restricted to the bot owner.', ephemeral=True)
            return False
        return True
    return discord.app_commands.check(predicate)