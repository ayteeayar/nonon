from __future__ import annotations
import json
from typing import Any, Union, get_args, get_origin

def coerce_value(_b: type, _h: str) -> Any:
    _f = get_origin(_b)
    if _f is Union:
        args = [_a for _a in get_args(_b) if _a is not type(None)]
        _c = args[0] if args else str
        if _h.strip().lower() in ('', 'null', 'none'):
            return None
        return coerce_value(_c, _h)
    try:
        import types as _types
        if isinstance(_b, _types.UnionType):
            args = [_a for _a in get_args(_b) if _a is not type(None)]
            _c = args[0] if args else str
            if _h.strip().lower() in ('', 'null', 'none'):
                return None
            return coerce_value(_c, _h)
    except AttributeError:
        pass
    if _f is list or _b is list:
        _h = _h.strip()
        if _h.startswith('['):
            try:
                _g = json.loads(_h)
                if not isinstance(_g, list):
                    raise ValueError('expected a json array')
                return [str(_i) for _i in _g]
            except json.JSONDecodeError as exc:
                raise ValueError(f'invalid json array: {exc}') from exc
        return [_d.strip() for _d in _h.split(',') if _d.strip()]
    if _b is bool:
        _e = _h.strip().lower()
        if _e in ('true', '1', 'yes', 'on', 'y'):
            return True
        if _e in ('false', '0', 'no', 'off', 'n'):
            return False
        raise ValueError(f'cannot convert {_h!r} to bool. use: true/false, yes/no, 1/0, on/off')
    if _b is int:
        try:
            return int(_h)
        except (ValueError, TypeError):
            raise ValueError(f'cannot convert {_h!r} to int')
    if _b is float:
        try:
            return float(_h)
        except (ValueError, TypeError):
            raise ValueError(f'cannot convert {_h!r} to float')
    if _b is str:
        return _h
    try:
        return _b(_h)
    except Exception as exc:
        raise ValueError(f"cannot convert {_h!r} to {getattr(_b, '__name__', str(_b))}: {exc}") from exc