# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Game-Specific RNG Adapters for Casino Games

This module provides specialized RNG wrappers for different casino
game types. Each adapter ensures proper random generation according
to game rules and regulatory requirements.

Supported Games:
- Slots: Reel symbol generation with weighted probabilities
- Cards: Deck shuffling and card dealing
- Dice: Fair dice rolls with configurable sides
- Roulette: Wheel spin simulation with proper distribution
- Lottery: Number selection with optional replacement

Each adapter includes:
- Audit logging for regulatory compliance
- RTP (Return to Player) configuration
- Outcome validation
- Statistics tracking

Usage:
    ```python
    # Slots
    slot_rng = SlotRNG(rtp_target=0.96)
    result = slot_rng.spin(5)  # 5-reel spin

    # Cards
    card_rng = CardRNG(num_decks=6)
    hand = card_rng.deal(2)  # Deal 2 cards

    # Dice
    dice_rng = DiceRNG(sides=6)
    roll = dice_rng.roll(2)  # Roll 2 dice

    # Roulette
    roulette_rng = RouletteRNG(wheel_type="european")
    number = roulette_rng.spin()
    ```
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .prng import SecurePRNG, create_casino_rng  # ty:ignore[unresolved-import]
from .shuffle import Card, create_card_deck, fisher_yates_shuffle  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)


@dataclass
class GameOutcome:
    """Generic game outcome record."""

    game_id: str
    timestamp: str
    outcome: Any
    rng_state: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class SlotRNG:
    """
    Slot machine RNG adapter.

    Handles weighted symbol generation for slot machines with
    configurable Return to Player (RTP) targets.

    The slot uses virtual reels where each symbol has a weight
    that determines its probability of appearing. The RNG selects
    a position on the virtual reel, and the symbol at that position
    becomes the result.

    Example Configuration:
        symbols = {
            "WILD": {"weight": 2, "payout": 1000},
            "SEVEN": {"weight": 5, "payout": 500},
            "BAR": {"weight": 10, "payout": 100},
            "CHERRY": {"weight": 20, "payout": 50},
            "BLANK": {"weight": 63, "payout": 0},
        }
    """

    def __init__(
        self,
        symbols: Optional[Dict[str, Dict[str, int]]] = None,
        num_reels: int = 5,
        rtp_target: float = 0.96,
        rng: Optional[SecurePRNG] = None,
    ):
        """
        Initialize slot RNG.

        Args:
            symbols: Symbol configuration with weights and payouts
            num_reels: Number of reels
            rtp_target: Target RTP (0.90-0.99 typical)
            rng: CSPRNG instance
        """
        self.rng = rng or create_casino_rng()
        self.num_reels = num_reels
        self.rtp_target = rtp_target
        self.game_id = str(uuid4())

        # Default symbol configuration (simplified)
        self.symbols = symbols or {
            "WILD": {"weight": 2, "payout": 1000},
            "SEVEN": {"weight": 5, "payout": 500},
            "BAR": {"weight": 10, "payout": 100},
            "CHERRY": {"weight": 20, "payout": 50},
            "LEMON": {"weight": 25, "payout": 25},
            "ORANGE": {"weight": 38, "payout": 10},
        }

        # Build virtual reel
        self._virtual_reel: List[str] = []
        for symbol, config in self.symbols.items():
            self._virtual_reel.extend([symbol] * config["weight"])

        self._total_weight = len(self._virtual_reel)
        self._spin_count = 0

        logger.info(f"SlotRNG initialized: {self.game_id}")

    def spin(self) -> List[str]:
        """
        Perform a slot spin.

        Returns:
            List of symbols for each reel
        """
        results = []
        for _ in range(self.num_reels):
            position = self.rng.random_int(0, self._total_weight - 1)
            results.append(self._virtual_reel[position])

        self._spin_count += 1
        return results

    def spin_with_outcome(self) -> GameOutcome:
        """
        Perform a spin and return full outcome record.

        Returns:
            GameOutcome with spin results and metadata
        """
        results = self.spin()

        return GameOutcome(
            game_id=self.game_id,
            timestamp=datetime.now().isoformat(),
            outcome=results,
            rng_state=self.rng.rng_id,
            metadata={
                "spin_number": self._spin_count,
                "rtp_target": self.rtp_target,
            },
        )

    def calculate_five_of_a_kind_return(self) -> float:
        """Expected return from the all-reels-match (five-of-a-kind) line only.

        This is NOT the game's theoretical RTP: it ignores paylines, partial
        matches, scatters, and bonus features, so it does not equal
        ``rtp_target``. A real RTP requires simulating or summing the full
        paytable; see ``slot_outcome_generator.calculate_theoretical_rtp`` for
        the Monte Carlo estimate that does account for the whole paytable.
        """
        expected_payout = 0.0
        for _symbol, config in self.symbols.items():
            prob_all_match = (config["weight"] / self._total_weight) ** self.num_reels
            expected_payout += prob_all_match * config["payout"]

        return expected_payout


