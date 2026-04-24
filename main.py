from __future__ import annotations
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from aiohttp import web
import structlog
from core.config import load_config
from core.logging_setup import configure_logging
from core.bot import KnowledgeBot, register_signal_handlers
log: structlog.BoundLogger = structlog.get_logger('main')

async def build_health_app(_b: KnowledgeBot) -> web.Application:
    _a = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({'status': 'ok', 'guilds': len(_b.guilds), 'latency_ms': round(_b.latency * 1000, 2), 'uptime_seconds': round(_b.uptime_seconds, 1), 'ready': _b.is_ready()})

    async def readyz(request: web.Request) -> web.Response:
        if _b.is_ready():
            return web.json_response({'status': 'ready'})
        return web.json_response({'status': 'not_ready'}, status=503)
    _a.router.add_get('/healthz', healthz)
    _a.router.add_get('/readyz', readyz)
    return _a

async def run_health_server(_b: KnowledgeBot, _c: str, _d: int) -> None:
    _a = await build_health_app(_b)
    _e = web.AppRunner(_a)
    await _e.setup()
    _f = web.TCPSite(_e, _c, _d)
    await _f.start()
    log.info('health.server.started', host=_c, port=_d)

def patch_bot_with_resolver(_a: KnowledgeBot) -> None:
    from core.permissions import PermissionResolver
    _a.resolver = PermissionResolver(_a)

async def _async_main() -> None:
    _b = load_config('config/config.yml')
    _c = _b.logging
    configure_logging(level=_c.level, json_logs=_c.json_logs, log_file=_c.log_file)
    log.info('nonon.starting', version='1.0.0')
    _a = KnowledgeBot(_b)
    patch_bot_with_resolver(_a)
    if _b.health.enabled:
        await run_health_server(_a, _b.health.host, _b.health.port)
    register_signal_handlers(_a)
    try:
        await _a.start(_b.bot_token)
    except Exception as exc:
        log.critical('bot.fatal_error', error=str(exc), exc_info=exc)
        sys.exit(1)

def main() -> None:
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass
if __name__ == '__main__':
    main()