from __future__ import annotations
import logging
import logging.handlers
import sys
from pathlib import Path
import structlog

def configure_logging(_e: str='INFO', _d: bool=True, _f: str='./logs/bot.log') -> None:
    Path(_f).parent.mkdir(parents=True, exist_ok=True)
    _j: list[structlog.types.Processor] = [structlog.contextvars.merge_contextvars, structlog.stdlib.add_logger_name, structlog.stdlib.add_log_level, structlog.stdlib.PositionalArgumentsFormatter(), structlog.processors.TimeStamper(fmt='iso'), structlog.processors.StackInfoRenderer()]
    if _d:
        _h: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        _h = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    structlog.configure(processors=[*_j, structlog.stdlib.ProcessorFormatter.wrap_for_formatter], logger_factory=structlog.stdlib.LoggerFactory(), wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(_e.upper())), cache_logger_on_first_use=True)
    _c = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=_j, processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, _h])
    _a = logging.StreamHandler(sys.stdout)
    _a.setFormatter(_c)
    _b = logging.handlers.TimedRotatingFileHandler(_f, when='midnight', backupCount=30, encoding='utf-8')
    _b.setFormatter(_c)
    _i = logging.getLogger()
    _i.handlers.clear()
    _i.addHandler(_a)
    _i.addHandler(_b)
    _i.setLevel(_e.upper())
    for _g in ('discord.http', 'discord.gateway', 'asyncio', 'aiohttp'):
        logging.getLogger(_g).setLevel(logging.WARNING)