class CardRNG:
    """
    Card game RNG adapter.

    Handles deck creation, shuffling, and dealing for card games.
    Supports multiple decks (e.g., 6-deck blackjack shoe).

    Features:
    - Automatic reshuffling when penetration threshold reached
    - Card counting simulation protection
    - Hand history for audit
    """

    def __init__(
        self,
        num_decks: int = 1,
        penetration: float = 0.75,
        rng: Optional[SecurePRNG] = None,
    ):
        """
        Initialize card RNG.

        Args:
            num_decks: Number of decks in the shoe
            penetration: Fraction of deck to deal before reshuffle
            rng: CSPRNG instance
        """
        self.rng = rng or create_casino_rng()
        self.num_decks = num_decks
        self.penetration = penetration
        self.game_id = str(uuid4())

        self._original_deck = create_card_deck(num_decks)
        self._shoe: List[Card] = []
        self._dealt_cards: List[Card] = []
        self._shuffle()

        logger.info(f"CardRNG initialized: {self.game_id} ({num_decks} decks)")

    def _shuffle(self) -> None:
        """Shuffle the deck/shoe."""
        self._shoe = fisher_yates_shuffle(self._original_deck, self.rng)
        self._dealt_cards = []
        logger.debug(f"Deck shuffled: {self.game_id}")

    def _check_penetration(self) -> None:
        """Check if reshuffle is needed."""
        cards_dealt = len(self._dealt_cards)
        total_cards = len(self._original_deck)
        current_penetration = cards_dealt / total_cards

        if current_penetration >= self.penetration:
            self._shuffle()

    def deal(self, num_cards: int = 1) -> List[Card]:
        """
        Deal cards from the shoe.

        Args:
            num_cards: Number of cards to deal

        Returns:
            List of dealt cards
        """
        self._check_penetration()

        if len(self._shoe) < num_cards:
            self._shuffle()

        dealt = self._shoe[:num_cards]
        self._shoe = self._shoe[num_cards:]
        self._dealt_cards.extend(dealt)

        return dealt

    def deal_with_outcome(self, num_cards: int = 1) -> GameOutcome:
        """
        Deal cards and return full outcome record.

        Returns:
            GameOutcome with dealt cards and metadata
        """
        cards = self.deal(num_cards)

        return GameOutcome(
            game_id=self.game_id,
            timestamp=datetime.now().isoformat(),
            outcome=[str(c) for c in cards],
            rng_state=self.rng.rng_id,
            metadata={
                "cards_remaining": len(self._shoe),
                "penetration": len(self._dealt_cards) / len(self._original_deck),
            },
        )

    def get_remaining_cards(self) -> int:
        """Get number of cards remaining in shoe."""
        return len(self._shoe)

    def force_reshuffle(self) -> None:
        """Force an immediate reshuffle."""
        self._shuffle()


class DiceRNG:
    """
    Dice RNG adapter.

    Handles fair dice rolling with configurable number of sides
    and dice. Suitable for craps, board games, etc.
    """

    def __init__(
        self, sides: int = 6, rng: Optional[SecurePRNG] = None
    ):
        """
        Initialize dice RNG.

        Args:
            sides: Number of sides per die
            rng: CSPRNG instance
        """
        self.rng = rng or create_casino_rng()
        self.sides = sides
        self.game_id = str(uuid4())
        self._roll_count = 0

        logger.info(f"DiceRNG initialized: {self.game_id} (d{sides})")

    def roll(self, num_dice: int = 1) -> List[int]:
        """
        Roll dice.

        Args:
            num_dice: Number of dice to roll

        Returns:
            List of die results (1 to sides)
        """
        results = [self.rng.random_int(1, self.sides) for _ in range(num_dice)]
        self._roll_count += 1
        return results

    def roll_with_outcome(self, num_dice: int = 1) -> GameOutcome:
        """
        Roll dice and return full outcome record.

        Returns:
            GameOutcome with roll results and metadata
        """
        results = self.roll(num_dice)

        return GameOutcome(
            game_id=self.game_id,
            timestamp=datetime.now().isoformat(),
            outcome=results,
            rng_state=self.rng.rng_id,
            metadata={
                "total": sum(results),
                "roll_number": self._roll_count,
                "sides": self.sides,
            },
        )


