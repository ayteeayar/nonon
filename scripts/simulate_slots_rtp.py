import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from casino.games.slots import spin_all_reels, evaluate_spin
NUM_SPINS = 1000000
LINE_BET = 100
ACTIVE_LINES = 9
JACKPOT_SEED = 10000
total_wagered = 0
total_returned = 0
hits = 0
scatter_triggers = 0
jackpot_hits = 0
jackpot_pool = JACKPOT_SEED
for _ in range(NUM_SPINS):
    grid = spin_all_reels()
    result = evaluate_spin(grid, LINE_BET, ACTIVE_LINES, jackpot_pool, is_free_spin=False)
    bet = LINE_BET * ACTIVE_LINES
    total_wagered += bet
    total_returned += result.total_payout
    if result.jackpot_hit:
        jackpot_hits += 1
        jackpot_pool = JACKPOT_SEED
    else:
        jackpot_pool += result.jackpot_contribution
    if result.total_payout > 0:
        hits += 1
    if result.free_spins_awarded > 0:
        scatter_triggers += 1
rtp = total_returned / total_wagered * 100
hit_freq = hits / NUM_SPINS * 100
scatter_rate = scatter_triggers / NUM_SPINS * 100
jackpot_rate = jackpot_hits / NUM_SPINS * 100
print(f'Simulated {NUM_SPINS:,} spins  ({ACTIVE_LINES} lines, {LINE_BET} bet/line)')
print(f'  RTP              : {rtp:.2f}%')
print(f'  Hit frequency    : {hit_freq:.2f}%')
print(f'  Scatter trigger  : {scatter_rate:.3f}%  (1 in {1 / scatter_rate * 100:.0f} spins)' if scatter_rate else '  Scatter trigger  : 0.000%')
print(f"  Jackpot hit rate : {jackpot_rate:.5f}%  (1 in {(int(1 / jackpot_rate * 100) if jackpot_rate else '∞')} spins)")