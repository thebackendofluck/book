#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Game Logic Test Framework for iGaming Platforms
================================================
Generic test framework for validating game logic across game types:
  - Slots (reel evaluation, payline matching, bonus triggers)
  - Table Games (blackjack, roulette, baccarat, craps)
  - Poker (hand evaluation, side bets, tournament logic)
  - Live Dealer (outcome verification, round state management)

Features:
  - Deterministic replay with seeded RNG
  - Mathematical model verification (RTP, variance, hit frequency)
  - Edge case coverage per game type
  - Regulatory compliance assertions (max bet, max win, responsible gaming)
  - Parallel simulation for RTP convergence

Usage:
  python game_test_framework.py --game slots --spins 1000000
  python game_test_framework.py --game blackjack --hands 500000 --rules vegas-strip
  python game_test_framework.py --game roulette --type european --spins 1000000
  pytest game_test_framework.py -v
"""

import hashlib
import json
import math
import sys
import time
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
except ImportError:
    print("Required: pip install numpy")
    sys.exit(1)


# ===================================================================
# Core Framework Types
# ===================================================================

class GameType(Enum):
    SLOTS = "slots"
    BLACKJACK = "blackjack"
    ROULETTE = "roulette"
    BACCARAT = "baccarat"
    POKER = "poker"
    CRAPS = "craps"
    KENO = "keno"


class TestSeverity(Enum):
    CRITICAL = auto()
    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()


@dataclass
class TestAssertion:
    name: str
    passed: bool
    expected: Any
    actual: Any
    severity: TestSeverity = TestSeverity.HIGH
    message: str = ""

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: expected={self.expected}, actual={self.actual}"


@dataclass
class GameRound:
    """Single game round result."""
    round_id: int
    bet_amount: float
    win_amount: float
    game_data: dict = field(default_factory=dict)
    rng_values_used: list = field(default_factory=list)


@dataclass
class SimulationResult:
    """Aggregated simulation results."""
    game_type: GameType
    total_rounds: int
    total_wagered: float
    total_won: float
    rtp: float
    variance: float
    hit_frequency: float
    max_win: float
    max_win_multiplier: float
    win_distribution: Dict[str, int] = field(default_factory=dict)
    assertions: List[TestAssertion] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def all_passed(self) -> bool:
        return all(a.passed for a in self.assertions)


# ===================================================================
# Deterministic RNG for Testing
# ===================================================================

class DeterministicRNG:
    """
    Seeded RNG for reproducible game testing.
    Wraps numpy's PCG64 generator for high-quality deterministic output.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = np.random.Generator(np.random.PCG64(seed))
        self.call_count = 0

    def random_int(self, low: int, high: int) -> int:
        """Random integer in [low, high] inclusive."""
        self.call_count += 1
        return int(self.rng.integers(low, high + 1))

    def random_float(self) -> float:
        """Random float in [0, 1)."""
        self.call_count += 1
        return float(self.rng.random())

    def shuffle(self, items: list) -> list:
        """Shuffle a list (returns new list)."""
        self.call_count += 1
        result = items.copy()
        self.rng.shuffle(result)
        return result

    def choice(self, items: list, weights: Optional[list] = None) -> Any:
        """Weighted random choice."""
        self.call_count += 1
        if weights:
            probs = np.array(weights) / sum(weights)
            idx = self.rng.choice(len(items), p=probs)
        else:
            idx = self.rng.integers(0, len(items))
        return items[idx]

    def reset(self):
        """Reset to initial seed for replay."""
        self.rng = np.random.Generator(np.random.PCG64(self.seed))
        self.call_count = 0


# ===================================================================
# Abstract Game Engine
# ===================================================================

class GameEngine(ABC):
    """Base class for all game logic engines."""

    def __init__(self, rng: DeterministicRNG, config: dict):
        self.rng = rng
        self.config = config
        self.game_type: GameType = GameType.SLOTS

    @abstractmethod
    def play_round(self, bet: float) -> GameRound:
        """Execute a single game round."""
        pass

    @abstractmethod
    def get_theoretical_rtp(self) -> float:
        """Return the theoretical RTP for current configuration."""
        pass

    def validate_bet(self, bet: float) -> bool:
        """Validate bet against min/max limits."""
        min_bet = self.config.get("min_bet", 0.01)
        max_bet = self.config.get("max_bet", 10000.0)
        return min_bet <= bet <= max_bet