class RouletteRNG:
    """
    Roulette wheel RNG adapter.

    Simulates fair roulette spins for European (0-36) or
    American (0, 00, 1-36) wheels.
    """

    def __init__(
        self,
        wheel_type: str = "european",
        rng: Optional[SecurePRNG] = None,
    ):
        """
        Initialize roulette RNG.

        Args:
            wheel_type: "european" (single zero) or "american" (double zero)
            rng: CSPRNG instance
        """
        self.rng = rng or create_casino_rng()
        self.wheel_type = wheel_type
        self.game_id = str(uuid4())

        # Set up wheel numbers
        if wheel_type == "european":
            self._numbers = list(range(37))  # 0-36
        else:  # american
            self._numbers = [-1] + list(range(37))  # -1 represents 00

        self._spin_count = 0
        logger.info(f"RouletteRNG initialized: {self.game_id} ({wheel_type})")

    def spin(self) -> int:
        """
        Spin the roulette wheel.

        Returns:
            Winning number (0-36, or -1 for 00 on American wheel)
        """
        result = self.rng.random_choice(self._numbers)
        self._spin_count += 1
        return result

    def spin_with_outcome(self) -> GameOutcome:
        """
        Spin wheel and return full outcome record.

        Returns:
            GameOutcome with spin result and metadata
        """
        result = self.spin()

        # Determine color
        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        if result == 0 or result == -1:
            color = "green"
        elif result in red_numbers:
            color = "red"
        else:
            color = "black"

        display_number = "00" if result == -1 else str(result)

        return GameOutcome(
            game_id=self.game_id,
            timestamp=datetime.now().isoformat(),
            outcome={"number": result, "display": display_number, "color": color},
            rng_state=self.rng.rng_id,
            metadata={
                "wheel_type": self.wheel_type,
                "spin_number": self._spin_count,
            },
        )


class LotteryRNG:
    """
    Lottery number RNG adapter.

    Generates lottery numbers with or without replacement.
    Suitable for keno, bingo, lotto-style games.
    """

    def __init__(
        self,
        min_number: int = 1,
        max_number: int = 49,
        rng: Optional[SecurePRNG] = None,
    ):
        """
        Initialize lottery RNG.

        Args:
            min_number: Minimum selectable number
            max_number: Maximum selectable number
            rng: CSPRNG instance
        """
        self.rng = rng or create_casino_rng()
        self.min_number = min_number
        self.max_number = max_number
        self.game_id = str(uuid4())
        self._draw_count = 0

        logger.info(
            f"LotteryRNG initialized: {self.game_id} ({min_number}-{max_number})"
        )

    def draw(self, count: int, with_replacement: bool = False) -> List[int]:
        """
        Draw lottery numbers.

        Args:
            count: Number of balls to draw
            with_replacement: Whether to allow duplicates

        Returns:
            List of drawn numbers (sorted)
        """
        available = list(range(self.min_number, self.max_number + 1))

        if not with_replacement and count > len(available):
            raise ValueError(f"Cannot draw {count} from {len(available)} numbers")

        if with_replacement:
            results = [self.rng.random_choice(available) for _ in range(count)]
        else:
            # Fisher-Yates partial shuffle for efficiency
            results = []
            for i in range(count):
                idx = self.rng.random_int(i, len(available) - 1)
                available[i], available[idx] = available[idx], available[i]
                results.append(available[i])

        self._draw_count += 1
        return sorted(results)

    def draw_with_outcome(
        self, count: int, with_replacement: bool = False
    ) -> GameOutcome:
        """
        Draw numbers and return full outcome record.

        Returns:
            GameOutcome with drawn numbers and metadata
        """
        results = self.draw(count, with_replacement)

        return GameOutcome(
            game_id=self.game_id,
            timestamp=datetime.now().isoformat(),
            outcome=results,
            rng_state=self.rng.rng_id,
            metadata={
                "count": count,
                "with_replacement": with_replacement,
                "draw_number": self._draw_count,
                "range": f"{self.min_number}-{self.max_number}",
            },
        )
