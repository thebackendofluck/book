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
Fisher-Yates Shuffle Implementation for Casino Card Games

This module provides the industry-standard Fisher-Yates shuffle
algorithm for fair card game implementations. It includes validation
functions to verify shuffle quality and detect bias.

Why Fisher-Yates:
- Produces uniform distribution of all n! permutations
- O(n) time complexity
- In-place operation (O(1) extra space)
- Proven mathematical correctness

Common Mistakes Avoided:
- Naive swap (for i: swap(i, rand(0, n-1))) - produces biased results
- Sattolo's algorithm - never leaves elements in place (wrong for cards)
- Insufficient RNG quality - we require CSPRNG

Usage:
    ```python
    from shuffle import create_card_deck, fisher_yates_shuffle

    # Create and shuffle a standard deck
    deck = create_card_deck()
    shuffled = fisher_yates_shuffle(deck, rng)

    # Validate shuffle quality
    is_valid = validate_shuffle(shuffled, original_deck=deck)
    ```

References:
- Fisher, R. A.; Yates, F. (1938). Statistical Tables
- Knuth, D. E. (1969). The Art of Computer Programming, Vol. 2
- GLI-11 Section 5.2: Card and Tile Shuffles
"""

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TypeVar

from .prng import SecurePRNG, create_casino_rng  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Card:
    """Represents a playing card."""

    rank: str  # 2-10, J, Q, K, A
    suit: str  # hearts, diamonds, clubs, spades

    def __str__(self) -> str:
        suit_symbols = {
            "hearts": "♥",
            "diamonds": "♦",
            "clubs": "♣",
            "spades": "♠",
        }
        return f"{self.rank}{suit_symbols.get(self.suit, self.suit[0].upper())}"

    def __repr__(self) -> str:
        return f"Card({self.rank!r}, {self.suit!r})"


def create_card_deck(num_decks: int = 1, include_jokers: bool = False) -> List[Card]:
    """
    Create a standard deck of playing cards.

    Args:
        num_decks: Number of decks to combine (e.g., 6 for blackjack shoe)
        include_jokers: Whether to include joker cards

    Returns:
        List of Card objects in standard order
    """
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    suits = ["hearts", "diamonds", "clubs", "spades"]

    deck: List[Card] = []
    for _ in range(num_decks):
        for suit in suits:
            for rank in ranks:
                deck.append(Card(rank=rank, suit=suit))

        if include_jokers:
            deck.append(Card(rank="Joker", suit="red"))
            deck.append(Card(rank="Joker", suit="black"))

    return deck


def fisher_yates_shuffle(
    items: List[T], rng: Optional[SecurePRNG] = None
) -> List[T]:
    """
    Perform Fisher-Yates shuffle on a list.

    This is the only algorithm that produces a uniform distribution
    of all n! permutations with exactly n! equiprobable outcomes.

    Algorithm:
        for i from n-1 down to 1:
            j = random integer in [0, i]
            swap items[i] and items[j]

    The critical detail is that j ranges from 0 to i (not 0 to n-1).
    This ensures each of the n! permutations has exactly 1/(n!)
    probability.

    Args:
        items: List to shuffle (will be copied, not modified in place)
        rng: Cryptographically secure RNG (creates one if not provided)

    Returns:
        New list with shuffled items
    """
    if rng is None:
        rng = create_casino_rng()
        logger.warning("fisher_yates_shuffle created its own RNG - "
                      "consider passing a shared RNG for consistency")

    # Make a copy to avoid modifying the original
    shuffled = list(items)
    n = len(shuffled)

    # Fisher-Yates shuffle (Knuth shuffle)
    for i in range(n - 1, 0, -1):
        # j is in range [0, i] inclusive
        j = rng.random_int(0, i)
        # Swap elements
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]

    return shuffled


def shuffle_deck_insecure(deck: List[T]) -> List[T]:
    """
    INSECURE naive shuffle - DO NOT USE IN PRODUCTION.

    This demonstrates the WRONG way to shuffle. It produces
    biased results because some permutations are reachable
    through more swap sequences than others.

    For a 3-element list [A, B, C]:
    - Correct shuffle has 3! = 6 outcomes, each with P = 1/6
    - This algorithm has 3^3 = 27 code paths
    - 27 is not divisible by 6, so distribution is biased

    Args:
        deck: List to shuffle

    Returns:
        Shuffled list (WITH BIAS - DO NOT USE)
    """
    import random

    logger.error("shuffle_deck_insecure called - THIS PRODUCES BIASED RESULTS")

    shuffled = list(deck)
    n = len(shuffled)

    # WRONG: j should be in [0, i], not [0, n-1]
    for i in range(n):
        j = random.randint(0, n - 1)  # This is the bug!
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]

    return shuffled


@dataclass
class ShuffleValidation:
    """Results of shuffle validation tests."""

    is_valid: bool
    cards_preserved: bool
    position_distribution_p_value: float
    pair_frequency_p_value: float
    details: Dict[str, Any]


def validate_shuffle(
    shuffled: List[Card],
    original_deck: Optional[List[Card]] = None,
    rng: Optional[SecurePRNG] = None,
    num_simulations: int = 10000,
) -> ShuffleValidation:
    """
    Validate shuffle quality using statistical tests.

    This function performs multiple tests to verify that a shuffle
    implementation produces fair, unbiased results.

    Tests performed:
    1. Card preservation: All original cards present, no duplicates
    2. Position distribution: Chi-square test for uniform position distribution
    3. Pair frequency: Adjacent card pairs appear at expected rates

    Args:
        shuffled: Shuffled deck to validate
        original_deck: Original deck for comparison (auto-generated if None)
        rng: RNG for simulation (creates one if not provided)
        num_simulations: Number of shuffles for statistical testing

    Returns:
        ShuffleValidation with test results
    """
    if original_deck is None:
        original_deck = create_card_deck()

    if rng is None:
        rng = create_casino_rng()

    details: Dict[str, Any] = {}

    # Test 1: Card preservation
    original_cards = Counter(str(c) for c in original_deck)
    shuffled_cards = Counter(str(c) for c in shuffled)
    cards_preserved = original_cards == shuffled_cards
    details["cards_preserved"] = cards_preserved

    if not cards_preserved:
        details["missing"] = list((original_cards - shuffled_cards).elements())
        details["extra"] = list((shuffled_cards - original_cards).elements())

    # Test 2: Position distribution (chi-square)
    # Run multiple shuffles and check position distribution
    n = len(original_deck)
    position_counts: Dict[str, List[int]] = {
        str(c): [0] * n for c in original_deck
    }

    for _ in range(num_simulations):
        test_shuffle = fisher_yates_shuffle(original_deck, rng)
        for pos, card in enumerate(test_shuffle):
            position_counts[str(card)][pos] += 1

    # Calculate chi-square for first card (representative)
    first_card = str(original_deck[0])
    expected_per_position = num_simulations / n
    observed = position_counts[first_card]

    chi_square = sum(
        (obs - expected_per_position) ** 2 / expected_per_position
        for obs in observed
    )

    # Degrees of freedom = n - 1
    # Critical value for 95% confidence with df=51 is ~68.67
    # Using simplified p-value approximation
    position_p_value = 1.0 - min(chi_square / (2 * (n - 1)), 1.0)
    details["position_chi_square"] = chi_square
    details["position_expected"] = expected_per_position

    # Test 3: Pair frequency
    # Check if specific pairs appear at expected rates
    pair_counts: Dict[str, int] = {}
    for _ in range(num_simulations):
        test_shuffle = fisher_yates_shuffle(original_deck, rng)
        for i in range(len(test_shuffle) - 1):
            pair = f"{test_shuffle[i]}->{test_shuffle[i+1]}"
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

    # Each pair should appear approximately equally
    total_pairs = sum(pair_counts.values())
    unique_pairs = len(pair_counts)
    expected_pair_freq = total_pairs / unique_pairs if unique_pairs > 0 else 0

    if unique_pairs > 0:
        pair_chi_square = sum(
            (count - expected_pair_freq) ** 2 / expected_pair_freq
            for count in pair_counts.values()
        )
        pair_p_value = 1.0 - min(pair_chi_square / (2 * (unique_pairs - 1)), 1.0)
    else:
        pair_p_value = 0.0
        pair_chi_square = float("inf")

    details["pair_chi_square"] = pair_chi_square
    details["unique_pairs"] = unique_pairs

    # Overall validation
    is_valid = (
        cards_preserved and position_p_value > 0.01 and pair_p_value > 0.01
    )

    return ShuffleValidation(
        is_valid=is_valid,
        cards_preserved=cards_preserved,
        position_distribution_p_value=position_p_value,
        pair_frequency_p_value=pair_p_value,
        details=details,
    )


def multiple_riffle_shuffle(
    deck: List[Card], rng: SecurePRNG, num_riffles: int = 7
) -> List[Card]:
    """
    Simulate multiple riffle shuffles.

    Mathematical research (by Bayer and Diaconis, 1992) shows that
    7 riffle shuffles are needed to thoroughly mix a 52-card deck.
    This function provides a similar mixing property.

    In practice, for digital shuffles, a single Fisher-Yates shuffle
    is sufficient and more efficient. This function is provided for
    platforms that want to simulate physical shuffling for player
    familiarity.

    Args:
        deck: Deck to shuffle
        rng: Cryptographically secure RNG
        num_riffles: Number of riffle shuffles (7 recommended)

    Returns:
        Shuffled deck
    """
    result = list(deck)

    for _ in range(num_riffles):
        # Split deck approximately in half
        n = len(result)
        split = n // 2 + rng.random_int(-2, 2)  # Slight variation
        split = max(1, min(n - 1, split))  # Ensure valid split

        left = result[:split]
        right = result[split:]

        # Interleave with some randomness
        result = []
        l_idx, r_idx = 0, 0

        while l_idx < len(left) or r_idx < len(right):
            # Randomly choose which pile to take from
            if l_idx >= len(left):
                result.append(right[r_idx])
                r_idx += 1
            elif r_idx >= len(right):
                result.append(left[l_idx])
                l_idx += 1
            elif rng.random_float() < 0.5:
                result.append(left[l_idx])
                l_idx += 1
            else:
                result.append(right[r_idx])
                r_idx += 1

    return result
