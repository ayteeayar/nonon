from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Optional
SYMBOLS = ['7️⃣', '3️⃣', '✌️', '1️⃣', '🔔', '🍒', '🍋', '🍊', '🍑', '🍇']
SYM_NAMES = ['SEVEN', 'BAR3', 'BAR2', 'BAR1', 'BELL', 'CHERRY', 'LEMON', 'ORANGE', 'PLUM', 'GRAPE']
SYM_EMOJI = {name: emoji for name, emoji in zip(SYM_NAMES, SYMBOLS)}
REEL_WEIGHTS: list[list[int]] = [[1, 4, 7, 14, 10, 8, 20, 17, 12, 7], [1, 3, 6, 13, 9, 8, 21, 18, 13, 8], [1, 3, 6, 13, 9, 8, 21, 18, 13, 8], [1, 3, 6, 13, 9, 8, 21, 18, 13, 8], [1, 3, 6, 13, 9, 8, 21, 18, 13, 8]]
PAYTABLE: dict[str, list[int]] = {'SEVEN': [0, 0, 750, 3000, 15000], 'BAR3': [0, 0, 300, 1200, 5000], 'BAR2': [0, 0, 150, 500, 2000], 'BAR1': [0, 0, 75, 250, 900], 'BELL': [0, 0, 40, 140, 500], 'CHERRY': [0, 0, 100, 300, 750], 'LEMON': [0, 0, 20, 65, 175], 'ORANGE': [0, 0, 20, 65, 175], 'PLUM': [0, 0, 15, 45, 125], 'GRAPE': [0, 0, 15, 45, 125]}
PAYLINES: list[list[int]] = [[0, 0, 0, 0, 0], [1, 1, 1, 1, 1], [2, 2, 2, 2, 2], [0, 1, 2, 1, 0], [2, 1, 0, 1, 2], [0, 0, 1, 0, 0], [2, 2, 1, 2, 2], [0, 1, 1, 1, 2], [2, 1, 1, 1, 0]]
FREE_SPINS_AWARD = 10
FREE_SPINS_TRIGGER_SYMBOL = 'BELL'
FREE_SPINS_TRIGGER_COUNT = 4

def _spin_reel(_b: int) -> list[str]:
    _c = REEL_WEIGHTS[_b]
    return [random.choices(SYM_NAMES, weights=_c)[0] for _a in range(3)]

def spin_all_reels() -> list[list[str]]:
    return [_spin_reel(_a) for _a in range(5)]

@dataclass
class LineResult:
    line_index: int
    symbols: list[str]
    match_symbol: Optional[str]
    match_count: int
    multiplier: int
    payout: int
    is_jackpot: bool = False

@dataclass
class SpinResult:
    grid: list[list[str]]
    line_bet: int
    total_bet: int
    line_results: list[LineResult]
    total_payout: int
    jackpot_contribution: int
    free_spins_awarded: int
    jackpot_hit: bool
    jackpot_amount: int

    @property
    def net(self) -> int:
        return self.total_payout - self.total_bet

    @property
    def grid_display(self) -> list[list[str]]:
        return [[SYM_EMOJI.get(self.grid[reel][row], '?') for reel in range(5)] for row in range(3)]

def evaluate_spin(_h: list[list[str]], _o: int, _a: int, _k: int, _j: bool=False, _m: float=0.01, _g: int=10) -> SpinResult:
    _y = 0 if _j else _o * _a
    _p: list[LineResult] = []
    _z = 0
    _n = False
    _b = 0
    for _i in range(_a):
        _s = PAYLINES[_i]
        _x = [_h[_u][_v] for _u, _v in enumerate(_s)]
        _e = _x[0]
        _d = 1
        for _w in _x[1:]:
            if _w == _e:
                _d += 1
            else:
                break
        if _e == 'SEVEN' and _d == 5:
            _n = True
            _b = _k
            _q = LineResult(line_index=_i, symbols=_x, match_symbol='SEVEN', match_count=5, multiplier=0, payout=_k, is_jackpot=True)
        else:
            if _e in PAYTABLE:
                _r = PAYTABLE[_e][_d - 1]
                _t = _r * _o
            else:
                _r = 0
                _t = 0
            _q = LineResult(line_index=_i, symbols=_x, match_symbol=_e if _t > 0 else None, match_count=_d if _t > 0 else 0, multiplier=_r, payout=_t)
        _p.append(_q)
        _z += _q.payout
    if _j:
        _f = 0
    else:
        _c = sum((1 for _u in range(5) for _v in range(3) if _h[_u][_v] == FREE_SPINS_TRIGGER_SYMBOL))
        _f = _g if _c >= FREE_SPINS_TRIGGER_COUNT else 0
    if _n or _j:
        _l = 0
    else:
        _l = max(1, int(_y * _m))
    return SpinResult(grid=_h, line_bet=_o, total_bet=_y, line_results=_p, total_payout=_z, jackpot_contribution=_l, free_spins_awarded=_f, jackpot_hit=_n, jackpot_amount=_b)