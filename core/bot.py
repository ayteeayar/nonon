from __future__ import annotations
import asyncio
import signal
import time
from typing import TYPE_CHECKING
import discord
import structlog
from discord.ext import commands
from core.config import NonobotConfig
if TYPE_CHECKING:
    from database.connection import DatabasePool
    from core.permissions import PermissionResolver
log: structlog.BoundLogger = structlog.get_logger(__name__)

class KnowledgeBot(commands.Bot):

    def __init__(self, config: NonobotConfig) -> None:
        intents = discord.Intents.all()
        super().__init__(command_prefix=config.discord.command_prefix, intents=intents, help_command=None, max_messages=10000, chunk_guilds_at_startup=True)
        self.config: NonobotConfig = config
        self.db: DatabasePool | None = None
        self.resolver: PermissionResolver | None = None
        self._ready_time: float | None = None
        self._shutdown_event = asyncio.Event()
        self._loaded_cogs: list[str] = []

    async def setup_hook(self) -> None:
        log.info('bot.setup_hook.start')
        from database.connection import DatabasePool
        self.db = DatabasePool(self.config.database)
        await self.db.initialise()
        log.info('bot.database.ready')
        from core.permissions import PermissionResolver
        self.resolver = PermissionResolver(self)
        log.info('bot.resolver.ready')
        await self._load_cogs()
        await self._sync_commands()
        log.info('bot.setup_hook.complete')

    async def _load_cogs(self) -> None:
        cog_modules = ['discord_layer.channel_manager', 'discord_layer.permission_manager', 'configure', 'moderation.commands', 'moderation.automod', 'moderation.captcha', 'logging_system.event_logger', 'analytics.tracker', 'analytics.presence_tracker', 'analytics.csv_import', 'scraping.scraper', 'sync.engine', 'logging_system.console_relay', 'lookup.commands', 'media.reel', 'media.song', 'core.vanish_cog', 'markov.cog', 'setup.cog', 'casino.cog']
        for module in cog_modules:
            try:
                await self.load_extension(module)
                self._loaded_cogs.append(module)
                log.info('cog.loaded', module=module)
            except Exception as exc:
                log.error('cog.load_failed', module=module, error=str(exc), exc_info=exc)

    async def _sync_commands(self) -> None:
        try:
            guild_ids: set[int] = set()
            if self.config.discord.guild_id:
                guild_ids.add(self.config.discord.guild_id)
            for gid_str in self.config.guilds:
                try:
                    guild_ids.add(int(gid_str))
                except ValueError:
                    pass
            if guild_ids:
                for gid in guild_ids:
                    g_obj = discord.Object(id=gid)
                    self.tree.copy_global_to(guild=g_obj)
                    synced = await self.tree.sync(guild=g_obj)
                    log.info('commands.synced', guild=gid, count=len(synced))
            else:
                synced = await self.tree.sync()
                log.info('commands.synced.global', count=len(synced))
        except Exception as exc:
            log.error('commands.sync_failed', error=str(exc), exc_info=exc)

    async def close(self) -> None:
        log.info('bot.shutdown.start')
        self._shutdown_event.set()
        for cog in list(self.cogs.values()):
            if hasattr(cog, 'cog_unload'):
                try:
                    await discord.utils.maybe_coroutine(cog.cog_unload)
                except Exception as exc:
                    log.warning('cog.unload_error', cog=type(cog).__name__, error=str(exc))
        if self.db:
            await self.db.close()
            log.info('bot.database.closed')
        await super().close()
        log.info('bot.shutdown.complete')

    async def on_ready(self) -> None:
        self._ready_time = time.monotonic()
        log.info('bot.ready', user=str(self.user), guilds=len(self.guilds), latency_ms=round(self.latency * 1000, 2))
        try:
            from configure.merger import apply_guild_db_overrides
            for guild in self.guilds:
                await apply_guild_db_overrides(self, guild.id)
            log.info('bot.config.overrides_applied', guild_count=len(self.guilds))
        except Exception as exc:
            log.error('bot.config.overrides_failed', error=str(exc), exc_info=exc)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=self.config.bot.status_text), status=discord.Status.online)

    async def on_connect(self) -> None:
        log.info('bot.connected')

    async def on_disconnect(self) -> None:
        log.warning('bot.disconnected')

    async def on_resumed(self) -> None:
        log.info('bot.session_resumed')

    async def on_error(self, event_method: str, *args: object, **kwargs: object) -> None:
        log.error('bot.event_error', event=event_method, exc_info=True)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info('bot.guild_joined', guild=guild.id, name=guild.name)
        try:
            from configure.merger import apply_guild_db_overrides
            await apply_guild_db_overrides(self, guild.id)
        except Exception as exc:
            log.warning('bot.config.override_join_failed', guild=guild.id, error=str(exc))
        try:
            g_obj = discord.Object(id=guild.id)
            self.tree.copy_global_to(guild=g_obj)
            await self.tree.sync(guild=g_obj)
        except Exception as exc:
            log.warning('commands.sync_new_guild_failed', guild=guild.id, error=str(exc))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info('bot.guild_left', guild=guild.id, name=guild.name)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("you don't have permission to use this command.")
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("i'm missing required permissions.")
            return
        log.error('command.error', command=ctx.command, error=str(error), exc_info=error)

    @property
    def uptime_seconds(self) -> float:
        if self._ready_time is None:
            return 0.0
        return time.monotonic() - self._ready_time

    def get_target_guild(self) -> discord.Guild | None:
        return self.get_guild(self.config.discord.guild_id)

    async def safe_send(self, channel: discord.abc.Messageable, content: str='', **kwargs: object) -> discord.Message | None:
        try:
            return await channel.send(content=content, **kwargs)
        except discord.Forbidden:
            log.warning('send.forbidden', channel=str(channel))
        except discord.HTTPException as exc:
            log.error('send.http_error', status=exc.status, error=str(exc))
        return None

    async def wait_until_shutdown(self) -> None:
        await self._shutdown_event.wait()

def register_signal_handlers(_a: KnowledgeBot) -> None:
    _b = asyncio.get_running_loop()

    def _handle(_c: int) -> None:
        log.info('signal.received', signal=_c)
        _b.create_task(_a.close())
    for _c in (signal.SIGTERM, signal.SIGINT):
        try:
            _b.add_signal_handler(_c, _handle, _c)
        except NotImplementedError:
            pass