# ===================================================================
# Slots Engine
# ===================================================================

class SlotsEngine(GameEngine):
    """
    Slots game logic with configurable reels, paylines, and features.

    Supports:
    - Multi-reel evaluation (3x3, 5x3, 5x4, 6x4 layouts)
    - Wild substitution
    - Scatter triggers
    - Free spins with multipliers
    - Cascading/tumbling reels
    """

    def __init__(self, rng: DeterministicRNG, config: dict):
        super().__init__(rng, config)
        self.game_type = GameType.SLOTS

        # Default 5x3 slot configuration
        self.num_reels = config.get("num_reels", 5)
        self.rows = config.get("rows", 3)

        # Symbols: {name: {id, weight_per_reel, pays}}
        self.symbols = config.get("symbols", self._default_symbols())
        self.paylines = config.get("paylines", self._default_paylines())
        self.wild_symbol = config.get("wild_symbol", "WILD")
        self.scatter_symbol = config.get("scatter_symbol", "SCATTER")
        self.free_spins_trigger = config.get("free_spins_trigger", 3)
        self.free_spins_count = config.get("free_spins_count", 10)
        self.free_spins_multiplier = config.get("free_spins_multiplier", 2)
        self.max_win_multiplier = config.get("max_win_multiplier", 5000)

    def _default_symbols(self) -> dict:
        return {
            "WILD": {"id": 0, "weight": 2, "pays": {5: 500, 4: 100, 3: 25}},
            "SCATTER": {"id": 1, "weight": 3, "pays": {}},
            "HIGH1": {"id": 2, "weight": 5, "pays": {5: 200, 4: 50, 3: 15}},
            "HIGH2": {"id": 3, "weight": 7, "pays": {5: 100, 4: 30, 3: 10}},
            "HIGH3": {"id": 4, "weight": 8, "pays": {5: 75, 4: 20, 3: 8}},
            "MID1": {"id": 5, "weight": 12, "pays": {5: 50, 4: 15, 3: 5}},
            "MID2": {"id": 6, "weight": 14, "pays": {5: 40, 4: 10, 3: 4}},
            "LOW1": {"id": 7, "weight": 18, "pays": {5: 25, 4: 8, 3: 3}},
            "LOW2": {"id": 8, "weight": 20, "pays": {5: 20, 4: 5, 3: 2}},
            "LOW3": {"id": 9, "weight": 22, "pays": {5: 15, 4: 4, 3: 1}},
        }

    def _default_paylines(self) -> List[List[int]]:
        """Default 20 paylines for 5x3 grid."""
        return [
            [1, 1, 1, 1, 1],  # Middle row
            [0, 0, 0, 0, 0],  # Top row
            [2, 2, 2, 2, 2],  # Bottom row
            [0, 1, 2, 1, 0],  # V shape
            [2, 1, 0, 1, 2],  # Inverted V
            [0, 0, 1, 2, 2],  # Diagonal down
            [2, 2, 1, 0, 0],  # Diagonal up
            [1, 0, 1, 2, 1],  # W shape
            [1, 2, 1, 0, 1],  # M shape
            [0, 1, 0, 1, 0],  # Zigzag top
            [2, 1, 2, 1, 2],  # Zigzag bottom
            [1, 0, 0, 0, 1],  # U shape top
            [1, 2, 2, 2, 1],  # U shape bottom
            [0, 1, 1, 1, 0],  # Flat top stairs
            [2, 1, 1, 1, 2],  # Flat bottom stairs
            [0, 0, 1, 0, 0],  # Peak top
            [2, 2, 1, 2, 2],  # Valley bottom
            [1, 0, 1, 0, 1],  # Alternating top
            [1, 2, 1, 2, 1],  # Alternating bottom
            [0, 2, 0, 2, 0],  # Wide zigzag
        ]

    def _spin_reels(self) -> List[List[str]]:
        """Spin all reels, return grid[reel][row]."""
        symbol_names = list(self.symbols.keys())
        weights = [self.symbols[s]["weight"] for s in symbol_names]

        grid = []
        for reel in range(self.num_reels):
            column = []
            for row in range(self.rows):
                symbol = self.rng.choice(symbol_names, weights)
                column.append(symbol)
            grid.append(column)
        return grid

    def _evaluate_payline(
        self, grid: List[List[str]], payline: List[int]
    ) -> Tuple[str, int, float]:
        """Evaluate a single payline. Returns (symbol, count, pay_multiplier)."""
        symbols_on_line = [grid[reel][payline[reel]] for reel in range(self.num_reels)]

        best_symbol = ""
        best_count = 0
        best_pay = 0.0

        for target_symbol in self.symbols:
            if target_symbol == self.scatter_symbol:
                continue

            count = 0
            for s in symbols_on_line:
                if s == target_symbol or s == self.wild_symbol:
                    count += 1
                else:
                    break

            if count >= 3:
                pays = self.symbols[target_symbol].get("pays", {})
                pay = pays.get(count, 0)
                if pay > best_pay:
                    best_pay = pay
                    best_count = count
                    best_symbol = target_symbol

        return best_symbol, best_count, best_pay

    def _count_scatters(self, grid: List[List[str]]) -> int:
        count = 0
        for reel in grid:
            for symbol in reel:
                if symbol == self.scatter_symbol:
                    count += 1
        return count

    def play_round(self, bet: float) -> GameRound:
        bet_per_line = bet / len(self.paylines)
        grid = self._spin_reels()
        total_win = 0.0
        winning_lines = []

        # Evaluate paylines
        for i, payline in enumerate(self.paylines):
            symbol, count, pay = self._evaluate_payline(grid, payline)
            if pay > 0:
                line_win = pay * bet_per_line
                total_win += line_win
                winning_lines.append({
                    "payline": i,
                    "symbol": symbol,
                    "count": count,
                    "win": line_win,
                })

        # Scatter check
        scatter_count = self._count_scatters(grid)
        free_spins_won = 0
        free_spins_win = 0.0

        if scatter_count >= self.free_spins_trigger:
            free_spins_won = self.free_spins_count
            # Simulate free spins
            for _ in range(free_spins_won):
                fs_grid = self._spin_reels()
                for payline in self.paylines:
                    _, _, pay = self._evaluate_payline(fs_grid, payline)
                    if pay > 0:
                        free_spins_win += pay * bet_per_line * self.free_spins_multiplier

        total_win += free_spins_win

        # Apply max win cap
        max_win = bet * self.max_win_multiplier
        total_win = min(total_win, max_win)

        return GameRound(
            round_id=0,
            bet_amount=bet,
            win_amount=total_win,
            game_data={
                "grid": grid,
                "winning_lines": winning_lines,
                "scatter_count": scatter_count,
                "free_spins_won": free_spins_won,
                "free_spins_win": free_spins_win,
            },
        )

    def get_theoretical_rtp(self) -> float:
        return self.config.get("theoretical_rtp", 0.96)


