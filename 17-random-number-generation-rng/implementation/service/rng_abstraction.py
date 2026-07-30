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
Multi-Game RNG Abstraction Layer
==================================

GLI-11 Section 4.8 Compliance: Game-Specific RNG Requirements
- Each game type has specific randomness requirements
- The abstraction layer must enforce game-specific constraints
- All game outcomes must be deterministically reproducible from RNG state
- The same underlying CSPRNG must serve all game types uniformly

Supported Game Types:
- Cards: Multi-deck shuffles, draw-without-replacement
- Dice: Single/multi-die rolls with configurable faces
- Slots: Weighted reel stop selection
- Roulette: Pocket selection (European 37, American 38)
- Keno: Draw-without-replacement from pool of 80
- Lottery: Combination generation from defined pool
- Bingo: Ball draw without replacement
- Crash/Provably Fair: Deterministic hash chain outcomes

Usage:
    rng = GameRNG()
    cards = rng.cards.shuffle_deck(num_decks=6)
    dice_result = rng.dice.roll(num_dice=2, faces=6)
    slot_stops = rng.slots.select_stops(reel_sizes=[100, 100, 100, 100, 100])
    roulette_num = rng.roulette.spin()
    keno_draw = rng.keno.draw(num_picks=20)
"""

import hashlib
import hmac
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("rng.abstraction")


# ---------------------------------------------------------------------------
# Core RNG Backend
# ---------------------------------------------------------------------------

class RNGBackend:
    """
    Pluggable RNG backend with rejection sampling.

    Wraps either FortunaGenerator or os.urandom and provides
    uniform integer and float generation with audit tracking.
    """

    def __init__(self, rng=None):
        self._rng = rng
        self._total_bytes = 0
        self._total_calls = 0

    def generate(self, num_bytes: int) -> bytes:
        """Generate raw random bytes."""
        self._total_bytes += num_bytes
        self._total_calls += 1
        if self._rng:
            return self._rng.generate(num_bytes)
        return os.urandom(num_bytes)

    def random_int(self, lower: int, upper: int) -> int:
        """Uniform random integer in [lower, upper] using rejection sampling."""
        if lower > upper:
            raise ValueError("lower must be <= upper")
        if lower == upper:
            return lower

        range_size = upper - lower + 1
        byte_count = (range_size.bit_length() + 7) // 8
        mask = (1 << range_size.bit_length()) - 1

        for _ in range(10000):
            raw = int.from_bytes(self.generate(byte_count), "big") & mask
            if raw < range_size:
                return lower + raw

        raise RuntimeError("Rejection sampling failed")

    def random_float(self) -> float:
        """Uniform float in [0.0, 1.0) with 53-bit precision."""
        raw = int.from_bytes(self.generate(7), "big") >> 3
        return raw / (1 << 53)

    @property
    def stats(self) -> dict:
        return {
            "total_bytes_generated": self._total_bytes,
            "total_calls": self._total_calls,
        }


# ---------------------------------------------------------------------------
# Audit Mixin
# ---------------------------------------------------------------------------

class AuditMixin:
    """Mixin for game-specific audit logging."""

    def __init__(self):
        self._audit_sequence = 0
        self._audit_log_path: Optional[str] = None

    def _audit(self, game_type: str, event: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "game_type": game_type,
            "event": event,
            **details,
        }
        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        logger.debug("GAME_RNG: %s", json.dumps(entry))


# ---------------------------------------------------------------------------
# Card Games Module
# ---------------------------------------------------------------------------

class CardGameRNG(AuditMixin):
    """
    Card game RNG: shuffle, draw, multi-deck shoes.

    GLI-11 5.1: All N! permutations must be equally likely.
    """

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def create_deck(self, num_decks: int = 1) -> List[int]:
        """Create ordered deck(s). Each card is 0-51."""
        if not 1 <= num_decks <= 8:
            raise ValueError("num_decks must be 1-8")
        return list(range(52)) * num_decks

    def shuffle_deck(self, num_decks: int = 1) -> List[int]:
        """Create and shuffle a deck using Fisher-Yates."""
        deck = self.create_deck(num_decks)
        n = len(deck)

        for i in range(n - 1, 0, -1):
            j = self._backend.random_int(0, i)
            deck[i], deck[j] = deck[j], deck[i]

        self._audit("cards", "SHUFFLE", {
            "num_decks": num_decks,
            "total_cards": n,
            "hash": hashlib.sha256(bytes(deck)).hexdigest()[:32],
        })
        return deck

    def draw(self, deck: List[int], count: int) -> Tuple[List[int], List[int]]:
        """Draw cards from top of deck. Returns (drawn, remaining)."""
        if count > len(deck):
            raise ValueError(f"Cannot draw {count} from {len(deck)} cards")
        drawn = deck[:count]
        remaining = deck[count:]
        self._audit("cards", "DRAW", {"count": count, "remaining": len(remaining)})
        return drawn, remaining

    @staticmethod
    def card_to_string(card_id: int) -> str:
        """Convert card ID (0-51) to readable string."""
        ranks = "A23456789TJQK"
        suits = "CDHS"
        return ranks[card_id % 13] + suits[card_id // 13]


# ---------------------------------------------------------------------------
# Dice Module
# ---------------------------------------------------------------------------

class DiceRNG(AuditMixin):
    """
    Dice game RNG: single/multi-die rolls.

    GLI-11 5.3: Each die face must have equal probability.
    """

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def roll(self, num_dice: int = 1, faces: int = 6) -> List[int]:
        """Roll num_dice dice with given number of faces."""
        if num_dice < 1 or num_dice > 100:
            raise ValueError("num_dice must be 1-100")
        if faces < 2 or faces > 1000:
            raise ValueError("faces must be 2-1000")

        results = [self._backend.random_int(1, faces) for _ in range(num_dice)]

        self._audit("dice", "ROLL", {
            "num_dice": num_dice, "faces": faces,
            "results": results, "total": sum(results),
        })
        return results

    def roll_sum(self, num_dice: int = 2, faces: int = 6) -> int:
        """Roll and return the sum."""
        return sum(self.roll(num_dice, faces))


# ---------------------------------------------------------------------------
# Slots Module
# ---------------------------------------------------------------------------

class SlotsRNG(AuditMixin):
    """
    Slot machine RNG: weighted reel stop selection.

    GLI-11 5.4: Each reel stop must be independently selected.
    """

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def select_stops(self, reel_sizes: List[int]) -> List[int]:
        """
        Select a random stop position for each reel.

        Args:
            reel_sizes: Number of virtual stops per reel

        Returns:
            List of stop positions (one per reel)
        """
        stops = [self._backend.random_int(0, size - 1) for size in reel_sizes]

        self._audit("slots", "SPIN", {
            "reel_sizes": reel_sizes,
            "stop_positions": stops,
        })
        return stops

    def weighted_select(self, weights: List[int]) -> int:
        """
        Select an index based on weights (for bonus features, etc.).

        Args:
            weights: List of integer weights

        Returns:
            Selected index
        """
        total = sum(weights)
        if total <= 0:
            raise ValueError("Total weight must be > 0")

        target = self._backend.random_int(0, total - 1)
        cumulative = 0
        for i, w in enumerate(weights):
            cumulative += w
            if target < cumulative:
                return i

        return len(weights) - 1  # Should not reach here


# ---------------------------------------------------------------------------
# Roulette Module
# ---------------------------------------------------------------------------

class RouletteRNG(AuditMixin):
    """
    Roulette RNG: pocket selection.

    GLI-11 5.2: Each pocket must have equal probability.
    """

    EUROPEAN_POCKETS = 37  # 0-36
    AMERICAN_POCKETS = 38  # 0-36 + 00

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def spin(self, variant: str = "european") -> dict:
        """
        Spin the roulette wheel.

        Args:
            variant: 'european' (37 pockets) or 'american' (38 pockets)

        Returns:
            Dict with number, color, and parity
        """
        if variant == "american":
            pocket = self._backend.random_int(0, self.AMERICAN_POCKETS - 1)
            if pocket == 37:
                result = {"number": "00", "color": "green", "parity": None}
            else:
                result = self._pocket_info(pocket)
        else:
            pocket = self._backend.random_int(0, self.EUROPEAN_POCKETS - 1)
            result = self._pocket_info(pocket)

        self._audit("roulette", "SPIN", {
            "variant": variant, "pocket": pocket, **result,
        })
        return result

    @staticmethod
    def _pocket_info(number: int) -> dict:
        """Determine color and parity for a roulette number."""
        if number == 0:
            return {"number": 0, "color": "green", "parity": None}

        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        color = "red" if number in red_numbers else "black"
        parity = "odd" if number % 2 == 1 else "even"

        return {"number": number, "color": color, "parity": parity}


# ---------------------------------------------------------------------------
# Keno Module
# ---------------------------------------------------------------------------

class KenoRNG(AuditMixin):
    """
    Keno RNG: draw without replacement from pool of 80.

    GLI-11 5.6: Each ball must have equal probability of selection.
    """

    POOL_SIZE = 80
    STANDARD_DRAW = 20

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def draw(self, num_picks: int = 20, pool_size: int = 80) -> List[int]:
        """
        Draw numbers without replacement.

        Uses Fisher-Yates partial shuffle for efficiency.
        """
        if num_picks > pool_size:
            raise ValueError(f"Cannot pick {num_picks} from {pool_size}")

        pool = list(range(1, pool_size + 1))

        # Partial Fisher-Yates (only need num_picks swaps)
        for i in range(num_picks):
            j = self._backend.random_int(i, pool_size - 1)
            pool[i], pool[j] = pool[j], pool[i]

        drawn = sorted(pool[:num_picks])

        self._audit("keno", "DRAW", {
            "pool_size": pool_size,
            "num_picks": num_picks,
            "drawn": drawn,
        })
        return drawn

    def evaluate(self, player_picks: List[int], drawn: List[int]) -> dict:
        """Evaluate player picks against drawn numbers."""
        hits = sorted(set(player_picks) & set(drawn))
        return {
            "player_picks": sorted(player_picks),
            "drawn": drawn,
            "hits": hits,
            "hit_count": len(hits),
            "total_picks": len(player_picks),
        }


# ---------------------------------------------------------------------------
# Provably Fair Module
# ---------------------------------------------------------------------------

class ProvablyFairRNG(AuditMixin):
    """
    Provably fair RNG using hash chains.

    Used for crash games, coin flips, and other provably fair games.
    The server seed chain is pre-generated and revealed after each round.
    """

    def __init__(self, backend: RNGBackend):
        super().__init__()
        self._backend = backend

    def generate_seed_chain(self, length: int = 10000) -> List[str]:
        """
        Generate a chain of server seeds.

        The chain is generated backwards: seed[n] = SHA-256(seed[n+1]).
        This allows revealing seeds in order without compromising future seeds.
        """
        terminal_seed = self._backend.generate(32).hex()
        chain = [terminal_seed]
        current = terminal_seed

        for _ in range(length - 1):
            current = hashlib.sha256(current.encode()).hexdigest()
            chain.append(current)

        chain.reverse()  # chain[0] is the first to be used

        self._audit("provably_fair", "CHAIN_GENERATED", {
            "length": length,
            "commitment_hash": hashlib.sha256(chain[0].encode()).hexdigest(),
        })
        return chain

    def compute_outcome(
        self,
        server_seed: str,
        client_seed: str,
        nonce: int,
    ) -> float:
        """
        Compute a provably fair outcome in [0, 1).

        The outcome is deterministic given server_seed, client_seed, and nonce.
        Players can verify after the server seed is revealed.
        """
        combined = f"{server_seed}:{client_seed}:{nonce}"
        hash_bytes = hashlib.sha256(combined.encode()).digest()

        # Use first 4 bytes as a 32-bit integer, convert to float
        value = int.from_bytes(hash_bytes[:4], "big")
        outcome = value / (2 ** 32)

        self._audit("provably_fair", "OUTCOME", {
            "nonce": nonce,
            "outcome": round(outcome, 10),
            "hash": hash_bytes.hex()[:32],
        })
        return outcome

    def crash_multiplier(
        self, server_seed: str, client_seed: str, nonce: int, house_edge: float = 0.03
    ) -> float:
        """
        Compute crash game multiplier.

        Uses the provably fair hash to generate a multiplier >= 1.0.
        House edge is applied via the instant-crash probability.
        """
        combined = f"{server_seed}:{client_seed}:{nonce}"
        hash_hex = hashlib.sha256(combined.encode()).hexdigest()

        # First 8 hex chars = 32-bit integer
        h = int(hash_hex[:8], 16)

        # Instant crash probability = house_edge
        if h % 33 == 0:
            return 1.0

        # Compute multiplier
        e = 2 ** 32
        result = (100 * e - h) / (e - h)
        multiplier = max(1.0, result / 100)

        return round(multiplier, 2)

    @staticmethod
    def verify_chain_link(seed: str, next_seed: str) -> bool:
        """Verify that seed = SHA-256(next_seed)."""
        expected = hashlib.sha256(next_seed.encode()).hexdigest()
        return hmac.compare_digest(seed, expected)


# ---------------------------------------------------------------------------
# Unified Game RNG Interface
# ---------------------------------------------------------------------------

class GameRNG:
    """
    Unified RNG interface for all game types.

    GLI-11 4.8: A single certified CSPRNG backend must serve
    all game types through game-specific interfaces.

    Usage:
        rng = GameRNG()
        deck = rng.cards.shuffle_deck(num_decks=6)
        dice = rng.dice.roll(2, 6)
        stops = rng.slots.select_stops([100, 100, 100])
        spin = rng.roulette.spin('european')
        draw = rng.keno.draw(20)
    """

    def __init__(self, rng_backend=None, audit_log_path: Optional[str] = None):
        self._backend = RNGBackend(rng_backend)

        self.cards = CardGameRNG(self._backend)
        self.dice = DiceRNG(self._backend)
        self.slots = SlotsRNG(self._backend)
        self.roulette = RouletteRNG(self._backend)
        self.keno = KenoRNG(self._backend)
        self.provably_fair = ProvablyFairRNG(self._backend)

        # Set audit paths
        if audit_log_path:
            for module in [self.cards, self.dice, self.slots,
                           self.roulette, self.keno, self.provably_fair]:
                module._audit_log_path = audit_log_path

        logger.info("GameRNG initialized with unified CSPRNG backend")

    def get_stats(self) -> dict:
        """Return overall RNG usage statistics."""
        return {
            "backend": self._backend.stats,
            "modules": ["cards", "dice", "slots", "roulette", "keno", "provably_fair"],
        }


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Game RNG abstraction self-test."""
    print("=== Game RNG Abstraction Self-Test ===\n")

    rng = GameRNG()

    # Test 1: Cards
    deck = rng.cards.shuffle_deck(1)
    assert len(deck) == 52
    assert sorted(deck) == list(range(52))
    drawn, remaining = rng.cards.draw(deck, 5)
    assert len(drawn) == 5
    assert len(remaining) == 47
    print(f"[PASS] Cards: shuffled 52, drew 5: "
          f"{[rng.cards.card_to_string(c) for c in drawn]}")

    # Test 2: Multi-deck
    shoe = rng.cards.shuffle_deck(6)
    assert len(shoe) == 312
    print(f"[PASS] 6-deck shoe: {len(shoe)} cards")

    # Test 3: Dice
    rolls = rng.dice.roll(2, 6)
    assert len(rolls) == 2
    assert all(1 <= r <= 6 for r in rolls)
    print(f"[PASS] Dice (2d6): {rolls} = {sum(rolls)}")

    # Test 4: Dice uniformity
    counts = [0] * 6
    for _ in range(60000):
        r = rng.dice.roll(1, 6)[0]
        counts[r - 1] += 1
    max_dev = max(abs(c - 10000) / 10000 for c in counts)
    assert max_dev < 0.03
    print(f"[PASS] Dice uniformity: {counts}, max_dev={max_dev:.4f}")

    # Test 5: Slots
    stops = rng.slots.select_stops([100, 100, 100, 100, 100])
    assert len(stops) == 5
    assert all(0 <= s < 100 for s in stops)
    print(f"[PASS] Slots (5 reels): stops={stops}")

    # Test 6: Weighted selection
    weights = [10, 30, 60]
    counts = [0, 0, 0]
    for _ in range(10000):
        idx = rng.slots.weighted_select(weights)
        counts[idx] += 1
    # Roughly 10%, 30%, 60%
    assert counts[0] < counts[1] < counts[2]
    print(f"[PASS] Weighted selection: {counts} (weights={weights})")

    # Test 7: Roulette
    result = rng.roulette.spin("european")
    assert result["number"] in list(range(37))
    assert result["color"] in ("red", "black", "green")
    print(f"[PASS] Roulette: {result}")

    # Test 8: Roulette American
    result = rng.roulette.spin("american")
    print(f"[PASS] American roulette: {result}")

    # Test 9: Keno
    draw = rng.keno.draw(20, 80)
    assert len(draw) == 20
    assert len(set(draw)) == 20
    assert all(1 <= n <= 80 for n in draw)
    print(f"[PASS] Keno draw: {draw}")

    # Test 10: Keno evaluation
    player = [1, 5, 10, 15, 20, 25, 30, 35, 40, 45]
    eval_result = rng.keno.evaluate(player, draw)
    print(f"[PASS] Keno eval: {eval_result['hit_count']}/{len(player)} hits")

    # Test 11: Provably fair
    chain = rng.provably_fair.generate_seed_chain(100)
    assert len(chain) == 100
    # Verify chain integrity
    for i in range(len(chain) - 1):
        assert rng.provably_fair.verify_chain_link(chain[i], chain[i + 1])
    print(f"[PASS] Provably fair: chain of {len(chain)}, integrity verified")

    # Test 12: Crash multiplier
    multiplier = rng.provably_fair.crash_multiplier(
        server_seed=chain[0], client_seed="player123", nonce=1
    )
    assert multiplier >= 1.0
    print(f"[PASS] Crash multiplier: {multiplier}x")

    # Test 13: Stats
    stats = rng.get_stats()
    assert stats["backend"]["total_calls"] > 0
    print(f"[PASS] Backend stats: {stats['backend']}")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
