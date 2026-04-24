from __future__ import annotations
import asyncio
import os
from typing import TYPE_CHECKING, Optional
import discord
from discord import app_commands
from discord.ext import commands
import structlog
from core.vanish import ephemeral_for
from casino import chips as ledger
from casino.games.blackjack import BlackjackGame, GamePhase, HandStatus, hand_value, is_blackjack
from casino.games.roulette import Bet as RouletteBet, spin as roulette_spin, bet_red, bet_black, bet_even, bet_odd, bet_low, bet_high, bet_column, bet_dozen, bet_straight
from casino.games.slots import spin_all_reels, evaluate_spin, SYM_EMOJI, SpinResult
if TYPE_CHECKING:
    from core.bot import KnowledgeBot
log: structlog.BoundLogger = structlog.get_logger(__name__)
MIN_BET = 10
MAX_BET = 50000
VIEW_TIMEOUT = 120
CHIPS_EMOJI = '🪙'
COLOR_DEFAULT = 2829617
COLOR_WIN = 5763719
COLOR_LOSS = 15548997
COLOR_PUSH = 16705372
COLOR_JACKPOT = 16766720
COLOR_INFO = 5793266

def _fmt(_a: int) -> str:
    return f'{_a:,}'

def _chips(_a: int) -> str:
    return f'{CHIPS_EMOJI} {_fmt(_a)}'

def _net_color(_a: int) -> int:
    if _a > 0:
        return COLOR_WIN
    if _a < 0:
        return COLOR_LOSS
    return COLOR_PUSH

def _can_dismiss(_c: discord.Interaction, _d: int, _b: 'CasinoCog') -> bool:
    _g = _c.user
    if _g.id == _d:
        return True
    if _g.id == _b._owner_id():
        return True
    if isinstance(_g, discord.Member) and _c.guild:
        _a = _b.bot.config.get_guild_moderation(_c.guild.id)
        _f = set(_a.mod_roles) | set(_a.admin_roles)
        if any((_e.name in _f for _e in _g.roles)):
            return True
    return False

class DismissButton(discord.ui.Button):

    def __init__(self, invoker_id: int, session_key: tuple, cog: 'CasinoCog', row: int=4):
        super().__init__(label='✕', style=discord.ButtonStyle.secondary, row=row)
        self.invoker_id = invoker_id
        self.session_key = session_key
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _can_dismiss(interaction, self.invoker_id, self.cog):
            await interaction.response.send_message('only the player, server staff, or the bot owner can dismiss this.', ephemeral=True)
            return
        self.cog._active_bj.discard(self.session_key)
        self.cog._active_roulette.discard(self.session_key)
        self.cog._active_slots.discard(self.session_key)
        self.view.stop()
        try:
            await interaction.message.delete()
        except (discord.Forbidden, discord.NotFound):
            await interaction.response.send_message("couldn't delete that message.", ephemeral=True)

class _ClearView(discord.ui.View):

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.stop()

def _bj_embed(_g: BlackjackGame, _f: bool=False) -> discord.Embed:
    _s = _g.state
    _i = not _f and _s.phase != GamePhase.COMPLETE
    if _f or _s.phase == GamePhase.COMPLETE:
        _d = _s.dealer.value
        _c = f'{_s.dealer.display(hide_hole=False)}  —  **{_d}**'
        if _s.dealer.is_bj:
            _c += '  *(blackjack)*'
        elif _d > 21:
            _c += '  *(bust)*'
    else:
        _c = f'{_s.dealer.display(hide_hole=True)}  —  **{_s.dealer.cards[0].value}+?**'
    _m = [f'**dealer**\n{_c}\n']
    for _k, _h in enumerate(_s.hands):
        _a = ' ◀' if _k == _s.active_hand_index and _s.phase == GamePhase.PLAYER_TURN else ''
        _j = _h.value
        _l = f'**hand {_k + 1}**{_a}' if len(_s.hands) > 1 else f'**your hand**{_a}'
        _t = ''
        if _h.status == HandStatus.BUST:
            _t = '  *(bust)*'
        elif _h.status == HandStatus.BLACKJACK or (_h.is_bj and (not _f)):
            _t = '  *(blackjack!)*'
        elif _h.status == HandStatus.DOUBLED:
            _t = '  *(doubled)*'
        _r = ' soft' if _h.soft and _j <= 21 else ''
        _m.append(f'{_l}\n{_h.display()}  —  **{_j}**{_r}{_t}')
        _m.append(f'bet: {_chips(_h.bet)}')
        if _h.insurance_bet:
            _m.append(f'insurance: {_chips(_h.insurance_bet)}')
        _m.append('')
    if _s.phase == GamePhase.COMPLETE:
        _q = []
        _o = 0
        for _p in _s.outcomes:
            _q.append(_p.label)
            _o += _p.net
        if _q:
            _m.append('**result**\n' + '\n'.join(_q))
            _n = f'+{_fmt(_o)}' if _o >= 0 else _fmt(_o)
            _m.append(f'\nnet: {CHIPS_EMOJI} {_n}')
        _b = _net_color(_o)
    else:
        _b = COLOR_DEFAULT
    _e = discord.Embed(description='\n'.join(_m), color=_b)
    _e.set_footer(text=f'blackjack  ·  shoe: {_s.shoe_remaining} cards remaining')
    return _e

