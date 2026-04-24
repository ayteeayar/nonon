from __future__ import annotations
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
SUITS = ('♠', '♥', '♦', '♣')
RANKS = ('A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K')
RANK_VALUES: dict[str, int] = {'A': 11, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 10, 'Q': 10, 'K': 10}

@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    def __str__(self) -> str:
        return f'{self.rank}{self.suit}'

    @property
    def value(self) -> int:
        return RANK_VALUES[self.rank]

    @property
    def is_ace(self) -> bool:
        return self.rank == 'A'

    @property
    def is_face_down(self) -> bool:
        return False
FACE_DOWN = Card('?', '?')

def _build_shoe(_b: int=6) -> list[Card]:
    _e = [Card(_c, _d) for _a in range(_b) for _d in SUITS for _c in RANKS]
    random.shuffle(_e)
    return _e

def hand_value(_c: list[Card]) -> int:
    _d = 0
    _a = 0
    for _b in _c:
        if _b.rank == '?':
            continue
        _d += _b.value
        if _b.is_ace:
            _a += 1
    while _d > 21 and _a:
        _d -= 10
        _a -= 1
    return _d

def is_soft(_c: list[Card]) -> bool:
    _d = sum((_b.value for _b in _c if _b.rank != '?'))
    _a = sum((1 for _b in _c if _b.is_ace))
    return _a > 0 and _d <= 21

def is_blackjack(_a: list[Card]) -> bool:
    return len(_a) == 2 and hand_value(_a) == 21

def is_pair(_a: list[Card]) -> bool:
    return len(_a) == 2 and _a[0].rank == _a[1].rank

class HandStatus(Enum):
    ACTIVE = auto()
    STOOD = auto()
    BUST = auto()
    BLACKJACK = auto()
    DOUBLED = auto()
    SURRENDERED = auto()

class GamePhase(Enum):
    INSURANCE = auto()
    PLAYER_TURN = auto()
    DEALER_TURN = auto()
    COMPLETE = auto()

@dataclass
class PlayerHand:
    cards: list[Card] = field(default_factory=list)
    bet: int = 0
    status: HandStatus = HandStatus.ACTIVE
    insurance_bet: int = 0

    @property
    def value(self) -> int:
        return hand_value(self.cards)

    @property
    def soft(self) -> bool:
        return is_soft(self.cards)

    @property
    def busted(self) -> bool:
        return self.value > 21

    @property
    def is_bj(self) -> bool:
        return is_blackjack(self.cards)

    @property
    def can_split(self) -> bool:
        return is_pair(self.cards) and self.status == HandStatus.ACTIVE

    @property
    def can_double(self) -> bool:
        return len(self.cards) == 2 and self.status == HandStatus.ACTIVE

    def display(self, hide_second: bool=False) -> str:
        if hide_second and len(self.cards) >= 2:
            return f'{self.cards[0]}  ?'
        return '  '.join((str(c) for c in self.cards))

@dataclass
class DealerHand:
    cards: list[Card] = field(default_factory=list)

    @property
    def value(self) -> int:
        return hand_value(self.cards)

    @property
    def soft(self) -> bool:
        return is_soft(self.cards)

    @property
    def upcard(self) -> Optional[Card]:
        return self.cards[0] if self.cards else None

    @property
    def is_bj(self) -> bool:
        return is_blackjack(self.cards)

    def display(self, hide_hole: bool=True) -> str:
        if hide_hole and len(self.cards) >= 2:
            return f'{self.cards[0]}  ?'
        return '  '.join((str(c) for c in self.cards))

@dataclass
class Outcome:
    hand_index: int
    result: str
    payout: int
    net: int
    label: str

@dataclass
class BlackjackState:
    phase: GamePhase
    dealer: DealerHand
    hands: list[PlayerHand]
    active_hand_index: int
    shoe_remaining: int
    outcomes: list[Outcome]
    insurance_available: bool
    insurance_declined: bool

