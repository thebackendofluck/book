# Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Poker Hand State Machine
Chapter 11 - Online Poker Platform Architecture

Full Texas Hold'em hand state machine covering:
- Preflop through showdown phase transitions
- Blind posting, ante handling
- Action validation (fold, check, call, bet, raise, all-in)
- Timeout handling with auto-fold/auto-check
- Side pot creation on all-in
- Showdown logic with hand evaluation
- State versioning and hash validation for sync

Dependencies: None (stdlib only)
"""

import enum
import time
import hashlib
import importlib.util
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from collections import OrderedDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("poker.state_machine")

_side_pot_calculator_module = None


def _get_side_pot_calculator():
    """Load the sibling ``side-pot-calculator.py`` module and cache it.

    The file uses a hyphenated name, like the other chapter-11 scripts, so
    it cannot be imported with ``from .side_pot_calculator import ...`` --
    hyphens aren't valid in Python identifiers and this module also isn't
    always loaded as part of a package. Loading it by file path sidesteps
    both problems.
    """
    global _side_pot_calculator_module
    if _side_pot_calculator_module is None:
        module_path = Path(__file__).with_name("side-pot-calculator.py")
        spec = importlib.util.spec_from_file_location("side_pot_calculator", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load side pot calculator from {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _side_pot_calculator_module = module
    return _side_pot_calculator_module


# ─── Enums ────────────────────────────────────────────────────────────

class Phase(enum.Enum):
    WAITING = "waiting"
    POSTING_BLINDS = "posting_blinds"
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    HAND_COMPLETE = "hand_complete"


class Action(enum.Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


class PlayerStatus(enum.Enum):
    ACTIVE = "active"           # In hand, can act
    FOLDED = "folded"           # Folded this hand
    ALL_IN = "all_in"           # All-in, no more actions
    SITTING_OUT = "sitting_out" # Sitting out (missed blinds)
    ELIMINATED = "eliminated"   # Tournament: busted out


# ─── Data Classes ─────────────────────────────────────────────────────

@dataclass
class Player:
    player_id: str
    seat: int
    stack: int              # Chips remaining
    status: PlayerStatus = PlayerStatus.ACTIVE
    bet_this_round: int = 0 # Bet placed in current betting round
    total_bet: int = 0      # Total bet in this hand (for side pots)
    hole_cards: list = field(default_factory=list)
    has_acted: bool = False
    timeout_count: int = 0  # Consecutive timeouts

    def reset_for_round(self):
        """Reset per-betting-round state."""
        self.bet_this_round = 0
        self.has_acted = False

    def reset_for_hand(self):
        """Reset for new hand."""
        self.status = PlayerStatus.ACTIVE
        self.bet_this_round = 0
        self.total_bet = 0
        self.hole_cards = []
        self.has_acted = False


@dataclass
class ActionRecord:
    """Immutable record of a player action for audit trail."""
    hand_id: str
    phase: str
    player_id: str
    action: str
    amount: int
    pot_after: int
    stack_after: int
    timestamp: float
    sequence: int


@dataclass
class HandState:
    hand_id: str
    table_id: str
    phase: Phase = Phase.WAITING
    players: dict = field(default_factory=dict)   # seat -> Player
    community_cards: list = field(default_factory=list)
    pot: int = 0
    current_bet: int = 0           # Current bet to match
    min_raise: int = 0             # Minimum raise amount
    dealer_seat: int = 0
    small_blind_seat: int = 0
    big_blind_seat: int = 0
    action_on_seat: int = -1       # Seat that must act next
    small_blind: int = 0
    big_blind: int = 0
    ante: int = 0
    deck: list = field(default_factory=list)
    action_history: list = field(default_factory=list)  # List of ActionRecord
    side_pots: list = field(default_factory=list)
    version: int = 0
    created_at: float = field(default_factory=time.time)
    action_deadline: float = 0.0   # When current action times out

    # Configuration
    action_timeout_s: float = 30.0
    max_consecutive_timeouts: int = 3

    @property
    def active_players(self) -> list:
        """Players still in the hand (not folded/eliminated)."""
        return [p for p in self.players.values()
                if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

    @property
    def acting_players(self) -> list:
        """Players who can still make decisions (not all-in, not folded)."""
        return [p for p in self.players.values()
                if p.status == PlayerStatus.ACTIVE]

    def increment_version(self):
        self.version += 1

    def compute_hash(self) -> str:
        """Compute deterministic hash of game state for sync validation."""
        state_dict = {
            "hand_id": self.hand_id,
            "phase": self.phase.value,
            "pot": self.pot,
            "current_bet": self.current_bet,
            "community_cards": self.community_cards,
            "action_on_seat": self.action_on_seat,
            "version": self.version,
            "players": {
                str(seat): {
                    "stack": p.stack,
                    "status": p.status.value,
                    "bet_this_round": p.bet_this_round,
                    "total_bet": p.total_bet,
                }
                for seat, p in sorted(self.players.items())
            },
        }
        serialized = json.dumps(state_dict, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()


# ─── State Machine ────────────────────────────────────────────────────

class PokerStateMachine:
    """
    Manages a single poker hand from blinds through showdown.

    Transition diagram:
        WAITING -> POSTING_BLINDS -> PREFLOP -> FLOP -> TURN -> RIVER -> SHOWDOWN -> HAND_COMPLETE

    At any point if only 1 player remains (all others folded), skip to HAND_COMPLETE.
    """

    # Valid phase transitions
    TRANSITIONS = {
        Phase.WAITING:        [Phase.POSTING_BLINDS],
        Phase.POSTING_BLINDS: [Phase.PREFLOP],
        Phase.PREFLOP:        [Phase.FLOP, Phase.SHOWDOWN, Phase.HAND_COMPLETE],
        Phase.FLOP:           [Phase.TURN, Phase.SHOWDOWN, Phase.HAND_COMPLETE],
        Phase.TURN:           [Phase.RIVER, Phase.SHOWDOWN, Phase.HAND_COMPLETE],
        Phase.RIVER:          [Phase.SHOWDOWN, Phase.HAND_COMPLETE],
        Phase.SHOWDOWN:       [Phase.HAND_COMPLETE],
        Phase.HAND_COMPLETE:  [Phase.WAITING],
    }

    def __init__(self, state: HandState, deal_func=None, evaluate_func=None):
        """
        Args:
            state: The hand state to manage.
            deal_func: Callable(deck, n) -> list of n cards. Injected for RNG certification.
            evaluate_func: Callable(hole_cards, community) -> (rank, description).
        """
        self.state = state
        self._deal = deal_func or self._default_deal
        self._evaluate = evaluate_func or self._default_evaluate
        self._action_seq = 0

    def _transition(self, new_phase: Phase):
        """Validate and execute phase transition."""
        allowed = self.TRANSITIONS.get(self.state.phase, [])
        if new_phase not in allowed:
            raise InvalidTransition(
                f"Cannot transition from {self.state.phase.value} to {new_phase.value}"
            )
        old_phase = self.state.phase
        self.state.phase = new_phase
        self.state.increment_version()
        logger.info(f"[Hand {self.state.hand_id}] {old_phase.value} -> {new_phase.value}")

    # ─── Hand Lifecycle ───────────────────────────────────────────

    def start_hand(self, seated_players: list, dealer_seat: int,
                   small_blind: int, big_blind: int, ante: int = 0, deck: list = None):  # ty:ignore[invalid-parameter-default]
        """
        Initialize and start a new hand.

        Args:
            seated_players: List of Player objects.
            dealer_seat: Seat number of the dealer button.
            small_blind: Small blind amount.
            big_blind: Big blind amount.
            ante: Ante amount (0 if none).
            deck: Pre-shuffled deck (for certified RNG injection).
        """
        if len(seated_players) < 2:
            raise InsufficientPlayers("Need at least 2 players to start a hand")

        # Reset state
        self.state.players = {}
        for p in seated_players:
            p.reset_for_hand()
            self.state.players[p.seat] = p

        self.state.community_cards = []
        self.state.pot = 0
        self.state.current_bet = 0
        self.state.min_raise = big_blind
        self.state.side_pots = []
        self.state.action_history = []
        self.state.dealer_seat = dealer_seat
        self.state.small_blind = small_blind
        self.state.big_blind = big_blind
        self.state.ante = ante
        self.state.deck = deck or []
        self._action_seq = 0

        # Determine blind positions
        seats = sorted(self.state.players.keys())
        dealer_idx = seats.index(dealer_seat)

        if len(seats) == 2:
            # Heads-up: dealer posts SB, other posts BB
            self.state.small_blind_seat = seats[dealer_idx]
            self.state.big_blind_seat = seats[(dealer_idx + 1) % 2]
        else:
            self.state.small_blind_seat = seats[(dealer_idx + 1) % len(seats)]
            self.state.big_blind_seat = seats[(dealer_idx + 2) % len(seats)]

        self._transition(Phase.POSTING_BLINDS)
        self._post_blinds_and_antes()
        self._deal_hole_cards()
        self._transition(Phase.PREFLOP)
        self._set_preflop_action()

    def _post_blinds_and_antes(self):
        """Post blinds and antes, creating forced bets."""
        # Post antes
        if self.state.ante > 0:
            for seat, player in self.state.players.items():
                ante_amount = min(self.state.ante, player.stack)
                player.stack -= ante_amount
                player.total_bet += ante_amount
                self.state.pot += ante_amount

        # Post small blind
        sb_player = self.state.players[self.state.small_blind_seat]
        sb_amount = min(self.state.small_blind, sb_player.stack)
        sb_player.stack -= sb_amount
        sb_player.bet_this_round = sb_amount
        sb_player.total_bet += sb_amount
        self.state.pot += sb_amount
        if sb_player.stack == 0:
            sb_player.status = PlayerStatus.ALL_IN

        # Post big blind
        bb_player = self.state.players[self.state.big_blind_seat]
        bb_amount = min(self.state.big_blind, bb_player.stack)
        bb_player.stack -= bb_amount
        bb_player.bet_this_round = bb_amount
        bb_player.total_bet += bb_amount
        self.state.pot += bb_amount
        self.state.current_bet = bb_amount
        if bb_player.stack == 0:
            bb_player.status = PlayerStatus.ALL_IN

        logger.info(f"[Hand {self.state.hand_id}] Blinds posted: "
                    f"SB={sb_amount} (seat {self.state.small_blind_seat}), "
                    f"BB={bb_amount} (seat {self.state.big_blind_seat}), pot={self.state.pot}")

    def _deal_hole_cards(self):
        """Deal 2 hole cards to each active player."""
        for seat in self._seats_from_dealer():
            player = self.state.players[seat]
            if player.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                player.hole_cards = self._deal(self.state.deck, 2)

    def _set_preflop_action(self):
        """Set action to player left of big blind (UTG) for preflop."""
        seats = self._active_seats()
        bb_idx = seats.index(self.state.big_blind_seat)
        utg_idx = (bb_idx + 1) % len(seats)
        self.state.action_on_seat = seats[utg_idx]
        self.state.action_deadline = time.time() + self.state.action_timeout_s
        self.state.min_raise = self.state.big_blind
        logger.info(f"[Hand {self.state.hand_id}] Action on seat {self.state.action_on_seat}")

    # ─── Action Processing ────────────────────────────────────────

    def process_action(self, seat: int, action: Action, amount: int = 0) -> dict:
        """
        Process a player action. Returns result dict with next state info.

        Args:
            seat: Seat number of acting player.
            action: The action taken.
            amount: Chip amount (for bet/raise).

        Returns:
            dict with keys: valid, error, phase_changed, hand_complete, next_seat

        Raises:
            InvalidAction: If action is not valid in current state.
        """
        if self.state.phase not in (Phase.PREFLOP, Phase.FLOP, Phase.TURN, Phase.RIVER):
            raise InvalidAction(f"Cannot act during {self.state.phase.value}")

        if seat != self.state.action_on_seat:
            raise InvalidAction(f"Not seat {seat}'s turn (action on seat {self.state.action_on_seat})")

        player = self.state.players.get(seat)
        if not player or player.status != PlayerStatus.ACTIVE:
            raise InvalidAction(f"Player at seat {seat} cannot act (status: {player.status.value if player else 'absent'})")

        # Validate and execute action
        valid_actions = self.get_valid_actions(seat)
        action_names = [a["action"] for a in valid_actions]

        if action.value not in action_names:
            raise InvalidAction(
                f"Action '{action.value}' not valid. Valid: {action_names}"
            )

        # Execute the action
        if action == Action.FOLD:
            self._do_fold(player)
        elif action == Action.CHECK:
            self._do_check(player)
        elif action == Action.CALL:
            self._do_call(player)
        elif action == Action.BET:
            self._do_bet(player, amount)
        elif action == Action.RAISE:
            self._do_raise(player, amount)
        elif action == Action.ALL_IN:
            self._do_all_in(player)

        # Record action
        self._action_seq += 1
        record = ActionRecord(
            hand_id=self.state.hand_id,
            phase=self.state.phase.value,
            player_id=player.player_id,
            action=action.value,
            amount=amount,
            pot_after=self.state.pot,
            stack_after=player.stack,
            timestamp=time.time(),
            sequence=self._action_seq,
        )
        self.state.action_history.append(record)
        player.has_acted = True
        player.timeout_count = 0  # Reset timeout counter on valid action

        self.state.increment_version()

        # Check if hand should end (only 1 player left)
        if len(self.state.active_players) == 1:
            return self._complete_hand_single_winner()

        # Check if betting round is complete
        if self._is_round_complete():
            return self._advance_phase()

        # Move to next player
        return self._advance_action()

    def process_timeout(self, seat: int) -> dict:
        """
        Handle action timeout. Auto-check if possible, otherwise auto-fold.

        Args:
            seat: Seat of the timed-out player.

        Returns:
            Same as process_action.
        """
        player = self.state.players.get(seat)
        if not player:
            raise InvalidAction(f"No player at seat {seat}")

        player.timeout_count += 1
        logger.info(f"[Hand {self.state.hand_id}] Timeout for seat {seat} "
                    f"(count: {player.timeout_count})")

        # Auto-check if possible, otherwise auto-fold
        if player.bet_this_round >= self.state.current_bet:
            return self.process_action(seat, Action.CHECK)
        else:
            return self.process_action(seat, Action.FOLD)

    def get_valid_actions(self, seat: int) -> list:
        """
        Get valid actions for a player.

        Returns list of dicts:
            [{"action": "fold"}, {"action": "call", "amount": 200},
             {"action": "raise", "min": 400, "max": 10000}, ...]
        """
        player = self.state.players.get(seat)
        if not player or player.status != PlayerStatus.ACTIVE:
            return []

        actions = [{"action": "fold"}]
        to_call = self.state.current_bet - player.bet_this_round

        if to_call <= 0:
            # No bet to match -> can check
            actions.append({"action": "check"})

            # Can open betting
            if player.stack > 0:
                min_bet = self.state.big_blind
                if player.stack <= min_bet:
                    actions.append({"action": "all_in", "amount": player.stack})
                else:
                    actions.append({
                        "action": "bet",
                        "min": min_bet,
                        "max": player.stack,
                    })  # ty:ignore[invalid-argument-type]
        else:
            # There's a bet to match
            if player.stack <= to_call:
                # Can only call all-in
                actions.append({"action": "all_in", "amount": player.stack})
            else:
                actions.append({"action": "call", "amount": to_call})

                # Can raise
                min_raise_to = self.state.current_bet + self.state.min_raise
                raise_amount = min_raise_to - player.bet_this_round
                if player.stack >= raise_amount:
                    actions.append({
                        "action": "raise",
                        "min": min_raise_to,
                        "max": player.bet_this_round + player.stack,
                    })  # ty:ignore[invalid-argument-type]
                elif player.stack > to_call:
                    # Can only all-in raise
                    actions.append({"action": "all_in", "amount": player.stack})

        return actions

    # ─── Action Implementations ───────────────────────────────────

    def _do_fold(self, player: Player):
        player.status = PlayerStatus.FOLDED
        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) folds")

    def _do_check(self, player: Player):
        if player.bet_this_round < self.state.current_bet:
            raise InvalidAction("Cannot check when there is a bet to match")
        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) checks")

    def _do_call(self, player: Player):
        to_call = self.state.current_bet - player.bet_this_round
        actual_call = min(to_call, player.stack)
        player.stack -= actual_call
        player.bet_this_round += actual_call
        player.total_bet += actual_call
        self.state.pot += actual_call

        if player.stack == 0:
            player.status = PlayerStatus.ALL_IN

        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) "
                    f"calls {actual_call} (pot: {self.state.pot})")

    def _do_bet(self, player: Player, amount: int):
        if self.state.current_bet > 0:
            raise InvalidAction("Cannot bet when there is already a bet (use raise)")
        if amount < self.state.big_blind:
            raise InvalidAction(f"Bet must be at least the big blind ({self.state.big_blind})")
        if amount > player.stack:
            raise InvalidAction(f"Bet {amount} exceeds stack {player.stack}")

        player.stack -= amount
        player.bet_this_round = amount
        player.total_bet += amount
        self.state.pot += amount
        self.state.current_bet = amount
        self.state.min_raise = amount  # Min raise = bet size

        # Reset has_acted for other players (new bet opens new round of action)
        for p in self.state.acting_players:
            if p.seat != player.seat:
                p.has_acted = False

        if player.stack == 0:
            player.status = PlayerStatus.ALL_IN

        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) "
                    f"bets {amount} (pot: {self.state.pot})")

    def _do_raise(self, player: Player, amount: int):
        """Amount is the total raise-to amount (not the increment)."""
        if amount < self.state.current_bet + self.state.min_raise:
            raise InvalidAction(
                f"Raise to {amount} is below minimum raise to "
                f"{self.state.current_bet + self.state.min_raise}"
            )

        raise_increment = amount - self.state.current_bet
        chips_needed = amount - player.bet_this_round

        if chips_needed > player.stack:
            raise InvalidAction(f"Raise requires {chips_needed} chips but stack is {player.stack}")

        player.stack -= chips_needed
        player.bet_this_round = amount
        player.total_bet += chips_needed
        self.state.pot += chips_needed
        self.state.min_raise = raise_increment
        self.state.current_bet = amount

        # Reset has_acted for other players
        for p in self.state.acting_players:
            if p.seat != player.seat:
                p.has_acted = False

        if player.stack == 0:
            player.status = PlayerStatus.ALL_IN

        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) "
                    f"raises to {amount} (pot: {self.state.pot})")

    def _do_all_in(self, player: Player):
        amount = player.stack
        total_bet = player.bet_this_round + amount

        if total_bet > self.state.current_bet:
            # All-in raise
            raise_increment = total_bet - self.state.current_bet
            if raise_increment >= self.state.min_raise:
                self.state.min_raise = raise_increment
            self.state.current_bet = total_bet

            # Reset has_acted for other players
            for p in self.state.acting_players:
                if p.seat != player.seat:
                    p.has_acted = False

        player.total_bet += amount
        player.bet_this_round += amount
        player.stack = 0
        player.status = PlayerStatus.ALL_IN
        self.state.pot += amount

        logger.info(f"[Hand {self.state.hand_id}] Seat {player.seat} ({player.player_id}) "
                    f"all-in for {amount} (total: {total_bet}, pot: {self.state.pot})")

    # ─── Round / Phase Management ─────────────────────────────────

    def _is_round_complete(self) -> bool:
        """Check if current betting round is complete."""
        acting = self.state.acting_players
        if not acting:
            return True
        return all(
            p.has_acted and p.bet_this_round >= self.state.current_bet
            for p in acting
        )

    def _advance_action(self) -> dict:
        """Move action to the next active player."""
        seats = self._active_seats()
        if not seats:
            return self._advance_phase()

        current_idx = seats.index(self.state.action_on_seat) if self.state.action_on_seat in seats else -1
        next_idx = (current_idx + 1) % len(seats)
        next_seat = seats[next_idx]

        # Skip players who are all-in
        attempts = 0
        while self.state.players[next_seat].status != PlayerStatus.ACTIVE and attempts < len(seats):
            next_idx = (next_idx + 1) % len(seats)
            next_seat = seats[next_idx]
            attempts += 1

        if attempts >= len(seats):
            return self._advance_phase()

        self.state.action_on_seat = next_seat
        self.state.action_deadline = time.time() + self.state.action_timeout_s

        return {
            "valid": True,
            "phase_changed": False,
            "hand_complete": False,
            "next_seat": next_seat,
            "phase": self.state.phase.value,
        }

    def _advance_phase(self) -> dict:
        """Advance to the next phase (deal community cards)."""
        # Reset for new betting round
        for p in self.state.players.values():
            p.reset_for_round()
        self.state.current_bet = 0
        self.state.min_raise = self.state.big_blind

        # Check if all remaining players are all-in (run out the board)
        all_in_or_folded = len(self.state.acting_players) <= 1

        next_phases = {
            Phase.PREFLOP: (Phase.FLOP, 3),
            Phase.FLOP: (Phase.TURN, 1),
            Phase.TURN: (Phase.RIVER, 1),
            Phase.RIVER: (Phase.SHOWDOWN, 0),
        }

        if self.state.phase in next_phases:
            next_phase, cards_to_deal = next_phases[self.state.phase]

            if cards_to_deal > 0:
                # Burn one card, deal community
                self._deal(self.state.deck, 1)  # burn
                new_cards = self._deal(self.state.deck, cards_to_deal)
                self.state.community_cards.extend(new_cards)
                logger.info(f"[Hand {self.state.hand_id}] Community: {self.state.community_cards}")

            if next_phase == Phase.SHOWDOWN or all_in_or_folded:
                # Run out remaining cards if needed
                if all_in_or_folded and next_phase != Phase.SHOWDOWN:
                    while len(self.state.community_cards) < 5:
                        self._deal(self.state.deck, 1)  # burn
                        card = self._deal(self.state.deck, 1)
                        self.state.community_cards.extend(card)
                    logger.info(f"[Hand {self.state.hand_id}] Board run out: {self.state.community_cards}")

                self._transition(Phase.SHOWDOWN)
                return self._resolve_showdown()

            self._transition(next_phase)

            # Set action to first active player after dealer
            seats = [s for s in self._seats_from_dealer()
                     if self.state.players[s].status == PlayerStatus.ACTIVE]
            if seats:
                self.state.action_on_seat = seats[0]
                self.state.action_deadline = time.time() + self.state.action_timeout_s
            else:
                # All players all-in, run the board
                return self._advance_phase()

            return {
                "valid": True,
                "phase_changed": True,
                "hand_complete": False,
                "next_seat": self.state.action_on_seat,
                "phase": self.state.phase.value,
                "community_cards": self.state.community_cards,
            }

        # Should not reach here
        return self._resolve_showdown()

    def _complete_hand_single_winner(self) -> dict:
        """Complete hand when all but one player have folded."""
        winner = self.state.active_players[0]
        winner.stack += self.state.pot

        self._transition(Phase.HAND_COMPLETE)

        logger.info(f"[Hand {self.state.hand_id}] Winner by fold: seat {winner.seat} "
                    f"({winner.player_id}) wins {self.state.pot}")

        return {
            "valid": True,
            "phase_changed": True,
            "hand_complete": True,
            "phase": Phase.HAND_COMPLETE.value,
            "winners": [{
                "player_id": winner.player_id,
                "seat": winner.seat,
                "amount": self.state.pot,
                "hand_rank": None,
                "hand_description": "Last player standing",
            }],
            "pots": [{"amount": self.state.pot, "winner": winner.player_id}],
        }

    def _resolve_showdown(self) -> dict:
        """Evaluate hands and distribute pots at showdown."""
        calculate_side_pots = _get_side_pot_calculator().calculate_side_pots

        active = self.state.active_players
        if not active:
            self._transition(Phase.HAND_COMPLETE)
            return {"valid": True, "hand_complete": True, "phase": Phase.HAND_COMPLETE.value, "winners": []}

        # Calculate side pots
        pots = calculate_side_pots(
            [(p.player_id, p.seat, p.total_bet, p.status != PlayerStatus.FOLDED)
             for p in self.state.players.values()]
        )

        # Evaluate hands
        hand_ranks = {}
        for p in active:
            rank, description = self._evaluate(p.hole_cards, self.state.community_cards)
            hand_ranks[p.player_id] = {
                "player_id": p.player_id,
                "seat": p.seat,
                "rank": rank,
                "description": description,
                "hole_cards": p.hole_cards,
            }

        # Award pots
        winners = []
        pot_results = []
        for pot in pots:
            eligible = [pid for pid in pot["eligible_players"] if pid in hand_ranks]
            if not eligible:
                continue

            # Find best hand among eligible
            eligible.sort(key=lambda pid: hand_ranks[pid]["rank"])
            best_rank = hand_ranks[eligible[0]]["rank"]
            pot_winners = [pid for pid in eligible if hand_ranks[pid]["rank"] == best_rank]
            # Odd-chip rule: on a tie, the extra unit goes to the first
            # winner seated to the left of the dealer button, not to
            # whichever player happens to sort first by total_bet/seat.
            pot_winners = self._odd_chip_order(pot_winners)

            # Split pot among winners
            share = pot["amount"] // len(pot_winners)
            remainder = pot["amount"] % len(pot_winners)

            for i, pid in enumerate(pot_winners):
                award = share + (1 if i < remainder else 0)
                player = next(p for p in self.state.players.values() if p.player_id == pid)
                player.stack += award

                winners.append({
                    "player_id": pid,
                    "seat": player.seat,
                    "amount": award,
                    "hand_rank": hand_ranks[pid]["rank"],
                    "hand_description": hand_ranks[pid]["description"],
                })

            pot_results.append({
                "amount": pot["amount"],
                "winners": pot_winners,
                "eligible": eligible,
            })

        self._transition(Phase.HAND_COMPLETE)

        logger.info(f"[Hand {self.state.hand_id}] Showdown complete: "
                    f"{len(winners)} winner(s), {len(pot_results)} pot(s)")

        return {
            "valid": True,
            "phase_changed": True,
            "hand_complete": True,
            "phase": Phase.HAND_COMPLETE.value,
            "winners": winners,
            "pots": pot_results,
            "showdown_hands": hand_ranks,
        }

    # ─── Helpers ──────────────────────────────────────────────────

    def _odd_chip_order(self, pids: list) -> list:
        """Order tied showdown winners starting from the first eligible seat
        to the left of the dealer button -- the traditional odd-chip
        recipient. ``share + 1`` awards go to the front of this ordering."""
        seats = sorted(self.state.players.keys())
        n = len(seats)
        dealer_idx = seats.index(self.state.dealer_seat) if self.state.dealer_seat in seats else 0
        pid_to_seat = {p.player_id: p.seat for p in self.state.players.values()}

        def seat_order(pid: str) -> int:
            seat_idx = seats.index(pid_to_seat[pid])
            return (seat_idx - dealer_idx - 1) % n

        return sorted(pids, key=seat_order)

    def _seats_from_dealer(self) -> list:
        """Return seat list starting from seat after dealer (clockwise)."""
        seats = sorted(self.state.players.keys())
        if not seats:
            return []
        dealer_idx = seats.index(self.state.dealer_seat) if self.state.dealer_seat in seats else 0
        n = len(seats)
        return [seats[(dealer_idx + 1 + i) % n] for i in range(n)]

    def _active_seats(self) -> list:
        """Return seats of players still in hand (active or all-in), in order."""
        seats = sorted(self.state.players.keys())
        return [s for s in seats
                if self.state.players[s].status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)]

    @staticmethod
    def _default_deal(deck: list, n: int) -> list:
        """Default deal: pop from end of deck."""
        if len(deck) < n:
            raise DeckExhausted(f"Need {n} cards but only {len(deck)} remain")
        cards = deck[-n:]
        del deck[-n:]
        return cards

    @staticmethod
    def _default_evaluate(hole_cards: list, community: list) -> tuple:
        """
        Placeholder hand evaluator. In production, use a proper poker hand evaluator
        (e.g., treys, pokereval, or a custom C extension).

        Returns (rank, description) where lower rank is better.
        """
        # This is a stub - production systems use optimized evaluators
        import random
        return (random.randint(1, 7462), "Evaluated hand")


# ─── Exceptions ───────────────────────────────────────────────────────

class InvalidTransition(Exception):
    pass

class InvalidAction(Exception):
    pass

class InsufficientPlayers(Exception):
    pass

class DeckExhausted(Exception):
    pass


# ─── Example Usage ────────────────────────────────────────────────────

if __name__ == "__main__":
    # Create a standard 52-card deck
    suits = ["h", "d", "c", "s"]
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    deck = [f"{r}{s}" for s in suits for r in ranks]
    import random
    random.shuffle(deck)

    # Setup hand state
    state = HandState(
        hand_id="HAND-000001",
        table_id="TABLE-001",
        small_blind=50,
        big_blind=100,
    )

    # Create players
    players = [
        Player(player_id="player_1", seat=1, stack=10000),
        Player(player_id="player_2", seat=3, stack=8000),
        Player(player_id="player_3", seat=5, stack=12000),
        Player(player_id="player_4", seat=7, stack=6000),
        Player(player_id="player_5", seat=9, stack=9500),
    ]

    sm = PokerStateMachine(state)
    sm.start_hand(players, dealer_seat=1, small_blind=50, big_blind=100, deck=deck)

    print(f"\nPhase: {state.phase.value}")
    print(f"Pot: {state.pot}")
    print(f"Action on seat: {state.action_on_seat}")

    # Show valid actions for current player
    valid = sm.get_valid_actions(state.action_on_seat)
    print(f"Valid actions for seat {state.action_on_seat}: {valid}")

    # Simulate UTG raise
    result = sm.process_action(state.action_on_seat, Action.RAISE, 250)
    print(f"\nAfter raise: phase={result['phase']}, next_seat={result.get('next_seat')}")

    # Next player calls
    result = sm.process_action(state.action_on_seat, Action.CALL)
    print(f"After call: phase={result['phase']}, next_seat={result.get('next_seat')}")

    # Next player folds
    result = sm.process_action(state.action_on_seat, Action.FOLD)
    print(f"After fold: phase={result['phase']}, next_seat={result.get('next_seat')}")

    print(f"\nState version: {state.version}")
    print(f"State hash: {state.compute_hash()[:16]}...")
    print(f"Action history: {len(state.action_history)} actions")
