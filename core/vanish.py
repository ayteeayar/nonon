from __future__ import annotations
_public_users: set[int] = set()

def is_vanished(_a: int) -> bool:
    return _a not in _public_users

def ephemeral_for(_a: int) -> bool:
    return _a not in _public_users

def toggle(_a: int) -> bool:
    if _a in _public_users:
        _public_users.discard(_a)
        return True
    else:
        _public_users.add(_a)
        return False

def set_vanished(_a: int) -> None:
    _public_users.discard(_a)

def set_public(_a: int) -> None:
    _public_users.add(_a)