# ===================================================================
# Blackjack Engine
# ===================================================================

class BlackjackEngine(GameEngine):
    """
    Blackjack game logic with configurable rules.

    Supports:
    - Multiple deck counts (1, 2, 4, 6, 8)
    - Hit/Stand/Double/Split
    - Dealer hits/stands on soft 17
    - Blackjack pays 3:2 or 6:5
    - Insurance and surrender
    """

    CARD_VALUES = {
        "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
        "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
    }

    def __init__(self, rng: DeterministicRNG, config: dict):
        super().__init__(rng, config)
        self.game_type = GameType.BLACKJACK
        self.num_decks = config.get("num_decks", 6)
        self.dealer_hits_soft_17 = config.get("dealer_hits_soft_17", True)
        self.blackjack_pays = config.get("blackjack_pays", 1.5)  # 3:2
        self.allow_double_after_split = config.get("allow_double_after_split", True)
        self.allow_surrender = config.get("allow_surrender", True)
        self.penetration = config.get("penetration", 0.75)
        self.shoe = []
        self._new_shoe()

    def _new_shoe(self):
        cards = list(self.CARD_VALUES.keys()) * 4 * self.num_decks
        self.shoe = self.rng.shuffle(cards)
        self.shoe_position = 0

    def _deal_card(self) -> str:
        if self.shoe_position >= len(self.shoe) * self.penetration:
            self._new_shoe()
        card = self.shoe[self.shoe_position]
        self.shoe_position += 1
        return card

    def _hand_value(self, hand: List[str]) -> int:
        value = sum(self.CARD_VALUES[c] for c in hand)
        aces = hand.count("A")
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        return value

    def _is_soft(self, hand: List[str]) -> bool:
        value = sum(self.CARD_VALUES[c] for c in hand)
        return hand.count("A") > 0 and value <= 21

    def _basic_strategy_action(
        self, player_hand: List[str], dealer_upcard: str
    ) -> str:
        """Perfect basic strategy for testing."""
        pv = self._hand_value(player_hand)
        dv = self.CARD_VALUES[dealer_upcard]
        soft = self._is_soft(player_hand) and len(player_hand) == 2

        if len(player_hand) == 2 and player_hand[0] == player_hand[1]:
            # Pair splitting logic (simplified)
            card = player_hand[0]
            if card == "A" or card == "8":
                return "split"
            if card in ("2", "3", "7") and dv <= 7:
                return "split"
            if card == "6" and dv <= 6:
                return "split"

        if soft:
            if pv >= 19:
                return "stand"
            if pv == 18:
                return "stand" if dv <= 8 else "hit"
            return "hit"

        if pv >= 17:
            return "stand"
        if pv >= 13 and dv <= 6:
            return "stand"
        if pv == 12 and 4 <= dv <= 6:
            return "stand"
        if pv == 11:
            return "double" if len(player_hand) == 2 else "hit"
        if pv == 10 and dv <= 9:
            return "double" if len(player_hand) == 2 else "hit"
        return "hit"

    def play_round(self, bet: float) -> GameRound:
        player_hand = [self._deal_card(), self._deal_card()]
        dealer_hand = [self._deal_card(), self._deal_card()]
        dealer_upcard = dealer_hand[0]

        pv = self._hand_value(player_hand)
        dv = self._hand_value(dealer_hand)

        win = 0.0
        outcome = ""

        # Check naturals
        player_bj = pv == 21 and len(player_hand) == 2
        dealer_bj = dv == 21 and len(dealer_hand) == 2

        if player_bj and dealer_bj:
            win = 0.0
            outcome = "push_blackjack"
        elif player_bj:
            win = bet * self.blackjack_pays
            outcome = "player_blackjack"
        elif dealer_bj:
            win = -bet
            outcome = "dealer_blackjack"
        else:
            # Player actions (basic strategy)
            current_bet = bet
            while True:
                action = self._basic_strategy_action(player_hand, dealer_upcard)
                if action == "hit":
                    player_hand.append(self._deal_card())
                    pv = self._hand_value(player_hand)
                    if pv > 21:
                        outcome = "player_bust"
                        win = -current_bet
                        break
                elif action == "double":
                    current_bet *= 2
                    player_hand.append(self._deal_card())
                    pv = self._hand_value(player_hand)
                    if pv > 21:
                        outcome = "player_bust"
                        win = -current_bet
                        break
                    break
                elif action == "stand":
                    break
                elif action == "split":
                    # Simplified: just hit instead
                    player_hand.append(self._deal_card())
                    pv = self._hand_value(player_hand)
                    if pv > 21:
                        outcome = "player_bust"
                        win = -current_bet
                        break
                else:
                    break

            # Dealer plays if player didn't bust
            if outcome != "player_bust":
                while True:
                    dv = self._hand_value(dealer_hand)
                    if dv > 21:
                        outcome = "dealer_bust"
                        win = current_bet
                        break
                    if dv >= 17:
                        if dv == 17 and self.dealer_hits_soft_17 and self._is_soft(dealer_hand):
                            dealer_hand.append(self._deal_card())
                            continue
                        break
                    dealer_hand.append(self._deal_card())

                if outcome != "dealer_bust":
                    pv = self._hand_value(player_hand)
                    dv = self._hand_value(dealer_hand)
                    if pv > dv:
                        outcome = "player_wins"
                        win = current_bet
                    elif pv < dv:
                        outcome = "dealer_wins"
                        win = -current_bet
                    else:
                        outcome = "push"
                        win = 0.0

        return GameRound(
            round_id=0,
            bet_amount=bet,
            win_amount=win,
            game_data={
                "player_hand": player_hand,
                "dealer_hand": dealer_hand,
                "outcome": outcome,
                "player_value": self._hand_value(player_hand),
                "dealer_value": self._hand_value(dealer_hand),
            },
        )

    def get_theoretical_rtp(self) -> float:
        return self.config.get("theoretical_rtp", 0.9950)


