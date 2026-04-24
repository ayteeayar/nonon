from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Optional
WHEEL_ORDER = [0, 28, 9, 26, 30, 11, 7, 20, 32, 17, 5, 22, 34, 15, 3, 24, 36, 13, 1, '00', 27, 10, 25, 29, 12, 8, 19, 31, 18, 6, 21, 33, 16, 4, 23, 35, 14, 2]
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
BLACK_NUMBERS = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}
COLUMNS: dict[int, set[int]] = {1: {1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34}, 2: {2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35}, 3: {3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36}}
DOZENS: dict[int, set[int]] = {1: set(range(1, 13)), 2: set(range(13, 25)), 3: set(range(25, 37))}

@dataclass
class Bet:
    bet_type: str
    amount: int
    params: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        match self.bet_type:
            case 'straight':
                return f"straight {self.params['number']}"
            case 'split':
                return f"split {self.params['a']}-{self.params['b']}"
            case 'street':
                return f"street {self.params['row']}"
            case 'corner':
                return f"corner {self.params['numbers']}"
            case 'six_line':
                return f"six line {self.params['rows']}"
            case 'column':
                return f"column {self.params['column']}"
            case 'dozen':
                return f"dozen {self.params['dozen']}"
            case 'red':
                return 'red'
            case 'black':
                return 'black'
            case 'even':
                return 'even'
            case 'odd':
                return 'odd'
            case 'low':
                return '1-18'
            case 'high':
                return '19-36'
            case _:
                return self.bet_type

@dataclass
class SpinResult:
    number: int | str
    color: str
    bets: list[Bet]
    payouts: list[int]
    total_wagered: int
    total_returned: int

    @property
    def net(self) -> int:
        return self.total_returned - self.total_wagered

    @property
    def display_number(self) -> str:
        return str(self.number)

def spin(_c: list[Bet]) -> SpinResult:
    _h = random.choice(WHEEL_ORDER)
    if _h in (0, '00'):
        _d = 'green'
        _e: Optional[int] = None
    else:
        _e = int(_h)
        _d = 'red' if _e in RED_NUMBERS else 'black'
    _g: list[int] = []
    for _b in _c:
        _f = _resolve(_b, _h, _e, _d)
        _g.append(_f)
    return SpinResult(number=_h, color=_d, bets=_c, payouts=_g, total_wagered=sum((_a.amount for _a in _c)), total_returned=sum(_g))

def _resolve(_b: Bet, _i: int | str, _f: Optional[int], _d: str) -> int:
    _a = _b.amount
    match _b.bet_type:
        case 'straight':
            _l = _b.params['number']
            _m = str(_i) == str(_l)
            return _a + _a * 35 if _m else 0
        case 'split':
            _m = str(_i) in (str(_b.params['a']), str(_b.params['b']))
            return _a + _a * 17 if _m else 0
        case 'street':
            _j = _b.params['row']
            _g = {_j * 3 - 2, _j * 3 - 1, _j * 3}
            _m = _f is not None and _f in _g
            return _a + _a * 11 if _m else 0
        case 'corner':
            _g = set(_b.params['numbers'])
            _m = _f is not None and _f in _g
            return _a + _a * 8 if _m else 0
        case 'six_line':
            _k = _b.params['rows']
            _g = set()
            for _h in _k:
                _g |= {_h * 3 - 2, _h * 3 - 1, _h * 3}
            _m = _f is not None and _f in _g
            return _a + _a * 5 if _m else 0
        case 'column':
            _c = _b.params['column']
            _m = _f is not None and _f in COLUMNS[_c]
            return _a + _a * 2 if _m else 0
        case 'dozen':
            _e = _b.params['dozen']
            _m = _f is not None and _f in DOZENS[_e]
            return _a + _a * 2 if _m else 0
        case 'red':
            _m = _d == 'red'
            return _a * 2 if _m else 0
        case 'black':
            _m = _d == 'black'
            return _a * 2 if _m else 0
        case 'even':
            _m = _f is not None and _f % 2 == 0
            return _a * 2 if _m else 0
        case 'odd':
            _m = _f is not None and _f % 2 == 1
            return _a * 2 if _m else 0
        case 'low':
            _m = _f is not None and 1 <= _f <= 18
            return _a * 2 if _m else 0
        case 'high':
            _m = _f is not None and 19 <= _f <= 36
            return _a * 2 if _m else 0
        case _:
            return 0

def bet_straight(_a: int, _b: int | str) -> Bet:
    return Bet('straight', _a, {'number': _b})

def bet_red(_a: int) -> Bet:
    return Bet('red', _a)

def bet_black(_a: int) -> Bet:
    return Bet('black', _a)

def bet_even(_a: int) -> Bet:
    return Bet('even', _a)

def bet_odd(_a: int) -> Bet:
    return Bet('odd', _a)

def bet_low(_a: int) -> Bet:
    return Bet('low', _a)

def bet_high(_a: int) -> Bet:
    return Bet('high', _a)

def bet_column(_a: int, _b: int) -> Bet:
    return Bet('column', _a, {'column': _b})

def bet_dozen(_a: int, _b: int) -> Bet:
    return Bet('dozen', _a, {'dozen': _b})