class BlackjackGame:
    MAX_SPLITS = 3
    RESHUFFLE_AT = 0.25

    def __init__(self, bet: int, decks: int=6) -> None:
        self.bet = bet
        self.decks = decks
        self._shoe: list[Card] = _build_shoe(decks)
        self._shoe_size = len(self._shoe)
        self.dealer = DealerHand()
        self.hands: list[PlayerHand] = []
        self.active_hand_index: int = 0
        self.phase: GamePhase = GamePhase.PLAYER_TURN
        self.outcomes: list[Outcome] = []
        self._insurance_available = False
        self._insurance_declined = False

    def _draw(self) -> Card:
        if len(self._shoe) / self._shoe_size < self.RESHUFFLE_AT:
            self._shoe = _build_shoe(self.decks)
            self._shoe_size = len(self._shoe)
        return self._shoe.pop()

    def deal(self) -> None:
        hand = PlayerHand(bet=self.bet)
        hand.cards = [self._draw(), self._draw()]
        self.dealer.cards = [self._draw(), self._draw()]
        self.hands = [hand]
        self.active_hand_index = 0
        if self.dealer.upcard and self.dealer.upcard.is_ace:
            self._insurance_available = True
            self.phase = GamePhase.INSURANCE
            return
        self._check_initial_blackjack()

    def insurance(self, take: bool) -> None:
        if self.phase != GamePhase.INSURANCE:
            return
        self._insurance_available = False
        self._insurance_declined = not take
        if take:
            ins = self.bet // 2
            self.hands[0].insurance_bet = ins
        self._check_initial_blackjack()

    def hit(self) -> None:
        if self.phase != GamePhase.PLAYER_TURN:
            return
        hand = self.hands[self.active_hand_index]
        if hand.status != HandStatus.ACTIVE:
            return
        hand.cards.append(self._draw())
        if hand.busted:
            hand.status = HandStatus.BUST
            self._advance_hand()
        elif hand.value == 21:
            hand.status = HandStatus.STOOD
            self._advance_hand()

    def stand(self) -> None:
        if self.phase != GamePhase.PLAYER_TURN:
            return
        hand = self.hands[self.active_hand_index]
        if hand.status != HandStatus.ACTIVE:
            return
        hand.status = HandStatus.STOOD
        self._advance_hand()

    def double(self) -> bool:
        if self.phase != GamePhase.PLAYER_TURN:
            return False
        hand = self.hands[self.active_hand_index]
        if not hand.can_double:
            return False
        hand.bet *= 2
        hand.cards.append(self._draw())
        hand.status = HandStatus.DOUBLED if not hand.busted else HandStatus.BUST
        self._advance_hand()
        return True

    def split(self) -> bool:
        if self.phase != GamePhase.PLAYER_TURN:
            return False
        if len(self.hands) > self.MAX_SPLITS:
            return False
        hand = self.hands[self.active_hand_index]
        if not hand.can_split:
            return False
        splitting_aces = hand.cards[0].is_ace
        new_hand = PlayerHand(bet=self.bet)
        new_hand.cards = [hand.cards.pop()]
        hand.cards.append(self._draw())
        new_hand.cards.append(self._draw())
        self.hands.insert(self.active_hand_index + 1, new_hand)
        if splitting_aces:
            hand.status = HandStatus.STOOD
            new_hand.status = HandStatus.STOOD
            self._advance_hand()
        return True

    def _check_initial_blackjack(self) -> None:
        hand = self.hands[0]
        dealer_bj = self.dealer.is_bj
        if hand.is_bj and dealer_bj:
            self.phase = GamePhase.COMPLETE
            self._resolve()
            return
        if dealer_bj:
            self.phase = GamePhase.COMPLETE
            self._resolve()
            return
        if hand.is_bj:
            hand.status = HandStatus.BLACKJACK
            self.phase = GamePhase.COMPLETE
            self._resolve()
            return
        self.phase = GamePhase.PLAYER_TURN

    def _advance_hand(self) -> None:
        for i in range(self.active_hand_index + 1, len(self.hands)):
            if self.hands[i].status == HandStatus.ACTIVE:
                self.active_hand_index = i
                return
        self._dealer_play()

    def _dealer_play(self) -> None:
        self.phase = GamePhase.DEALER_TURN
        any_alive = any((h.status not in (HandStatus.BUST, HandStatus.SURRENDERED) for h in self.hands))
        if any_alive:
            while True:
                v = self.dealer.value
                soft = self.dealer.soft
                if v < 17 or (v == 17 and soft):
                    self.dealer.cards.append(self._draw())
                else:
                    break
        self.phase = GamePhase.COMPLETE
        self._resolve()

    def _resolve(self) -> None:
        dealer_bj = self.dealer.is_bj
        dv = self.dealer.value
        for i, hand in enumerate(self.hands):
            pv = hand.value
            ins_win = False
            if hand.insurance_bet > 0:
                if dealer_bj:
                    ins_payout = hand.insurance_bet * 3
                    ins_win = True
                    self.outcomes.append(Outcome(hand_index=i, result='insurance_win', payout=ins_payout, net=ins_payout - hand.insurance_bet * 2, label=f'insurance win +{ins_payout - hand.insurance_bet}'))
                else:
                    self.outcomes.append(Outcome(hand_index=i, result='insurance_loss', payout=0, net=-hand.insurance_bet, label=f'insurance loss -{hand.insurance_bet}'))
            if hand.status == HandStatus.BLACKJACK:
                if dealer_bj:
                    self.outcomes.append(Outcome(hand_index=i, result='push', payout=hand.bet, net=0, label='push — blackjack vs blackjack'))
                else:
                    win = int(hand.bet * 1.5)
                    payout = hand.bet + win
                    self.outcomes.append(Outcome(hand_index=i, result='blackjack', payout=payout, net=win, label=f'blackjack! +{win}'))
            elif hand.status in (HandStatus.BUST, HandStatus.SURRENDERED):
                self.outcomes.append(Outcome(hand_index=i, result='loss', payout=0, net=-hand.bet, label=f'bust -{hand.bet}' if hand.status == HandStatus.BUST else 'surrendered'))
            elif dealer_bj:
                self.outcomes.append(Outcome(hand_index=i, result='loss', payout=0, net=-hand.bet, label=f'dealer blackjack -{hand.bet}'))
            elif dv > 21:
                self.outcomes.append(Outcome(hand_index=i, result='win', payout=hand.bet * 2, net=hand.bet, label=f'dealer bust +{hand.bet}'))
            elif pv > dv:
                self.outcomes.append(Outcome(hand_index=i, result='win', payout=hand.bet * 2, net=hand.bet, label=f'win +{hand.bet}'))
            elif pv == dv:
                self.outcomes.append(Outcome(hand_index=i, result='push', payout=hand.bet, net=0, label='push'))
            else:
                self.outcomes.append(Outcome(hand_index=i, result='loss', payout=0, net=-hand.bet, label=f'loss -{hand.bet}'))

    @property
    def state(self) -> BlackjackState:
        return BlackjackState(phase=self.phase, dealer=self.dealer, hands=self.hands, active_hand_index=self.active_hand_index, shoe_remaining=len(self._shoe), outcomes=self.outcomes, insurance_available=self._insurance_available, insurance_declined=self._insurance_declined)

    @property
    def total_wagered(self) -> int:
        return sum((h.bet for h in self.hands)) + sum((h.insurance_bet for h in self.hands))

    @property
    def total_payout(self) -> int:
        return sum((o.payout for o in self.outcomes))

    @property
    def net(self) -> int:
        return sum((o.net for o in self.outcomes))