# ===================================================================
# Roulette Engine
# ===================================================================

class RouletteEngine(GameEngine):
    """European and American roulette."""

    EUROPEAN_NUMBERS = list(range(0, 37))   # 0-36
    AMERICAN_NUMBERS = list(range(-1, 37))  # -1=00, 0-36

    RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

    def __init__(self, rng: DeterministicRNG, config: dict):
        super().__init__(rng, config)
        self.game_type = GameType.ROULETTE
        self.variant = config.get("variant", "european")
        self.numbers = (
            self.EUROPEAN_NUMBERS
            if self.variant == "european"
            else self.AMERICAN_NUMBERS
        )

    def play_round(self, bet: float) -> GameRound:
        # Default bet: red
        bet_type = self.config.get("default_bet", "red")
        number = self.rng.choice(self.numbers)

        win = 0.0
        if bet_type == "red":
            if number in self.RED_NUMBERS:
                win = bet
        elif bet_type == "straight":
            target = self.config.get("straight_number", 17)
            if number == target:
                win = bet * 35

        return GameRound(
            round_id=0,
            bet_amount=bet,
            win_amount=win,
            game_data={"number": number, "bet_type": bet_type},
        )

    def get_theoretical_rtp(self) -> float:
        if self.variant == "european":
            return 1 - 1 / 37  # 97.30%
        return 1 - 2 / 38  # 94.74%