class InsuranceView(discord.ui.View):

    def __init__(self, game: BlackjackGame, eph: bool, session_key: tuple, cog: 'CasinoCog', invoker_id: int=0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self.eph = eph
        self.session_key = session_key
        self.cog = cog
        self.add_item(DismissButton(invoker_id, session_key, cog, row=1))

    async def _advance(self, interaction: discord.Interaction, take: bool) -> None:
        self.game.insurance(take=take)
        self.stop()
        await self.cog._send_bj_turn(interaction, self.game, self.eph, self.session_key)

    @discord.ui.button(label='take insurance', style=discord.ButtonStyle.secondary)
    async def take(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self._advance(interaction, take=True)

    @discord.ui.button(label='decline', style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await self._advance(interaction, take=False)

    async def on_timeout(self) -> None:
        self.cog._active_bj.discard(self.session_key)

class BlackjackView(discord.ui.View):

    def __init__(self, game: BlackjackGame, eph: bool, session_key: tuple, cog: 'CasinoCog', invoker_id: int=0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.game = game
        self.eph = eph
        self.session_key = session_key
        self.cog = cog
        self.invoker_id = invoker_id
        self._rebuild_buttons()

    def _rebuild_buttons(self) -> None:
        self.clear_items()
        state = self.game.state
        if state.phase != GamePhase.PLAYER_TURN:
            return
        hand = state.hands[state.active_hand_index]
        hit = discord.ui.Button(label='hit', style=discord.ButtonStyle.primary)
        hit.callback = self._hit
        self.add_item(hit)
        stand = discord.ui.Button(label='stand', style=discord.ButtonStyle.secondary)
        stand.callback = self._stand
        self.add_item(stand)
        if hand.can_double:
            dbl = discord.ui.Button(label='double', style=discord.ButtonStyle.secondary)
            dbl.callback = self._double
            self.add_item(dbl)
        if hand.can_split and len(state.hands) < 4:
            spl = discord.ui.Button(label='split', style=discord.ButtonStyle.secondary)
            spl.callback = self._split
            self.add_item(spl)
        self.add_item(DismissButton(self.invoker_id, self.session_key, self.cog, row=1))

    async def _hit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.game.hit()
        self.stop()
        await self.cog._send_bj_turn(interaction, self.game, self.eph, self.session_key)

    async def _stand(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.game.stand()
        self.stop()
        await self.cog._send_bj_turn(interaction, self.game, self.eph, self.session_key)

    async def _double(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        extra = self.game.hands[self.game.active_hand_index].bet
        try:
            await ledger.deduct(self.cog.bot.db, interaction.guild.id, interaction.user.id, extra, 'blackjack:double')
        except ValueError:
            await interaction.followup.send(f'not enough chips to double (need {_chips(extra)} more).', ephemeral=True)
            return
        self.game.double()
        self.stop()
        await self.cog._send_bj_turn(interaction, self.game, self.eph, self.session_key)

    async def _split(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        extra = self.game.bet
        try:
            await ledger.deduct(self.cog.bot.db, interaction.guild.id, interaction.user.id, extra, 'blackjack:split')
        except ValueError:
            await interaction.followup.send(f'not enough chips to split (need {_chips(extra)} more).', ephemeral=True)
            return
        self.game.split()
        self.stop()
        await self.cog._send_bj_turn(interaction, self.game, self.eph, self.session_key)

    async def on_timeout(self) -> None:
        self.cog._active_bj.discard(self.session_key)
_ROULETTE_OPTS = [discord.SelectOption(label='red', value='red', emoji='🔴'), discord.SelectOption(label='black', value='black', emoji='⚫'), discord.SelectOption(label='even', value='even'), discord.SelectOption(label='odd', value='odd'), discord.SelectOption(label='low (1-18)', value='low'), discord.SelectOption(label='high (19-36)', value='high'), discord.SelectOption(label='column 1', value='col1'), discord.SelectOption(label='column 2', value='col2'), discord.SelectOption(label='column 3', value='col3'), discord.SelectOption(label='dozen 1 (1-12)', value='doz1'), discord.SelectOption(label='dozen 2 (13-24)', value='doz2'), discord.SelectOption(label='dozen 3 (25-36)', value='doz3')]
_PAYOUT_LABELS = {'red': '1:1', 'black': '1:1', 'even': '1:1', 'odd': '1:1', 'low': '1:1', 'high': '1:1', 'col1': '2:1', 'col2': '2:1', 'col3': '2:1', 'doz1': '2:1', 'doz2': '2:1', 'doz3': '2:1'}
_OUTSIDE_MAP = {'red': lambda lb: bet_red(lb), 'black': lambda lb: bet_black(lb), 'even': lambda lb: bet_even(lb), 'odd': lambda lb: bet_odd(lb), 'low': lambda lb: bet_low(lb), 'high': lambda lb: bet_high(lb), 'col1': lambda lb: bet_column(lb, 1), 'col2': lambda lb: bet_column(lb, 2), 'col3': lambda lb: bet_column(lb, 3), 'doz1': lambda lb: bet_dozen(lb, 1), 'doz2': lambda lb: bet_dozen(lb, 2), 'doz3': lambda lb: bet_dozen(lb, 3)}

class RouletteView(discord.ui.View):

    def __init__(self, line_bet: int, eph: bool, session_key: tuple, cog: 'CasinoCog', invoker_id: int=0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.line_bet = line_bet
        self.eph = eph
        self.session_key = session_key
        self.cog = cog
        self.bets: list[RouletteBet] = []
        self.add_item(DismissButton(invoker_id, session_key, cog, row=3))

    def _build_embed(self) -> discord.Embed:
        lines = [f'bet per selection: {_chips(self.line_bet)}\n']
        if self.bets:
            lines.append('**current bets**')
            for b in self.bets:
                pays = _PAYOUT_LABELS.get(b.bet_type, 'varies')
                lines.append(f'• {b.label}  —  {_chips(b.amount)}  (pays {pays})')
            lines.append(f'\ntotal at risk: {_chips(sum((b.amount for b in self.bets)))}')
        else:
            lines.append('select a bet type below, then spin.')
        embed = discord.Embed(title='roulette', description='\n'.join(lines), color=COLOR_DEFAULT)
        embed.set_footer(text='american roulette  ·  0  00  1-36  ·  house edge 5.26%')
        return embed

    @discord.ui.select(placeholder='choose outside bet type', min_values=1, max_values=1, options=_ROULETTE_OPTS)
    async def outside_bet(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.defer()
        val = select.values[0]
        if val in _OUTSIDE_MAP:
            self.bets.append(_OUTSIDE_MAP[val](self.line_bet))
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    @discord.ui.button(label='straight bet (number)', style=discord.ButtonStyle.secondary, row=1)
    async def straight_modal_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_StraightBetModal(self))

    @discord.ui.button(label='clear bets', style=discord.ButtonStyle.danger, row=1)
    async def clear_bets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.bets.clear()
        await interaction.edit_original_response(embed=self._build_embed(), view=self)

    @discord.ui.button(label='spin', style=discord.ButtonStyle.primary, row=2)
    async def spin_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        if not self.bets:
            await interaction.followup.send('place at least one bet first.', ephemeral=True)
            return
        total = sum((b.amount for b in self.bets))
        try:
            await ledger.deduct(self.cog.bot.db, interaction.guild.id, interaction.user.id, total, 'roulette:wager')
        except ValueError:
            await interaction.followup.send(f'not enough chips. need {_chips(total)}.', ephemeral=True)
            return
        result = roulette_spin(self.bets)
        self.cog._active_roulette.discard(self.session_key)
        if result.total_returned > 0:
            await ledger.earn(self.cog.bot.db, interaction.guild.id, interaction.user.id, result.total_returned, 'roulette:payout')
        game_result = 'win' if result.net > 0 else 'loss' if result.net < 0 else 'push'
        await ledger.record_game_result(self.cog.bot.db, interaction.guild.id, interaction.user.id, 'roulette', result.total_wagered, result.total_returned, game_result)
        num = result.display_number
        color_emoji = '🔴' if result.color == 'red' else '⚫' if result.color == 'black' else '🟢'
        res_lines = [f'**{color_emoji} {num}**\n']
        for bet, payout in zip(result.bets, result.payouts):
            if payout > bet.amount:
                status = f'+{_fmt(payout - bet.amount)}'
            elif payout == bet.amount:
                status = 'push'
            else:
                status = f'-{_fmt(bet.amount)}'
            res_lines.append(f'• {bet.label}  →  {status}')
        net_str = f'+{_fmt(result.net)}' if result.net >= 0 else _fmt(result.net)
        res_lines.append(f'\nnet: {CHIPS_EMOJI} {net_str}')
        balance = await ledger.get_balance(self.cog.bot.db, interaction.guild.id, interaction.user.id)
        res_lines.append(f'balance: {_chips(balance)}')
        embed = discord.Embed(title='roulette result', description='\n'.join(res_lines), color=_net_color(result.net))
        embed.set_footer(text='american roulette  ·  0  00  1-36')
        self.stop()
        await interaction.edit_original_response(embed=embed, view=_ClearView())

    async def on_timeout(self) -> None:
        self.cog._active_roulette.discard(self.session_key)

class _StraightBetModal(discord.ui.Modal, title='straight bet'):
    number = discord.ui.TextInput(label='number (0, 00, or 1-36)', placeholder='e.g. 7', max_length=2)

    def __init__(self, rv: RouletteView):
        super().__init__()
        self.rv = rv

    async def on_submit(self, interaction: discord.Interaction) -> None:
        val = self.number.value.strip()
        if val == '00':
            target: int | str = '00'
        else:
            try:
                n = int(val)
                if not 0 <= n <= 36:
                    raise ValueError
                target = n
            except ValueError:
                await interaction.response.send_message('invalid number. enter 0, 00, or 1-36.', ephemeral=True)
                return
        self.rv.bets.append(bet_straight(self.rv.line_bet, target))
        await interaction.response.edit_message(embed=self.rv._build_embed(), view=self.rv)
_LINES_OPTIONS = [discord.SelectOption(label='1 line', value='1'), discord.SelectOption(label='3 lines', value='3'), discord.SelectOption(label='5 lines', value='5'), discord.SelectOption(label='9 lines', value='9', default=True)]

def _slots_grid_str(_a: list[list[str]]) -> str:
    _d = []
    for _c in range(3):
        _d.append('  '.join((SYM_EMOJI.get(_a[_b][_c], '?') for _b in range(5))))
    return '\n'.join(_d)

class _AutoSpinModal(discord.ui.Modal, title='auto spin'):
    count = discord.ui.TextInput(label='number of spins (1–1000)', placeholder='e.g. 25', max_length=4)

    def __init__(self, slots_view: 'SlotsView'):
        super().__init__()
        self._slots_view = slots_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            n = int(self.count.value.strip())
            if not 1 <= n <= 1000:
                raise ValueError
        except ValueError:
            await interaction.response.send_message('enter a whole number between 1 and 100.', ephemeral=True)
            return
        await interaction.response.defer()
        await self._slots_view._run_auto_spin(interaction, n)

class SlotsView(discord.ui.View):

    def __init__(self, line_bet: int, active_lines: int, eph: bool, session_key: tuple, cog: 'CasinoCog', free_spins: int=0, invoker_id: int=0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.line_bet = line_bet
        self.active_lines = active_lines
        self.eph = eph
        self.session_key = session_key
        self.cog = cog
        self.free_spins = free_spins
        self.invoker_id = invoker_id
        self.add_item(DismissButton(invoker_id, session_key, cog, row=2))

    def _build_embed(self, result: Optional[SpinResult]=None, jackpot_amount: int=0, balance: int=0, is_free_spin_result: bool=False) -> discord.Embed:
        total_bet = self.line_bet * self.active_lines
        lines = []
        if self.free_spins > 0:
            lines.append(f'**free spins: {self.free_spins} remaining**\n')
        if is_free_spin_result:
            lines.append(f'~~bet: {_chips(self.line_bet)} × {self.active_lines} lines~~  **free spin**')
        else:
            lines.append(f'bet: {_chips(self.line_bet)} × {self.active_lines} lines = {_chips(total_bet)}')
        if jackpot_amount:
            lines.append(f'jackpot: {_chips(jackpot_amount)}')
        if balance:
            lines.append(f'balance: {_chips(balance)}')
        if result:
            lines.append(f'\n{_slots_grid_str(result.grid)}\n')
            if result.jackpot_hit:
                lines.append(f'🎰 **JACKPOT!** {_chips(result.jackpot_amount)}')
            elif result.free_spins_awarded:
                lines.append(f'🔔 **free spins!** +{result.free_spins_awarded}')
            win_lines = [lr for lr in result.line_results if lr.payout > 0]
            if win_lines:
                lines.append('\n**winning lines**')
                for lr in win_lines[:5]:
                    sym = SYM_EMOJI.get(lr.match_symbol, '') if lr.match_symbol else ''
                    lines.append(f'• line {lr.line_index + 1}: {sym} ×{lr.match_count}  →  +{_fmt(lr.payout)}')
                if len(win_lines) > 5:
                    lines.append(f'  … and {len(win_lines) - 5} more winning lines')
            net_str = f'+{_fmt(result.net)}' if result.net >= 0 else _fmt(result.net)
            lines.append(f'\nnet: {CHIPS_EMOJI} {net_str}')
            color = COLOR_JACKPOT if result.jackpot_hit else _net_color(result.net)
        else:
            lines.append('\npress spin to play.')
            color = COLOR_DEFAULT
        embed = discord.Embed(description='\n'.join(lines), color=color)
        embed.set_footer(text='progressive slots  ·  5 reels  ·  3 rows  ·  9 paylines')
        return embed

    @discord.ui.select(placeholder='active paylines', min_values=1, max_values=1, options=_LINES_OPTIONS, row=0)
    async def lines_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.defer()
        self.active_lines = int(select.values[0])
        jackpot = await ledger.get_jackpot(self.cog.bot.db, interaction.guild.id)
        balance = await ledger.get_balance(self.cog.bot.db, interaction.guild.id, interaction.user.id)
        await interaction.edit_original_response(embed=self._build_embed(jackpot_amount=jackpot, balance=balance), view=self)

    @discord.ui.button(label='spin', style=discord.ButtonStyle.primary, row=1)
    async def spin_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        total_bet = self.line_bet * self.active_lines
        is_free = self.free_spins > 0
        if not is_free:
            try:
                await ledger.deduct(self.cog.bot.db, interaction.guild.id, interaction.user.id, total_bet, 'slots:wager')
            except ValueError:
                await interaction.followup.send(f'not enough chips. need {_chips(total_bet)}.', ephemeral=True)
                return
        cfg = self.cog.bot.config.get_guild_casino(interaction.guild.id)
        jackpot_amount = await ledger.get_jackpot(self.cog.bot.db, interaction.guild.id)
        grid = spin_all_reels()
        result = evaluate_spin(grid, self.line_bet, self.active_lines, jackpot_amount, is_free_spin=is_free, jackpot_contribution_pct=cfg.jackpot_contribution_pct, free_spins_award=cfg.slots_free_spins_award)
        if result.jackpot_hit:
            actual_jackpot = await ledger.claim_jackpot(self.cog.bot.db, interaction.guild.id, interaction.user.id)
            result = SpinResult(grid=result.grid, line_bet=result.line_bet, total_bet=result.total_bet, line_results=result.line_results, total_payout=result.total_payout - result.jackpot_amount + actual_jackpot, jackpot_contribution=0, free_spins_awarded=result.free_spins_awarded, jackpot_hit=True, jackpot_amount=actual_jackpot)
        else:
            await ledger.increment_jackpot(self.cog.bot.db, interaction.guild.id, result.jackpot_contribution)
        if result.total_payout > 0:
            await ledger.earn(self.cog.bot.db, interaction.guild.id, interaction.user.id, result.total_payout, 'slots:payout')
        game_result = 'win' if result.net > 0 else 'loss' if result.net < 0 else 'push'
        await ledger.record_game_result(self.cog.bot.db, interaction.guild.id, interaction.user.id, 'slots', result.total_bet, result.total_payout, game_result)
        new_free = max(0, self.free_spins - 1) + result.free_spins_awarded
        new_jackpot = await ledger.get_jackpot(self.cog.bot.db, interaction.guild.id)
        balance = await ledger.get_balance(self.cog.bot.db, interaction.guild.id, interaction.user.id)
        embed = self._build_embed(result=result, jackpot_amount=new_jackpot, balance=balance, is_free_spin_result=is_free)
        if result.jackpot_hit:
            self.cog._active_slots.discard(self.session_key)
            self.stop()
            await interaction.edit_original_response(embed=embed, view=_ClearView())
        else:
            new_view = SlotsView(self.line_bet, self.active_lines, self.eph, self.session_key, self.cog, free_spins=new_free, invoker_id=self.invoker_id)
            self.stop()
            await interaction.edit_original_response(embed=embed, view=new_view)

    @discord.ui.button(label='auto spin', style=discord.ButtonStyle.secondary, row=1)
    async def auto_spin_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(_AutoSpinModal(self))

    @discord.ui.button(label='done', style=discord.ButtonStyle.danger, row=1)
    async def done_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.cog._active_slots.discard(self.session_key)
        balance = await ledger.get_balance(self.cog.bot.db, interaction.guild.id, interaction.user.id)
        await interaction.edit_original_response(embed=discord.Embed(description=f'session ended. balance: {_chips(balance)}', color=COLOR_DEFAULT), view=_ClearView())
        self.stop()

    async def on_timeout(self) -> None:
        self.cog._active_slots.discard(self.session_key)

    async def _run_auto_spin(self, interaction: discord.Interaction, num_spins: int) -> None:
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        db = self.cog.bot.db
        cfg = self.cog.bot.config.get_guild_casino(guild_id)
        for item in self.children:
            item.disabled = True
        self.stop()
        spins_run = 0
        chips_wagered = 0
        chips_returned = 0
        bonuses_hit = 0
        biggest_win = 0
        free_spins_used = 0
        free_spins = self.free_spins
        last_net = 0

        def _tally_embed(spin_num: int, balance: int, jackpot_amt: int, note: str='', jackpot_hit: bool=False) -> discord.Embed:
            session_net = chips_returned - chips_wagered
            net_str = f'+{_fmt(session_net)}' if session_net >= 0 else _fmt(session_net)
            spin_net_str = f'+{_fmt(last_net)}' if last_net >= 0 else _fmt(last_net)
            lines = [f'**auto spin — {spin_num} / {num_spins}**\n', f'balance: {_chips(balance)}', f'session net: {CHIPS_EMOJI} {net_str}', f'wagered: {_chips(chips_wagered)}  ·  returned: {_chips(chips_returned)}', f'last spin: {CHIPS_EMOJI} {spin_net_str}']
            if free_spins > 0:
                lines.append(f'free spins remaining: {free_spins}')
            if note:
                lines.append(f'\n{note}')
            color = COLOR_JACKPOT if jackpot_hit else _net_color(session_net)
            embed = discord.Embed(description='\n'.join(lines), color=color)
            embed.set_footer(text=f'progressive slots  ·  {self.active_lines} lines  ·  biggest win: {_fmt(biggest_win)}  ·  bonuses: {bonuses_hit}')
            return embed
        jackpot_result: Optional[SpinResult] = None
        SPIN_DELAY = 1.5
        for spin_num in range(1, num_spins + 1):
            is_free = free_spins > 0
            total_bet = self.line_bet * self.active_lines
            if not is_free:
                try:
                    await ledger.deduct(db, guild_id, user_id, total_bet, 'slots:wager')
                    chips_wagered += total_bet
                except ValueError:
                    break
            else:
                free_spins_used += 1
            jackpot_amount = await ledger.get_jackpot(db, guild_id)
            grid = spin_all_reels()
            result = evaluate_spin(grid, self.line_bet, self.active_lines, jackpot_amount, is_free_spin=is_free, jackpot_contribution_pct=cfg.jackpot_contribution_pct, free_spins_award=cfg.slots_free_spins_award)
            if result.jackpot_hit:
                actual_jackpot = await ledger.claim_jackpot(db, guild_id, user_id)
                result = SpinResult(grid=result.grid, line_bet=result.line_bet, total_bet=result.total_bet, line_results=result.line_results, total_payout=result.total_payout - result.jackpot_amount + actual_jackpot, jackpot_contribution=0, free_spins_awarded=result.free_spins_awarded, jackpot_hit=True, jackpot_amount=actual_jackpot)
            else:
                await ledger.increment_jackpot(db, guild_id, result.jackpot_contribution)
            if result.total_payout > 0:
                await ledger.earn(db, guild_id, user_id, result.total_payout, 'slots:payout')
                chips_returned += result.total_payout
            game_result = 'win' if result.net > 0 else 'loss' if result.net < 0 else 'push'
            await ledger.record_game_result(db, guild_id, user_id, 'slots', result.total_bet, result.total_payout, game_result)
            last_net = result.net
            if result.net > biggest_win:
                biggest_win = result.net
            if result.free_spins_awarded:
                bonuses_hit += 1
            free_spins = max(0, free_spins - 1) + result.free_spins_awarded
            spins_run = spin_num
            if result.jackpot_hit:
                jackpot_result = result
                break
            balance = await ledger.get_balance(db, guild_id, user_id)
            jackpot_amt = await ledger.get_jackpot(db, guild_id)
            note = ''
            if result.free_spins_awarded:
                note = f'🔔 **bonus triggered!** +{result.free_spins_awarded} free spins'
            elif result.net > self.line_bet * self.active_lines * 10:
                note = f'💰 big win: {CHIPS_EMOJI} +{_fmt(result.net)}'
            embed = _tally_embed(spin_num, balance, jackpot_amt, note=note)
            try:
                await interaction.edit_original_response(embed=embed)
            except discord.HTTPException:
                pass
            is_last = spin_num == num_spins
            if not is_last:
                await asyncio.sleep(SPIN_DELAY)
        balance = await ledger.get_balance(db, guild_id, user_id)
        if jackpot_result is not None:
            self.cog._active_slots.discard(self.session_key)
            jackpot_amt = jackpot_result.jackpot_amount
            lines = [f'🎰 **JACKPOT!** {_chips(jackpot_amt)}\n', f'balance: {_chips(balance)}', f'session: {spins_run} spins  ·  wagered {_chips(chips_wagered)}  ·  returned {_chips(chips_returned)}']
            embed = discord.Embed(description='\n'.join(lines), color=COLOR_JACKPOT)
            embed.set_footer(text='progressive slots  ·  jackpot')
            await interaction.edit_original_response(embed=embed, view=_ClearView())
            return
        self.cog._active_slots.discard(self.session_key)
        session_net = chips_returned - chips_wagered
        net_str = f'+{_fmt(session_net)}' if session_net >= 0 else _fmt(session_net)
        summary_lines = [f'**auto spin complete — {spins_run} spins**\n', f'balance: {_chips(balance)}', f'session net: {CHIPS_EMOJI} {net_str}', f'wagered: {_chips(chips_wagered)}  ·  returned: {_chips(chips_returned)}']
        if free_spins_used:
            summary_lines.append(f'free spins used: {free_spins_used}')
        if bonuses_hit:
            summary_lines.append(f'bonus triggers: {bonuses_hit}')
        if biggest_win:
            summary_lines.append(f'biggest win: {_chips(biggest_win)}')
        summary_embed = discord.Embed(description='\n'.join(summary_lines), color=_net_color(session_net))
        summary_embed.set_footer(text='progressive slots  ·  auto spin summary')
        self.cog._active_slots.add(self.session_key)
        new_view = SlotsView(self.line_bet, self.active_lines, self.eph, self.session_key, self.cog, free_spins=free_spins, invoker_id=self.invoker_id)
        await interaction.edit_original_response(embed=summary_embed, view=new_view)

async def _fetch_leaderboard(_b, _d: int, _a: str, _c: Optional[str], _e: int=10) -> list[dict]:
    match _a:
        case 'balance':
            _g = await _b.fetch_all('SELECT user_id, balance, total_earned, total_spent FROM chip_balances WHERE guild_id = ? ORDER BY balance DESC LIMIT ?', (_d, _e))
        case 'earned':
            _g = await _b.fetch_all('SELECT user_id, total_earned AS value FROM chip_balances WHERE guild_id = ? ORDER BY total_earned DESC LIMIT ?', (_d, _e))
        case 'spent':
            _g = await _b.fetch_all('SELECT user_id, total_spent AS value FROM chip_balances WHERE guild_id = ? ORDER BY total_spent DESC LIMIT ?', (_d, _e))
        case 'wins' if _c:
            _g = await _b.fetch_all('SELECT user_id, games_won AS value, games_played FROM casino_game_stats WHERE guild_id = ? AND game = ? ORDER BY games_won DESC LIMIT ?', (_d, _c, _e))
        case 'losses' if _c:
            _g = await _b.fetch_all('SELECT user_id, games_lost AS value, games_played FROM casino_game_stats WHERE guild_id = ? AND game = ? ORDER BY games_lost DESC LIMIT ?', (_d, _c, _e))
        case 'net' if _c:
            _g = await _b.fetch_all('SELECT user_id, (total_won - total_wagered) AS value, games_played FROM casino_game_stats WHERE guild_id = ? AND game = ? ORDER BY value DESC LIMIT ?', (_d, _c, _e))
        case 'wagered' if _c:
            _g = await _b.fetch_all('SELECT user_id, total_wagered AS value, games_played FROM casino_game_stats WHERE guild_id = ? AND game = ? ORDER BY total_wagered DESC LIMIT ?', (_d, _c, _e))
        case 'biggest_win' if _c:
            _g = await _b.fetch_all('SELECT user_id, biggest_win AS value FROM casino_game_stats WHERE guild_id = ? AND game = ? ORDER BY biggest_win DESC LIMIT ?', (_d, _c, _e))
        case _:
            _g = []
    return [dict(_f) for _f in _g]

class CasinoCog(commands.Cog, name='casino'):

    def __init__(self, bot: 'KnowledgeBot') -> None:
        self.bot = bot
        self._active_bj: set[tuple] = set()
        self._active_roulette: set[tuple] = set()
        self._active_slots: set[tuple] = set()

    def _owner_id(self) -> int:
        env_key = self.bot.config.bot.owner_id_env
        return int(os.environ.get(env_key, 0))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.bot.db:
            return
        await ledger.passive_earn_from_message(self.bot.db, message)

    @app_commands.command(name='chips', description='check your chip balance')
    async def chips_cmd(self, interaction: discord.Interaction) -> None:
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        row = await ledger.get_full_balance_row(self.bot.db, interaction.guild.id, interaction.user.id)
        jackpot = await ledger.get_jackpot(self.bot.db, interaction.guild.id)
        embed = discord.Embed(color=COLOR_INFO)
        embed.add_field(name='balance', value=_chips(row['balance']), inline=True)
        embed.add_field(name='total earned', value=_chips(row['total_earned']), inline=True)
        embed.add_field(name='total spent', value=_chips(row['total_spent']), inline=True)
        embed.add_field(name='jackpot pool', value=_chips(jackpot), inline=False)
        embed.set_footer(text='1 per message  ·  3 per link  ·  5 per attachment')
        await interaction.followup.send(embed=embed, ephemeral=eph)

    @app_commands.command(name='give', description='give chips to a user (owner only)')
    @app_commands.describe(user='recipient', amount='chips to give')
    async def give_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: int) -> None:
        if interaction.user.id != self._owner_id():
            await interaction.response.send_message('owner only.', ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message('amount must be positive.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        new_balance = await ledger.earn(self.bot.db, interaction.guild.id, user.id, amount, 'admin:give')
        await interaction.followup.send(f'gave {_chips(amount)} to {user.mention}. balance: {_chips(new_balance)}', ephemeral=True)

    @app_commands.command(name='backfill', description='backfill chips from message history (owner only)')
    @app_commands.describe(dry_run='preview without writing changes')
    async def backfill_cmd(self, interaction: discord.Interaction, dry_run: bool=False) -> None:
        if interaction.user.id != self._owner_id():
            await interaction.response.send_message('owner only.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await ledger.backfill_from_db(self.bot.db, interaction.guild.id, dry_run=dry_run)
        label = 'dry run — no changes made' if dry_run else 'backfill complete'
        embed = discord.Embed(title=label, color=COLOR_INFO)
        embed.add_field(name='messages scanned', value=_fmt(result['total_messages']))
        embed.add_field(name='chips to award', value=_chips(result['total_chips']))
        embed.add_field(name='users affected', value=_fmt(result['users_affected']))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name='blackjack', description='play a hand of blackjack')
    @app_commands.describe(bet='chips to wager')
    async def blackjack_cmd(self, interaction: discord.Interaction, bet: int) -> None:
        eph = ephemeral_for(interaction.user.id)
        session_key = (interaction.guild.id, interaction.user.id, 'bj')
        if session_key in self._active_bj:
            await interaction.response.send_message('you already have an active blackjack game.', ephemeral=True)
            return
        if not MIN_BET <= bet <= MAX_BET:
            await interaction.response.send_message(f'bet must be between {_chips(MIN_BET)} and {_chips(MAX_BET)}.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=eph)
        balance = await ledger.get_balance(self.bot.db, interaction.guild.id, interaction.user.id)
        if balance < bet:
            await interaction.followup.send(f'not enough chips. balance: {_chips(balance)}, bet: {_chips(bet)}.', ephemeral=True)
            return
        await ledger.deduct(self.bot.db, interaction.guild.id, interaction.user.id, bet, 'blackjack:bet')
        self._active_bj.add(session_key)
        game = BlackjackGame(bet=bet)
        game.deal()
        await self._send_bj_turn(interaction, game, eph, session_key, initial=True)

    async def _send_bj_turn(self, interaction: discord.Interaction, game: BlackjackGame, eph: bool, session_key: tuple, initial: bool=False) -> None:
        state = game.state

        async def _send(embed: discord.Embed, view: discord.ui.View | None=None) -> None:
            if initial:
                await interaction.followup.send(embed=embed, view=view, ephemeral=eph)
            else:
                await interaction.edit_original_response(embed=embed, view=view)
        if state.phase == GamePhase.INSURANCE:
            embed = _bj_embed(game)
            embed.title = 'blackjack — insurance?'
            half = game.bet // 2
            embed.description = f'dealer shows an ace. insurance costs {_chips(half)}.\n\n' + (embed.description or '')
            view = InsuranceView(game, eph, session_key, self, invoker_id=session_key[1])
            await _send(embed, view)
            return
        if state.phase == GamePhase.PLAYER_TURN:
            embed = _bj_embed(game)
            view = BlackjackView(game, eph, session_key, self, invoker_id=session_key[1])
            await _send(embed, view)
            return
        if state.phase == GamePhase.COMPLETE:
            self._active_bj.discard(session_key)
            embed = _bj_embed(game, final=True)
            if game.total_payout > 0:
                await ledger.earn(self.bot.db, interaction.guild.id, interaction.user.id, game.total_payout, 'blackjack:payout')
            game_result = 'win' if game.net > 0 else 'loss' if game.net < 0 else 'push'
            await ledger.record_game_result(self.bot.db, interaction.guild.id, interaction.user.id, 'blackjack', game.total_wagered, game.total_payout, game_result)
            balance = await ledger.get_balance(self.bot.db, interaction.guild.id, interaction.user.id)
            embed.set_footer(text=f'blackjack  ·  balance: {_fmt(balance)} chips')
            await _send(embed, _ClearView())

    @app_commands.command(name='roulette', description='spin the roulette wheel')
    @app_commands.describe(bet='chips per bet selection')
    async def roulette_cmd(self, interaction: discord.Interaction, bet: int) -> None:
        eph = ephemeral_for(interaction.user.id)
        session_key = (interaction.guild.id, interaction.user.id, 'roulette')
        if session_key in self._active_roulette:
            await interaction.response.send_message('you already have an active roulette session.', ephemeral=True)
            return
        if not MIN_BET <= bet <= MAX_BET:
            await interaction.response.send_message(f'bet must be between {_chips(MIN_BET)} and {_chips(MAX_BET)}.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=eph)
        balance = await ledger.get_balance(self.bot.db, interaction.guild.id, interaction.user.id)
        if balance < bet:
            await interaction.followup.send(f'not enough chips. balance: {_chips(balance)}.', ephemeral=True)
            return
        self._active_roulette.add(session_key)
        view = RouletteView(bet, eph, session_key, self, invoker_id=interaction.user.id)
        await interaction.followup.send(embed=view._build_embed(), view=view, ephemeral=eph)

    @app_commands.command(name='slots', description='spin the progressive slots')
    @app_commands.describe(bet='chips per active payline')
    async def slots_cmd(self, interaction: discord.Interaction, bet: int) -> None:
        eph = ephemeral_for(interaction.user.id)
        session_key = (interaction.guild.id, interaction.user.id, 'slots')
        if session_key in self._active_slots:
            await interaction.response.send_message('you already have an active slots session.', ephemeral=True)
            return
        if not MIN_BET <= bet <= MAX_BET:
            await interaction.response.send_message(f'bet must be between {_chips(MIN_BET)} and {_chips(MAX_BET)}.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=eph)
        balance = await ledger.get_balance(self.bot.db, interaction.guild.id, interaction.user.id)
        if balance < bet:
            await interaction.followup.send(f'not enough chips. balance: {_chips(balance)}.', ephemeral=True)
            return
        jackpot = await ledger.get_jackpot(self.bot.db, interaction.guild.id)
        self._active_slots.add(session_key)
        view = SlotsView(bet, 9, eph, session_key, self, invoker_id=interaction.user.id)
        await interaction.followup.send(embed=view._build_embed(jackpot_amount=jackpot, balance=balance), view=view, ephemeral=eph)

    @app_commands.command(name='leaderboard', description='casino and chip leaderboards')
    @app_commands.describe(board='ranking category', game='game filter')
    @app_commands.choices(board=[app_commands.Choice(name='chip balance', value='balance'), app_commands.Choice(name='most earned', value='earned'), app_commands.Choice(name='most spent', value='spent'), app_commands.Choice(name='most wins', value='wins'), app_commands.Choice(name='most losses', value='losses'), app_commands.Choice(name='net profit', value='net'), app_commands.Choice(name='most wagered', value='wagered'), app_commands.Choice(name='biggest single win', value='biggest_win')], game=[app_commands.Choice(name='blackjack', value='blackjack'), app_commands.Choice(name='roulette', value='roulette'), app_commands.Choice(name='slots', value='slots'), app_commands.Choice(name='all games', value='all')])
    async def leaderboard_cmd(self, interaction: discord.Interaction, board: str='balance', game: str='all') -> None:
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        game_filter = None if game == 'all' else game
        needs_game = board in ('wins', 'losses', 'net', 'wagered', 'biggest_win')
        if needs_game and game == 'all':
            match board:
                case 'wins':
                    rows = await self.bot.db.fetch_all('SELECT user_id, SUM(games_won) AS value, SUM(games_played) AS games_played FROM casino_game_stats WHERE guild_id = ? GROUP BY user_id ORDER BY value DESC LIMIT 10', (interaction.guild.id,))
                case 'losses':
                    rows = await self.bot.db.fetch_all('SELECT user_id, SUM(games_lost) AS value, SUM(games_played) AS games_played FROM casino_game_stats WHERE guild_id = ? GROUP BY user_id ORDER BY value DESC LIMIT 10', (interaction.guild.id,))
                case 'net':
                    rows = await self.bot.db.fetch_all('SELECT user_id, SUM(total_won - total_wagered) AS value, SUM(games_played) AS games_played FROM casino_game_stats WHERE guild_id = ? GROUP BY user_id ORDER BY value DESC LIMIT 10', (interaction.guild.id,))
                case 'wagered':
                    rows = await self.bot.db.fetch_all('SELECT user_id, SUM(total_wagered) AS value, SUM(games_played) AS games_played FROM casino_game_stats WHERE guild_id = ? GROUP BY user_id ORDER BY value DESC LIMIT 10', (interaction.guild.id,))
                case 'biggest_win':
                    rows = await self.bot.db.fetch_all('SELECT user_id, MAX(biggest_win) AS value FROM casino_game_stats WHERE guild_id = ? GROUP BY user_id ORDER BY value DESC LIMIT 10', (interaction.guild.id,))
                case _:
                    rows = []
            rows = [dict(r) for r in rows]
        else:
            rows = await _fetch_leaderboard(self.bot.db, interaction.guild.id, board, game_filter)
        title_map = {'balance': 'chip balance', 'earned': 'most chips earned', 'spent': 'most chips spent', 'wins': 'most wins', 'losses': 'most losses', 'net': 'net profit', 'wagered': 'most wagered', 'biggest_win': 'biggest single win'}
        game_label = f' — {game_filter}' if game_filter else ''
        title = title_map.get(board, board) + game_label
        if not rows:
            await interaction.followup.send(f'no data yet for **{title}**.', ephemeral=eph)
            return
        medals = ['🥇', '🥈', '🥉']
        lines = []
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f'{i + 1}.'
            member = interaction.guild.get_member(row['user_id'])
            name = member.display_name if member else f"user {row['user_id']}"
            if board == 'balance':
                val = f"{_chips(row['balance'])}  (earned {_fmt(row['total_earned'])}, spent {_fmt(row['total_spent'])})"
            elif board in ('earned', 'spent', 'biggest_win'):
                val = _chips(row['value'])
            elif board == 'net':
                v = row['value']
                val = f"{CHIPS_EMOJI} {('+' if v >= 0 else '')}{_fmt(v)}"
                if 'games_played' in row:
                    val += f"  ({_fmt(row['games_played'])} games)"
            else:
                val = _fmt(row['value'])
                if 'games_played' in row and row['games_played']:
                    rate = round(row['value'] / row['games_played'] * 100, 1)
                    val += f"  /  {_fmt(row['games_played'])} games  ({rate}%)"
            lines.append(f'{prefix} **{name}** — {val}')
        embed = discord.Embed(title=title, description='\n'.join(lines), color=COLOR_INFO)
        embed.set_footer(text=f'top {len(rows)}  ·  {interaction.guild.name}')
        await interaction.followup.send(embed=embed, ephemeral=eph)

    @app_commands.command(name='gamestats', description='view your casino game statistics')
    @app_commands.describe(game='game to show stats for')
    @app_commands.choices(game=[app_commands.Choice(name='all', value='all'), app_commands.Choice(name='blackjack', value='blackjack'), app_commands.Choice(name='roulette', value='roulette'), app_commands.Choice(name='slots', value='slots')])
    async def gamestats_cmd(self, interaction: discord.Interaction, game: str='all') -> None:
        eph = ephemeral_for(interaction.user.id)
        await interaction.response.defer(ephemeral=eph)
        games = ['blackjack', 'roulette', 'slots'] if game == 'all' else [game]
        placeholders = ','.join('?' * len(games))
        rows = await self.bot.db.fetch_all(f'SELECT game, games_played, games_won, games_lost, games_pushed, total_wagered, total_won, biggest_win, biggest_loss FROM casino_game_stats WHERE guild_id = ? AND user_id = ? AND game IN ({placeholders})', (interaction.guild.id, interaction.user.id, *games))
        if not rows:
            await interaction.followup.send('no game stats yet — go play!', ephemeral=eph)
            return
        embed = discord.Embed(title=f'casino stats — {interaction.user.display_name}', color=COLOR_INFO)
        for row in rows:
            played = row['games_played']
            won = row['games_won']
            lost = row['games_lost']
            pushed = row['games_pushed']
            wagered = row['total_wagered']
            returned = row['total_won']
            net = returned - wagered
            win_rate = round(won / played * 100, 1) if played else 0.0
            rtp = round(returned / wagered * 100, 1) if wagered else 0.0
            field_val = '\n'.join([f'played: {_fmt(played)}', f'w / l / p: {_fmt(won)} / {_fmt(lost)} / {_fmt(pushed)}', f'win rate: {win_rate}%', f'wagered: {_chips(wagered)}', f"net: {CHIPS_EMOJI} {('+' if net >= 0 else '')}{_fmt(net)}", f'rtp: {rtp}%', f"biggest win: {_chips(row['biggest_win'])}", f"biggest loss: {_chips(row['biggest_loss'])}"])
            embed.add_field(name=row['game'], value=field_val, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=eph)

async def setup(_a: 'KnowledgeBot') -> None:
    await _a.add_cog(CasinoCog(_a))