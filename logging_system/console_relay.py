from __future__ import annotations
import datetime
import json
import logging
import queue
import re
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
_relay_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
_ANSI_RE = re.compile('\\x1b\\[[0-9;]*m')
_LEVELS: dict[str, tuple[str, callable]] = {'debug': ('·', lambda s: f'*{s}*'), 'info': ('»', lambda s: s), 'warning': ('!', lambda s: f'__**{s}**__'), 'warn': ('!', lambda s: f'__**{s}**__'), 'error': ('×', lambda s: f'**{s}**'), 'critical': ('‼', lambda s: f'**{s}**')}
_MSG_CHAR_LIMIT = 1950
_LOGGER_SHORT: dict[str, str] = {'core.bot': 'bot', 'core.config': 'config', 'core.vanish_cog': 'vanish', 'database.connection': 'db', 'discord.client': 'discord', 'discord.gateway': 'gateway', 'discord.http': 'http', 'discord.app_commands.tree': 'cmds', 'logging_system.event_logger': 'events', 'logging_system.console_relay': 'relay', 'moderation.commands': 'mod', 'moderation.automod': 'automod', 'moderation.infractions': 'infractions', 'analytics.tracker': 'analytics', 'analytics.presence_tracker': 'presence', 'scraping.scraper': 'scraper', 'sync.engine': 'sync', 'discord_layer.channel_manager': 'channels', 'discord_layer.permission_manager': 'perms', 'lookup.commands': 'lookup', 'media.reel': 'media', 'aiosqlite': 'sqlite'}
_DROP_FIELDS = frozenset(('timestamp', 'level', 'logger', 'version', 'event'))
_SILENT_LOGGERS = frozenset(('bot', 'events', 'sync', 'mod', 'automod', 'analytics', 'presence', 'scraper', 'relay', 'infractions', 'channels', 'perms', 'config', 'db', 'lookup', 'media', 'vanish'))

def _parse_structured(_a: str) -> dict | None:
    _a = _a.strip()
    if not _a.startswith('{'):
        return None
    try:
        return json.loads(_a)
    except Exception:
        pass
    try:
        import ast
        _b = ast.literal_eval(_a)
        if isinstance(_b, dict):
            return _b
    except Exception:
        pass
    return None

def _format_record(_j: logging.LogRecord) -> str:
    _n = datetime.datetime.fromtimestamp(_j.created).strftime('%H:%M:%S')
    _g = _j.levelname.lower()
    _m, _p = _LEVELS.get(_g, ('·', lambda s: s))
    _i = _ANSI_RE.sub('', _j.getMessage()).strip()
    _c = _parse_structured(_i)
    if _c is not None:
        _d = str(_c.get('event', _i))
        _h = str(_c.get('logger', _j.name))
        _k = _LOGGER_SHORT.get(_h, _h.split('.')[-1])
        _b: list[str] = []
        for _f, _o in _c.items():
            if _f in _DROP_FIELDS or _o in (None, '', {}, []):
                continue
            _l = str(_o)
            if len(_l) > 60:
                _l = _l[:57] + '…'
            if _f == 'latency_ms':
                _b.append(f'latency={_l}ms')
            elif _f in ('error', 'exc_info'):
                _b.append(f'err={_l.splitlines()[0][:50]}')
            else:
                _b.append(f'{_f}={_l}')
        _a = _d
        if _k not in _SILENT_LOGGERS:
            _a = f'{_k}  {_d}'
        if _b:
            _a = f"{_a}  {'  '.join(_b)}"
    else:
        _k = _LOGGER_SHORT.get(_j.name, _j.name.split('.')[-1])
        _a = f'{_k}  {_i}' if _k and _k not in _SILENT_LOGGERS else _i
    _e = _p(f'{_m}  {_a}')
    return f'`{_n}` {_e}'

