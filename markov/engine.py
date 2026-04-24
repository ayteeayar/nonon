from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
import markovify
import structlog
log: structlog.BoundLogger = structlog.get_logger(__name__)

class GenerationError(Exception):

class ModelLoadError(Exception):

def build_model(_d: list[str], _c: int=2) -> markovify.Text:
    if not _d:
        raise ValueError('cannot build a model from an empty corpus')
    _a = '\n'.join(_d)
    _b = markovify.Text(_a, state_size=_c, well_formed=False)
    log.info('markov.model.built', message_count=len(_d), state_size=_c)
    return _b

def generate_sentence(_b: markovify.Text, _e: int=100, _a: int=80, _d: str | None=None) -> str:
    _c: str | None
    if _d:
        _c = _b.make_sentence_with_start(_d, tries=_e, strict=False)
    else:
        _c = _b.make_short_sentence(max_chars=500, tries=_e)
    if _c is None:
        raise GenerationError(f'could not generate a sentence after {_e} attempts' + (f" with seed '{_d}'" if _d else ''))
    return _c

def combine_models(_a: list[markovify.Text], _b: list[float] | None=None) -> markovify.Text:
    return markovify.combine(_a, _b)

def save_model(_b: markovify.Text, _c: Path) -> None:
    _c.parent.mkdir(parents=True, exist_ok=True)
    _d = _c.with_suffix('.tmp')
    _a = _b.to_json()
    _d.write_text(_a, encoding='utf-8')
    _d.rename(_c)
    log.info('markov.model.saved', path=str(_c))

def load_model(_c: Path) -> markovify.Text:
    if not _c.exists():
        raise ModelLoadError(f'model file not found: {_c}')
    try:
        _a = _c.read_text(encoding='utf-8')
        _b = markovify.Text.from_json(_a)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ModelLoadError(f'failed to deserialise model at {_c}: {exc}') from exc
    log.info('markov.model.loaded', path=str(_c))
    return _b