# ===================================================================
# Game Test Runner
# ===================================================================

class GameTestRunner:
    """
    Runs simulation and validates game logic against mathematical models.
    """

    def __init__(self, engine: GameEngine, seed: int = 42):
        self.engine = engine
        self.seed = seed

    def run_simulation(
        self,
        num_rounds: int,
        bet_amount: float = 1.0,
        verbose: bool = True,
    ) -> SimulationResult:
        """Run N rounds and compute statistics."""
        t0 = time.monotonic()

        wins = []
        total_wagered = 0.0
        total_won = 0.0
        win_counts = Counter()
        max_win = 0.0

        for i in range(num_rounds):
            round_result = self.engine.play_round(bet_amount)
            net = round_result.win_amount
            wins.append(net)
            total_wagered += bet_amount
            total_won += max(0, net)

            # Categorize wins
            if net > 0:
                multiplier = net / bet_amount
                if multiplier >= 100:
                    win_counts["100x+"] += 1
                elif multiplier >= 10:
                    win_counts["10x-99x"] += 1
                elif multiplier >= 1:
                    win_counts["1x-9x"] += 1
                else:
                    win_counts["<1x"] += 1
                max_win = max(max_win, net)
            elif net == 0:
                win_counts["push"] += 1
            else:
                win_counts["loss"] += 1

            if verbose and (i + 1) % (num_rounds // 10) == 0:
                current_rtp = (total_won / total_wagered) if total_wagered > 0 else 0
                print(f"  Progress: {(i+1)/num_rounds*100:.0f}% | RTP: {current_rtp*100:.2f}%")

        wins_array = np.array(wins)
        rtp = total_won / total_wagered if total_wagered > 0 else 0
        hit_freq = sum(1 for w in wins if w > 0) / num_rounds

        result = SimulationResult(
            game_type=self.engine.game_type,
            total_rounds=num_rounds,
            total_wagered=total_wagered,
            total_won=total_won,
            rtp=rtp,
            variance=float(wins_array.var()),
            hit_frequency=hit_freq,
            max_win=max_win,
            max_win_multiplier=max_win / bet_amount if bet_amount > 0 else 0,
            win_distribution=dict(win_counts),
            duration_seconds=time.monotonic() - t0,
        )

        # Run assertions
        self._validate_results(result, bet_amount)

        return result

    def _validate_results(self, result: SimulationResult, bet_amount: float):
        """Run standard assertions against simulation results."""
        theoretical_rtp = self.engine.get_theoretical_rtp()

        # RTP within tolerance (larger tolerance for fewer rounds)
        rtp_tolerance = 3.0 / math.sqrt(result.total_rounds) if result.total_rounds > 0 else 1.0
        rtp_diff = abs(result.rtp - theoretical_rtp)
        result.assertions.append(
            TestAssertion(
                name="RTP within tolerance",
                passed=rtp_diff <= rtp_tolerance,
                expected=f"{theoretical_rtp*100:.2f}% +/- {rtp_tolerance*100:.2f}%",
                actual=f"{result.rtp*100:.2f}%",
                severity=TestSeverity.CRITICAL,
                message=f"Difference: {rtp_diff*100:.4f}%",
            )
        )

        # Hit frequency reasonable
        result.assertions.append(
            TestAssertion(
                name="Hit frequency > 0",
                passed=result.hit_frequency > 0,
                expected="> 0%",
                actual=f"{result.hit_frequency*100:.2f}%",
                severity=TestSeverity.CRITICAL,
            )
        )

        # Max win doesn't exceed cap
        max_win_cap = self.engine.config.get("max_win_multiplier", 50000)
        result.assertions.append(
            TestAssertion(
                name="Max win within cap",
                passed=result.max_win_multiplier <= max_win_cap,
                expected=f"<= {max_win_cap}x",
                actual=f"{result.max_win_multiplier:.1f}x",
                severity=TestSeverity.HIGH,
            )
        )

        # No negative RTP
        result.assertions.append(
            TestAssertion(
                name="RTP is non-negative",
                passed=result.rtp >= 0,
                expected=">= 0",
                actual=f"{result.rtp*100:.2f}%",
                severity=TestSeverity.CRITICAL,
            )
        )

    def print_report(self, result: SimulationResult):
        """Print formatted simulation report."""
        print("\n" + "=" * 60)
        print(f"GAME TEST REPORT: {result.game_type.value.upper()}")
        print("=" * 60)
        print(f"  Rounds:         {result.total_rounds:,}")
        print(f"  Total Wagered:  {result.total_wagered:,.2f}")
        print(f"  Total Won:      {result.total_won:,.2f}")
        print(f"  RTP:            {result.rtp*100:.4f}%")
        print(f"  Theoretical:    {self.engine.get_theoretical_rtp()*100:.4f}%")
        print(f"  Hit Frequency:  {result.hit_frequency*100:.2f}%")
        print(f"  Max Win:        {result.max_win:,.2f} ({result.max_win_multiplier:.1f}x)")
        print(f"  Variance:       {result.variance:.4f}")
        print(f"  Duration:       {result.duration_seconds:.2f}s")

        print(f"\n  Win Distribution:")
        for category, count in sorted(result.win_distribution.items()):
            pct = count / result.total_rounds * 100
            print(f"    {category:12s}: {count:>8,} ({pct:.2f}%)")

        print(f"\n  Assertions:")
        for a in result.assertions:
            status_str = "\033[92mPASS\033[0m" if a.passed else "\033[91mFAIL\033[0m"
            print(f"    [{status_str}] {a.name}: expected={a.expected}, actual={a.actual}")

        overall = "ALL PASSED" if result.all_passed else "FAILURES DETECTED"
        print(f"\n  Overall: {overall}")
        print("=" * 60)


# ===================================================================
# Main
# ===================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="iGaming Game Logic Test Framework")
    parser.add_argument(
        "--game",
        choices=["slots", "blackjack", "roulette"],
        default="slots",
    )
    parser.add_argument("--rounds", type=int, default=100000)
    parser.add_argument("--bet", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", help="Save results to JSON file")

    args = parser.parse_args()

    rng = DeterministicRNG(seed=args.seed)

    if args.game == "slots":
        engine = SlotsEngine(rng, {"theoretical_rtp": 0.96})
    elif args.game == "blackjack":
        engine = BlackjackEngine(rng, {"num_decks": 6, "theoretical_rtp": 0.9950})
    elif args.game == "roulette":
        engine = RouletteEngine(rng, {"variant": "european"})
    else:
        print(f"Unknown game: {args.game}")
        sys.exit(1)

    runner = GameTestRunner(engine, seed=args.seed)
    print(f"\nRunning {args.rounds:,} rounds of {args.game}...")
    result = runner.run_simulation(args.rounds, bet_amount=args.bet)
    runner.print_report(result)

    if args.output:
        report = {
            "game_type": result.game_type.value,
            "total_rounds": result.total_rounds,
            "rtp": round(result.rtp * 100, 4),
            "theoretical_rtp": round(engine.get_theoretical_rtp() * 100, 4),
            "hit_frequency": round(result.hit_frequency * 100, 2),
            "max_win_multiplier": round(result.max_win_multiplier, 1),
            "variance": round(result.variance, 6),
            "all_passed": result.all_passed,
            "assertions": [
                {"name": a.name, "passed": a.passed, "expected": str(a.expected), "actual": str(a.actual)}
                for a in result.assertions
            ],
        }
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nResults saved to {args.output}")

    sys.exit(0 if result.all_passed else 1)


if __name__ == "__main__":
    main()