class _DiscordRelayHandler(logging.Handler):

    def __init__(self, min_level: int=logging.INFO) -> None:
        super().__init__(level=min_level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _relay_queue.put_nowait(_format_record(record))
        except Exception:
            pass

class ConsoleRelay(commands.Cog):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._current_lines: list[str] = []
        self._current_message: discord.Message | None = None
        self._channel: discord.TextChannel | None = None
        cfg_log = self.bot.config.logging
        flush_secs: float = getattr(cfg_log, 'console_relay_flush_seconds', 3.0)
        self._flush_loop.change_interval(seconds=flush_secs)
        self._flush_loop.start()

    def cog_unload(self) -> None:
        self._flush_loop.cancel()

    async def _get_channel(self) -> discord.TextChannel | None:
        if self._channel is not None:
            try:
                _ = self._channel.guild
                return self._channel
            except AttributeError:
                self._channel = None
        ch_id: int | None = None
        for guild in self.bot.guilds:
            gid = getattr(guild, 'id', None)
            if gid is None:
                continue
            guild_cfg = self.bot.config.get_guild_discord(gid)
            cid = getattr(guild_cfg, 'console_channel_id', None)
            if cid:
                ch_id = cid
                break
        if not ch_id:
            ch_id = getattr(self.bot.config.discord, 'console_channel_id', None)
        if not ch_id:
            return None
        ch = self.bot.get_channel(ch_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(ch_id)
            except discord.HTTPException:
                return None
        if not isinstance(ch, discord.TextChannel):
            return None
        self._channel = ch
        log.info('console_relay.channel_resolved', channel=ch_id)
        return self._channel

    async def _ensure_readonly(self, channel: discord.TextChannel) -> None:
        try:
            ow = channel.overwrites_for(channel.guild.default_role)
            if ow.send_messages is not False:
                ow.send_messages = False
                ow.add_reactions = False
                await channel.set_permissions(channel.guild.default_role, overwrite=ow, reason='nonon console relay — read-only enforcement')
        except discord.HTTPException:
            pass

    def _build_body(self, lines: list[str]) -> str:
        return '\n'.join(lines)

    async def _post_or_edit(self, channel: discord.TextChannel, body: str) -> bool:
        try:
            if self._current_message is None:
                self._current_message = await channel.send(body)
            elif self._current_message.content != body:
                await self._current_message.edit(content=body)
            return True
        except discord.Forbidden:
            log.warning('console_relay.forbidden', channel=channel.id)
        except discord.HTTPException as exc:
            log.warning('console_relay.http_error', error=str(exc))
            self._current_message = None
        return False

    @tasks.loop(seconds=3.0)
    async def _flush_loop(self) -> None:
        if not self.bot.is_ready():
            return
        cfg_log = self.bot.config.logging
        if not getattr(cfg_log, 'console_relay_enabled', True):
            return
        new_lines: list[str] = []
        while True:
            try:
                new_lines.append(_relay_queue.get_nowait())
            except queue.Empty:
                break
        if not new_lines:
            return
        channel = await self._get_channel()
        if not channel:
            return
        if self._current_message is None and (not self._current_lines):
            await self._ensure_readonly(channel)
        max_lines: int = getattr(cfg_log, 'console_relay_max_lines', 30)
        for line in new_lines:
            candidate = self._current_lines + [line]
            candidate_body = self._build_body(candidate)
            overflow = len(candidate) > max_lines or len(candidate_body) > _MSG_CHAR_LIMIT
            if overflow and self._current_lines:
                await self._post_or_edit(channel, self._build_body(self._current_lines))
                self._current_message = None
                self._current_lines = [line]
            else:
                self._current_lines = candidate
        if self._current_lines:
            await self._post_or_edit(channel, self._build_body(self._current_lines))

    @_flush_loop.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

async def setup(_a: 'KnowledgeBot') -> None:
    _b = _a.config.logging
    _c: bool = getattr(_b, 'console_relay_enabled', False)
    if _c:
        _f: str = getattr(_b, 'console_relay_level', 'INFO').upper()
        _e = getattr(logging, _f, logging.INFO)
        _d = _DiscordRelayHandler(min_level=_e)
        logging.getLogger().addHandler(_d)
        log.info('console_relay.handler_registered', level=_f)
    else:
        log.info('console_relay.disabled')
    await _a.add_cog(ConsoleRelay(_a))