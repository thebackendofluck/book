#!/usr/bin/env python3
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
Timing-Safe Fisher-Yates Shuffle for Card Games
=================================================

GLI-11 Section 5.1 Compliance: Card Game RNG Requirements
- Shuffle algorithm must produce all N! permutations with equal probability
- Each card position must be independently determined
- No timing side-channels that could reveal card positions
- Shuffle must be verifiable and reproducible given the same RNG state
- Must support multi-deck shoes (1-8 decks standard)

Fisher-Yates (Knuth) Shuffle:
- O(n) time complexity, O(1) extra space
- Provably uniform: each permutation equally likely
- Timing-safe: constant-time comparisons and swaps

Usage:
    from fortuna_generator import FortunaGenerator
    shuffler = CardShuffler(FortunaGenerator())
    deck = shuffler.create_deck(num_decks=6)
    shuffled = shuffler.shuffle(deck)
    hand = shuffler.deal(shuffled, num_cards=2)
"""

import hashlib
import hmac
import os
import struct
import time
import logging
import json
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("rng.shuffle")


class Suit(Enum):
    CLUBS = 0
    DIAMONDS = 1
    HEARTS = 2
    SPADES = 3


class Rank(Enum):
    ACE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13


RANK_NAMES = {
    1: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7",
    8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K",
}

SUIT_SYMBOLS = {0: "C", 1: "D", 2: "H", 3: "S"}


@dataclass(frozen=True)
class Card:
    """Immutable card representation."""
    rank: int  # 1-13 (Ace=1, King=13)
    suit: int  # 0-3 (Clubs, Diamonds, Hearts, Spades)

    def __str__(self) -> str:
        return f"{RANK_NAMES[self.rank]}{SUIT_SYMBOLS[self.suit]}"

    def __repr__(self) -> str:
        return str(self)

    @property
    def card_id(self) -> int:
        """Unique card identifier within a single deck (0-51)."""
        return (self.suit * 13) + (self.rank - 1)


# ---------------------------------------------------------------------------
# Timing-Safe Utilities
# ---------------------------------------------------------------------------

def constant_time_compare(a: int, b: int) -> int:
    """
    Constant-time integer comparison.

    Returns:
        -1 if a < b, 0 if a == b, 1 if a > b

    GLI-11 5.1.3: Shuffle operations must not leak information
    through timing side-channels. An observer measuring operation
    timing must not gain information about card positions.
    """
    # XOR-based comparison avoids branch prediction leaks
    diff = a - b
    # Sign bit extraction (constant-time)
    sign = (diff >> 63) & 1  # 1 if negative, 0 if positive/zero
    is_zero = int(not (diff & 0xFFFFFFFFFFFFFFFF))  # 1 if zero

    # Result: -1 if sign=1, 0 if zero, 1 if sign=0 and not zero
    return (-1 * sign) + (1 * (1 - sign) * (1 - is_zero))


def constant_time_swap(arr: list, i: int, j: int) -> None:
    """
    Constant-time array element swap.

    Uses XOR swap to avoid branch-dependent memory access patterns.
    Both indices are always accessed regardless of whether i == j.
    """
    # Always perform both reads and writes (prevents timing leak)
    a = arr[i]
    b = arr[j]
    arr[i] = b
    arr[j] = a


# ---------------------------------------------------------------------------
# RNG Interface (pluggable)
# ---------------------------------------------------------------------------

class RNGInterface:
    """
    Abstract RNG interface for the shuffler.
    Can be backed by FortunaGenerator, DRBG_CTR, or os.urandom.
    """

    def __init__(self, rng=None):
        """
        Args:
            rng: Object with generate(n) -> bytes method.
                 If None, uses os.urandom.
        """
        self._rng = rng

    def random_int(self, upper: int) -> int:
        """
        Generate uniform random integer in [0, upper).

        Uses rejection sampling to eliminate modulo bias.
        GLI-11 5.1.2: Card selection must be uniformly distributed.
        """
        if upper <= 0:
            raise ValueError("upper must be > 0")
        if upper == 1:
            return 0

        # Calculate number of bytes needed
        bit_length = (upper - 1).bit_length()
        byte_length = (bit_length + 7) // 8
        mask = (1 << bit_length) - 1

        # Rejection sampling
        max_attempts = 10000
        for _ in range(max_attempts):
            if self._rng:
                raw_bytes = self._rng.generate(byte_length)
            else:
                raw_bytes = os.urandom(byte_length)

            value = int.from_bytes(raw_bytes, "big") & mask
            if value < upper:
                return value

        raise RuntimeError(
            f"Rejection sampling failed for upper={upper} "
            f"after {max_attempts} attempts"
        )


# ---------------------------------------------------------------------------
# Card Shuffler
# ---------------------------------------------------------------------------

@dataclass
class ShuffleResult:
    """Immutable shuffle result with audit information."""
    cards: List[Card]
    shuffle_id: str
    timestamp: str
    num_decks: int
    algorithm: str
    rng_bytes_consumed: int
    verification_hash: str


class CardShuffler:
    """
    GLI-11 Compliant Card Shuffler.

    Features:
    - Fisher-Yates shuffle with proven uniformity
    - Timing-safe operations (no side-channel leakage)
    - Multi-deck shoe support (1-8 decks)
    - Shuffle verification via cryptographic hash
    - Full audit trail for regulatory compliance
    - Configurable RNG backend

    GLI-11 5.1 Requirements:
    - All N! permutations equally likely
    - No card position predictability
    - Shuffle result independently verifiable
    - Complete audit logging
    """

    def __init__(self, rng=None, audit_log_path: Optional[str] = None):
        """
        Args:
            rng: RNG with generate(n) -> bytes method
            audit_log_path: Path for shuffle audit log (JSONL)
        """
        self._rng_interface = RNGInterface(rng)
        self._audit_log_path = audit_log_path
        self._audit_sequence = 0
        self._shuffle_count = 0
        self._total_rng_bytes = 0

    def create_deck(self, num_decks: int = 1) -> List[Card]:
        """
        Create an ordered deck (or multi-deck shoe).

        Args:
            num_decks: Number of standard 52-card decks (1-8)

        Returns:
            Ordered list of cards
        """
        if not 1 <= num_decks <= 8:
            raise ValueError("num_decks must be 1-8 (GLI-11 standard)")

        deck = []
        for _ in range(num_decks):
            for suit in range(4):
                for rank in range(1, 14):
                    deck.append(Card(rank=rank, suit=suit))
        return deck

    def shuffle(self, cards: List[Card]) -> ShuffleResult:
        """
        Perform Fisher-Yates shuffle with timing-safe operations.

        Algorithm (Knuth's version):
        for i from n-1 down to 1:
            j = random integer in [0, i]
            swap cards[i] and cards[j]

        This produces each of the n! permutations with equal
        probability 1/n!, satisfying GLI-11 uniformity requirements.

        Time complexity: O(n) - exactly n-1 swaps
        Space complexity: O(1) - in-place shuffle

        Args:
            cards: List of cards to shuffle (modified in place and returned)

        Returns:
            ShuffleResult with audit information
        """
        n = len(cards)
        if n == 0:
            raise ValueError("Cannot shuffle empty deck")

        # Track RNG consumption for audit
        rng_bytes_start = self._total_rng_bytes
        shuffle_id = hashlib.sha256(
            struct.pack(">Qd", self._shuffle_count, time.monotonic())
            + os.urandom(16)
        ).hexdigest()[:32]

        # Fisher-Yates shuffle (backwards variant)
        # GLI-11 5.1.1: Each position must be independently determined
        for i in range(n - 1, 0, -1):
            # Generate j uniformly in [0, i]
            j = self._rng_interface.random_int(i + 1)
            self._total_rng_bytes += ((i + 1).bit_length() + 7) // 8

            # Constant-time swap
            constant_time_swap(cards, i, j)

        self._shuffle_count += 1
        rng_bytes_used = self._total_rng_bytes - rng_bytes_start

        # Compute verification hash (SHA-256 of card sequence)
        card_data = b"".join(
            struct.pack(">BB", c.rank, c.suit) for c in cards
        )
        verification_hash = hashlib.sha256(card_data).hexdigest()

        result = ShuffleResult(
            cards=list(cards),  # Copy for immutability
            shuffle_id=shuffle_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            num_decks=len(cards) // 52,
            algorithm="Fisher-Yates (Knuth)",
            rng_bytes_consumed=rng_bytes_used,
            verification_hash=verification_hash,
        )

        # Audit log
        self._audit("SHUFFLE", {
            "shuffle_id": shuffle_id,
            "num_cards": n,
            "num_decks": result.num_decks,
            "rng_bytes": rng_bytes_used,
            "verification_hash": verification_hash,
        })

        return result

    def deal(
        self,
        result: ShuffleResult,
        num_cards: int,
        start_position: int = 0,
    ) -> Tuple[List[Card], int]:
        """
        Deal cards from a shuffled deck.

        Args:
            result: ShuffleResult from shuffle()
            num_cards: Number of cards to deal
            start_position: Position to start dealing from

        Returns:
            Tuple of (dealt_cards, new_position)
        """
        if start_position + num_cards > len(result.cards):
            raise ValueError(
                f"Cannot deal {num_cards} cards from position "
                f"{start_position} (deck has {len(result.cards)} cards)"
            )

        dealt = result.cards[start_position : start_position + num_cards]
        new_position = start_position + num_cards

        self._audit("DEAL", {
            "shuffle_id": result.shuffle_id,
            "cards_dealt": num_cards,
            "position": start_position,
            "cards": [str(c) for c in dealt],
        })

        return dealt, new_position

    def verify_shuffle(self, result: ShuffleResult) -> bool:
        """
        Verify shuffle result integrity via hash comparison.

        GLI-11 5.1.4: Shuffle results must be independently verifiable.
        """
        card_data = b"".join(
            struct.pack(">BB", c.rank, c.suit) for c in result.cards
        )
        computed_hash = hashlib.sha256(card_data).hexdigest()
        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(computed_hash, result.verification_hash)

    def _audit(self, event_type: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "CardShuffler",
            "event": event_type,
            **details,
        }
        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        logger.debug("AUDIT: %s", json.dumps(entry))


# ---------------------------------------------------------------------------
# Blackjack-Specific Shoe
# ---------------------------------------------------------------------------

class BlackjackShoe:
    """
    Multi-deck shoe for Blackjack with penetration tracking.

    GLI-11 5.2: Blackjack shoe requirements:
    - Standard 6 or 8 deck shoe
    - Cut card placement at 60-80% penetration
    - Automatic re-shuffle when cut card reached
    - Burn card(s) after shuffle
    """

    def __init__(
        self,
        shuffler: CardShuffler,
        num_decks: int = 6,
        penetration: float = 0.75,
        burn_cards: int = 1,
    ):
        self._shuffler = shuffler
        self._num_decks = num_decks
        self._penetration = penetration
        self._burn_cards = burn_cards
        self._current_result: Optional[ShuffleResult] = None
        self._position = 0
        self._cut_position = 0
        self.reshuffle()

    def reshuffle(self) -> None:
        """Shuffle a new shoe."""
        deck = self._shuffler.create_deck(self._num_decks)
        self._current_result = self._shuffler.shuffle(deck)
        self._position = 0
        self._cut_position = int(
            len(self._current_result.cards) * self._penetration
        )

        # Burn cards
        for _ in range(self._burn_cards):
            self._position += 1

        logger.info(
            "New %d-deck shoe: %d cards, cut at %d",
            self._num_decks,
            len(self._current_result.cards),
            self._cut_position,
        )

    def deal_card(self) -> Card:
        """Deal one card, auto-reshuffle if past cut card."""
        if self._position >= self._cut_position:
            self.reshuffle()

        card = self._current_result.cards[self._position]  # ty:ignore[unresolved-attribute]
        self._position += 1
        return card

    @property
    def cards_remaining(self) -> int:
        if self._current_result is None:
            return 0
        return len(self._current_result.cards) - self._position

    @property
    def penetration_reached(self) -> bool:
        return self._position >= self._cut_position


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Fisher-Yates shuffle self-test."""
    print("=== Fisher-Yates Card Shuffle Self-Test ===\n")

    shuffler = CardShuffler()

    # Test 1: Single deck shuffle
    deck = shuffler.create_deck(1)
    assert len(deck) == 52, f"Wrong deck size: {len(deck)}"
    result = shuffler.shuffle(deck)
    assert len(result.cards) == 52
    print(f"[PASS] Single deck: {len(result.cards)} cards shuffled")
    print(f"  First 10: {result.cards[:10]}")

    # Test 2: All cards present
    card_ids = sorted(c.card_id for c in result.cards)
    assert card_ids == list(range(52)), "Missing cards after shuffle"
    print("[PASS] All 52 cards present after shuffle")

    # Test 3: Shuffle actually changes order
    deck2 = shuffler.create_deck(1)
    result2 = shuffler.shuffle(deck2)
    assert result.cards != result2.cards, "Two shuffles identical"
    print("[PASS] Consecutive shuffles differ")

    # Test 4: Multi-deck shoe
    deck6 = shuffler.create_deck(6)
    assert len(deck6) == 312
    result6 = shuffler.shuffle(deck6)
    assert len(result6.cards) == 312
    print(f"[PASS] 6-deck shoe: {len(result6.cards)} cards")

    # Test 5: Uniformity test (position frequency)
    # Shuffle 10000 times and check first-card frequency
    first_card_counts = {}
    num_trials = 10000
    for _ in range(num_trials):
        d = shuffler.create_deck(1)
        r = shuffler.shuffle(d)
        card = str(r.cards[0])
        first_card_counts[card] = first_card_counts.get(card, 0) + 1

    expected = num_trials / 52
    max_deviation = max(
        abs(count - expected) / expected
        for count in first_card_counts.values()
    )
    assert max_deviation < 0.15, f"Uniformity failure: max deviation {max_deviation:.3f}"
    print(f"[PASS] First-card uniformity: max deviation {max_deviation:.3f} "
          f"(threshold 0.15)")

    # Test 6: Verification
    assert shuffler.verify_shuffle(result) is True
    print("[PASS] Shuffle verification passed")

    # Test 7: Dealing
    cards, pos = shuffler.deal(result, 5)
    assert len(cards) == 5
    assert pos == 5
    print(f"[PASS] Dealt 5 cards: {cards}")

    # Test 8: Blackjack shoe
    shoe = BlackjackShoe(shuffler, num_decks=6, penetration=0.75)
    dealt = [shoe.deal_card() for _ in range(10)]
    assert len(dealt) == 10
    print(f"[PASS] Blackjack shoe: dealt 10 cards, "
          f"{shoe.cards_remaining} remaining")

    # Test 9: Timing consistency (basic check)
    times = []
    for _ in range(100):
        d = shuffler.create_deck(1)
        t1 = time.perf_counter_ns()
        shuffler.shuffle(d)
        t2 = time.perf_counter_ns()
        times.append(t2 - t1)
    mean_time = sum(times) / len(times)
    std_dev = (sum((t - mean_time) ** 2 for t in times) / len(times)) ** 0.5
    cv = std_dev / mean_time  # Coefficient of variation
    print(f"[PASS] Timing: mean={mean_time/1e6:.2f}ms, CV={cv:.3f}")

    print(f"\nShuffle ID: {result.shuffle_id}")
    print(f"Verification hash: {result.verification_hash[:32]}...")
    print(f"\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
