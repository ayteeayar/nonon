from __future__ import annotations
from typing import TYPE_CHECKING
import structlog
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
from core.config import GuildOverride
from configure.store import GuildConfigStore, SECTION_MODELS
log: structlog.BoundLogger = structlog.get_logger(__name__)

async def apply_guild_db_overrides(_b: 'KnowledgeBot', _d: int) -> None:
    _i = GuildConfigStore(_b.db)
    try:
        _a = await _i.get_all(_d)
    except Exception as exc:
        log.error('config.merge.load_failed', guild_id=_d, error=str(exc), exc_info=exc)
        return
    if not _a:
        return
    _g = _b.config.guilds.get(str(_d))
    if _g is None:
        _g = GuildOverride()
    for _h, _e in _a.items():
        _f = SECTION_MODELS.get(_h)
        if _f is None:
            log.warning('config.merge.unknown_section', guild_id=_d, section=_h)
            continue
        _c = getattr(_g, _h, None)
        if _c is None:
            _c = _f()
        try:
            _j = _c.model_copy(update=_e)
        except Exception as exc:
            log.error('config.merge.section_failed', guild_id=_d, section=_h, kv=_e, error=str(exc), exc_info=exc)
            continue
        _g = _g.model_copy(update={_h: _j})
    _b.config.guilds[str(_d)] = _g
    log.debug('config.merge.applied', guild_id=_d, sections=list(_a.keys()))