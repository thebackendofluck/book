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
Weighted Slot Symbol Selection with Configurable Paytables
==========================================================

GLI-11 Section 5.4 Compliance: Slot Machine RNG Requirements
- Each reel stop must be independently selected using the CSPRNG
- Weighted symbol selection must match the configured reel strip exactly
- Payline evaluation must be deterministic given the same stop positions
- All RNG draws, stop positions, and payouts must be audit-logged
- Theoretical RTP must be calculable from reel strip + paytable config

Architecture:
- ReelStrip: Defines symbol distribution per reel (weights)
- Paytable: Maps symbol combinations to payout multipliers
- SlotOutcomeGenerator: Selects stops, evaluates paylines, computes payout

Usage:
    from fortuna_generator import FortunaGenerator
    gen = SlotOutcomeGenerator(rng=FortunaGenerator(), config=CLASSIC_5REEL)
    result = gen.spin(bet_amount=1.00)
    print(result.display_grid, result.total_payout)
"""

import hashlib
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("rng.slots")


# ---------------------------------------------------------------------------
# Symbol Definitions
# ---------------------------------------------------------------------------

class Symbol(Enum):
    WILD = "WILD"
    SCATTER = "SCATTER"
    BONUS = "BONUS"
    SEVEN = "7"
    BAR3 = "BAR3"
    BAR2 = "BAR2"
    BAR1 = "BAR1"
    CHERRY = "CHERRY"
    LEMON = "LEMON"
    ORANGE = "ORANGE"
    PLUM = "PLUM"
    GRAPE = "GRAPE"
    WATERMELON = "WATERMELON"
    BELL = "BELL"
    DIAMOND = "DIAMOND"
    ACE = "A"
    KING = "K"
    QUEEN = "Q"
    JACK = "J"
    TEN = "10"


# ---------------------------------------------------------------------------
# Reel Strip Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReelStrip:
    """
    Defines the virtual reel strip for one reel.

    Each entry is a (symbol, weight) pair. The weight determines how many
    virtual stops map to that symbol. Total stops = sum of all weights.

    GLI-11 5.4.2: The virtual reel strip must be the sole determinant
    of symbol selection probability. Weights must be immutable after
    certification.
    """
    symbols: Tuple[Tuple[Symbol, int], ...]  # (symbol, weight) pairs

    @property
    def total_stops(self) -> int:
        return sum(w for _, w in self.symbols)

    @property
    def stop_map(self) -> List[Symbol]:
        """Expand weights into a flat stop-position list."""
        stops = []
        for symbol, weight in self.symbols:
            stops.extend([symbol] * weight)
        return stops

    def probability(self, symbol: Symbol) -> float:
        """Return the probability of landing on a given symbol."""
        count = sum(w for s, w in self.symbols if s == symbol)
        return count / self.total_stops

    def to_dict(self) -> dict:
        return {
            "total_stops": self.total_stops,
            "symbols": [
                {"symbol": s.value, "weight": w, "probability": round(w / self.total_stops, 6)}
                for s, w in self.symbols
            ],
        }


# ---------------------------------------------------------------------------
# Payline Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Payline:
    """
    A payline maps reel positions to a line pattern.
    positions[i] = row index (0=top, 1=middle, 2=bottom) for reel i.
    """
    line_id: int
    positions: Tuple[int, ...]  # One per reel

    def __repr__(self) -> str:
        return f"Line{self.line_id}({list(self.positions)})"


@dataclass(frozen=True)
class PaytableEntry:
    """Payout for matching symbols on a payline."""
    symbol: Symbol
    count: int           # Minimum consecutive matches from left
    multiplier: float    # Payout = bet_per_line * multiplier
    wild_substitutes: bool = True


# ---------------------------------------------------------------------------
# Slot Machine Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotConfig:
    """Complete slot machine configuration for certification."""
    name: str
    reels: Tuple[ReelStrip, ...]      # One strip per reel
    rows: int                          # Visible rows (typically 3)
    paylines: Tuple[Payline, ...]
    paytable: Tuple[PaytableEntry, ...]
    scatter_symbol: Optional[Symbol] = Symbol.SCATTER
    wild_symbol: Optional[Symbol] = Symbol.WILD
    scatter_pays: Tuple[Tuple[int, float], ...] = ()  # (count, multiplier)
    min_bet_per_line: float = 0.01
    max_bet_per_line: float = 100.0

    @property
    def num_reels(self) -> int:
        return len(self.reels)


# ---------------------------------------------------------------------------
# Classic 5-Reel Configuration (Example)
# ---------------------------------------------------------------------------

def _build_classic_5reel() -> SlotConfig:
    """
    Build a classic 5-reel, 3-row, 20-payline slot with ~96% RTP.

    Reel strips are designed so theoretical RTP falls within
    GLI-11 acceptable range (typically 85-98% depending on jurisdiction).
    """
    # Reel strip definitions with weighted stops
    reel1 = ReelStrip(symbols=(
        (Symbol.WILD, 2), (Symbol.SCATTER, 3), (Symbol.SEVEN, 4),
        (Symbol.BAR3, 5), (Symbol.BAR2, 6), (Symbol.BAR1, 7),
        (Symbol.CHERRY, 8), (Symbol.BELL, 6), (Symbol.GRAPE, 7),
        (Symbol.WATERMELON, 5), (Symbol.ORANGE, 7), (Symbol.PLUM, 6),
        (Symbol.ACE, 5), (Symbol.KING, 6), (Symbol.QUEEN, 7),
        (Symbol.JACK, 7), (Symbol.TEN, 9),
    ))
    reel2 = ReelStrip(symbols=(
        (Symbol.WILD, 3), (Symbol.SCATTER, 2), (Symbol.SEVEN, 4),
        (Symbol.BAR3, 5), (Symbol.BAR2, 6), (Symbol.BAR1, 7),
        (Symbol.CHERRY, 7), (Symbol.BELL, 6), (Symbol.GRAPE, 7),
        (Symbol.WATERMELON, 6), (Symbol.ORANGE, 7), (Symbol.PLUM, 6),
        (Symbol.ACE, 6), (Symbol.KING, 6), (Symbol.QUEEN, 7),
        (Symbol.JACK, 7), (Symbol.TEN, 8),
    ))
    reel3 = ReelStrip(symbols=(
        (Symbol.WILD, 2), (Symbol.SCATTER, 3), (Symbol.SEVEN, 3),
        (Symbol.BAR3, 5), (Symbol.BAR2, 6), (Symbol.BAR1, 7),
        (Symbol.CHERRY, 8), (Symbol.BELL, 7), (Symbol.GRAPE, 7),
        (Symbol.WATERMELON, 5), (Symbol.ORANGE, 7), (Symbol.PLUM, 6),
        (Symbol.ACE, 6), (Symbol.KING, 6), (Symbol.QUEEN, 7),
        (Symbol.JACK, 7), (Symbol.TEN, 8),
    ))
    reel4 = ReelStrip(symbols=(
        (Symbol.WILD, 3), (Symbol.SCATTER, 2), (Symbol.SEVEN, 4),
        (Symbol.BAR3, 4), (Symbol.BAR2, 6), (Symbol.BAR1, 7),
        (Symbol.CHERRY, 7), (Symbol.BELL, 6), (Symbol.GRAPE, 8),
        (Symbol.WATERMELON, 6), (Symbol.ORANGE, 7), (Symbol.PLUM, 6),
        (Symbol.ACE, 5), (Symbol.KING, 7), (Symbol.QUEEN, 7),
        (Symbol.JACK, 7), (Symbol.TEN, 8),
    ))
    reel5 = ReelStrip(symbols=(
        (Symbol.WILD, 2), (Symbol.SCATTER, 3), (Symbol.SEVEN, 3),
        (Symbol.BAR3, 5), (Symbol.BAR2, 6), (Symbol.BAR1, 8),
        (Symbol.CHERRY, 8), (Symbol.BELL, 6), (Symbol.GRAPE, 7),
        (Symbol.WATERMELON, 5), (Symbol.ORANGE, 7), (Symbol.PLUM, 7),
        (Symbol.ACE, 6), (Symbol.KING, 6), (Symbol.QUEEN, 7),
        (Symbol.JACK, 7), (Symbol.TEN, 7),
    ))

    # 20 standard paylines (3-row, 5-reel)
    paylines = tuple(
        Payline(line_id=i, positions=positions)
        for i, positions in enumerate([
            (1, 1, 1, 1, 1),  # Line 0: middle row
            (0, 0, 0, 0, 0),  # Line 1: top row
            (2, 2, 2, 2, 2),  # Line 2: bottom row
            (0, 1, 2, 1, 0),  # Line 3: V-shape
            (2, 1, 0, 1, 2),  # Line 4: inverted V
            (0, 0, 1, 2, 2),  # Line 5: descending
            (2, 2, 1, 0, 0),  # Line 6: ascending
            (1, 0, 0, 0, 1),  # Line 7: top valley
            (1, 2, 2, 2, 1),  # Line 8: bottom valley
            (0, 1, 0, 1, 0),  # Line 9: zigzag top
            (2, 1, 2, 1, 2),  # Line 10: zigzag bottom
            (1, 0, 1, 0, 1),  # Line 11: wave top
            (1, 2, 1, 2, 1),  # Line 12: wave bottom
            (0, 1, 1, 1, 0),  # Line 13: top bracket
            (2, 1, 1, 1, 2),  # Line 14: bottom bracket
            (1, 1, 0, 1, 1),  # Line 15: top dip
            (1, 1, 2, 1, 1),  # Line 16: bottom dip
            (0, 0, 1, 0, 0),  # Line 17: top with center dip
            (2, 2, 1, 2, 2),  # Line 18: bottom with center rise
            (0, 2, 0, 2, 0),  # Line 19: alternating
        ])
    )

    # Paytable: symbol, minimum consecutive from left, multiplier
    paytable = (
        PaytableEntry(Symbol.SEVEN, 5, 500.0),
        PaytableEntry(Symbol.SEVEN, 4, 100.0),
        PaytableEntry(Symbol.SEVEN, 3, 25.0),
        PaytableEntry(Symbol.BAR3, 5, 200.0),
        PaytableEntry(Symbol.BAR3, 4, 50.0),
        PaytableEntry(Symbol.BAR3, 3, 15.0),
        PaytableEntry(Symbol.BAR2, 5, 100.0),
        PaytableEntry(Symbol.BAR2, 4, 25.0),
        PaytableEntry(Symbol.BAR2, 3, 10.0),
        PaytableEntry(Symbol.BAR1, 5, 50.0),
        PaytableEntry(Symbol.BAR1, 4, 15.0),
        PaytableEntry(Symbol.BAR1, 3, 5.0),
        PaytableEntry(Symbol.CHERRY, 5, 40.0),
        PaytableEntry(Symbol.CHERRY, 4, 10.0),
        PaytableEntry(Symbol.CHERRY, 3, 5.0),
        PaytableEntry(Symbol.BELL, 5, 30.0),
        PaytableEntry(Symbol.BELL, 4, 8.0),
        PaytableEntry(Symbol.BELL, 3, 3.0),
        PaytableEntry(Symbol.GRAPE, 5, 25.0),
        PaytableEntry(Symbol.GRAPE, 4, 6.0),
        PaytableEntry(Symbol.GRAPE, 3, 2.0),
        PaytableEntry(Symbol.WATERMELON, 5, 25.0),
        PaytableEntry(Symbol.WATERMELON, 4, 6.0),
        PaytableEntry(Symbol.WATERMELON, 3, 2.0),
        PaytableEntry(Symbol.ORANGE, 5, 20.0),
        PaytableEntry(Symbol.ORANGE, 4, 5.0),
        PaytableEntry(Symbol.ORANGE, 3, 2.0),
        PaytableEntry(Symbol.PLUM, 5, 20.0),
        PaytableEntry(Symbol.PLUM, 4, 5.0),
        PaytableEntry(Symbol.PLUM, 3, 2.0),
        PaytableEntry(Symbol.ACE, 5, 15.0),
        PaytableEntry(Symbol.ACE, 4, 4.0),
        PaytableEntry(Symbol.KING, 5, 12.0),
        PaytableEntry(Symbol.KING, 4, 3.0),
        PaytableEntry(Symbol.QUEEN, 5, 10.0),
        PaytableEntry(Symbol.QUEEN, 4, 2.0),
        PaytableEntry(Symbol.JACK, 5, 10.0),
        PaytableEntry(Symbol.JACK, 4, 2.0),
        PaytableEntry(Symbol.TEN, 5, 8.0),
        PaytableEntry(Symbol.TEN, 4, 2.0),
    )

    scatter_pays = (
        (5, 50.0),
        (4, 10.0),
        (3, 3.0),
    )

    return SlotConfig(
        name="Classic Fruits 5-Reel",
        reels=(reel1, reel2, reel3, reel4, reel5),
        rows=3,
        paylines=paylines,
        paytable=paytable,
        scatter_pays=scatter_pays,
    )


CLASSIC_5REEL = _build_classic_5reel()


# ---------------------------------------------------------------------------
# Spin Result
# ---------------------------------------------------------------------------

@dataclass
class SpinResult:
    """Complete spin result with audit trail."""
    spin_id: str
    timestamp: str
    stop_positions: List[int]
    display_grid: List[List[str]]  # rows x reels
    payline_wins: List[dict]
    scatter_win: Optional[dict]
    total_payout: float
    bet_amount: float
    bet_per_line: float
    num_lines: int
    rng_bytes_consumed: int
    verification_hash: str

    def to_dict(self) -> dict:
        return {
            "spin_id": self.spin_id,
            "timestamp": self.timestamp,
            "stop_positions": self.stop_positions,
            "display_grid": self.display_grid,
            "payline_wins": self.payline_wins,
            "scatter_win": self.scatter_win,
            "total_payout": self.total_payout,
            "bet_amount": self.bet_amount,
            "bet_per_line": self.bet_per_line,
            "num_lines": self.num_lines,
            "rng_bytes_consumed": self.rng_bytes_consumed,
            "verification_hash": self.verification_hash,
        }


# ---------------------------------------------------------------------------
# RNG Interface (matches fisher_yates_shuffle.py)
# ---------------------------------------------------------------------------

class RNGInterface:
    """Pluggable RNG backend for slot outcome generation."""

    def __init__(self, rng=None):
        self._rng = rng

    def random_int(self, upper: int) -> Tuple[int, int]:
        """
        Generate uniform random integer in [0, upper).
        Returns (value, bytes_consumed).
        Uses rejection sampling to eliminate modulo bias.
        """
        if upper <= 1:
            return 0, 0

        bit_length = (upper - 1).bit_length()
        byte_length = (bit_length + 7) // 8
        mask = (1 << bit_length) - 1

        total_bytes = 0
        for _ in range(10000):
            if self._rng:
                raw_bytes = self._rng.generate(byte_length)
            else:
                raw_bytes = os.urandom(byte_length)
            total_bytes += byte_length

            value = int.from_bytes(raw_bytes, "big") & mask
            if value < upper:
                return value, total_bytes

        raise RuntimeError(f"Rejection sampling failed for upper={upper}")


# ---------------------------------------------------------------------------
# Slot Outcome Generator
# ---------------------------------------------------------------------------

class SlotOutcomeGenerator:
    """
    GLI-11 Compliant Slot Outcome Generator.

    Features:
    - Weighted symbol selection via virtual reel strips
    - Configurable paytable with wild substitution
    - Multi-payline evaluation (left-to-right consecutive)
    - Scatter pay evaluation (any position)
    - Full audit trail per spin
    - Deterministic: same RNG state produces same outcome
    - Theoretical RTP calculation from configuration

    GLI-11 5.4 Requirements:
    - Each reel stop independently selected from CSPRNG
    - Weighted selection must match virtual reel strip exactly
    - No correlation between consecutive spins
    - Payout must match paytable deterministically
    - Complete audit log of every spin
    """

    def __init__(self, config: SlotConfig, rng=None, audit_log_path: Optional[str] = None):
        self._config = config
        self._rng = RNGInterface(rng)
        self._audit_log_path = audit_log_path
        self._audit_sequence = 0
        self._spin_count = 0
        self._total_bet = 0.0
        self._total_payout = 0.0

        # Pre-compute stop maps for each reel
        self._stop_maps = [reel.stop_map for reel in config.reels]

        # Sort paytable by (symbol, -count) for efficient lookup
        self._paytable_lookup: Dict[Symbol, List[PaytableEntry]] = {}
        for entry in config.paytable:
            self._paytable_lookup.setdefault(entry.symbol, []).append(entry)
        for entries in self._paytable_lookup.values():
            entries.sort(key=lambda e: -e.count)  # Highest count first

        logger.info(
            "Slot generator initialized: %s (%d reels, %d paylines, %d paytable entries)",
            config.name, config.num_reels, len(config.paylines), len(config.paytable),
        )

    def spin(self, bet_amount: float, num_lines: Optional[int] = None) -> SpinResult:
        """
        Execute a single slot spin.

        GLI-11 5.4.1: Each reel stop position is independently selected
        using the CSPRNG with rejection sampling to eliminate bias.

        Args:
            bet_amount: Total bet for this spin
            num_lines: Number of active paylines (default: all)

        Returns:
            SpinResult with complete audit information
        """
        config = self._config
        if num_lines is None:
            num_lines = len(config.paylines)
        num_lines = min(num_lines, len(config.paylines))
        bet_per_line = bet_amount / num_lines

        # Generate spin ID
        spin_id = hashlib.sha256(
            struct.pack(">Qd", self._spin_count, time.monotonic())
            + os.urandom(16)
        ).hexdigest()[:32]

        # --- Step 1: Select stop positions for each reel ---
        stop_positions = []
        total_rng_bytes = 0
        for reel_idx, reel in enumerate(config.reels):
            stop, bytes_used = self._rng.random_int(reel.total_stops)
            stop_positions.append(stop)
            total_rng_bytes += bytes_used

        # --- Step 2: Build visible grid (rows x reels) ---
        grid: List[List[Symbol]] = []
        for row in range(config.rows):
            grid_row = []
            for reel_idx in range(config.num_reels):
                stop_map = self._stop_maps[reel_idx]
                total_stops = len(stop_map)
                # Wrap around for rows above/below the center stop
                offset = stop_positions[reel_idx] + (row - config.rows // 2)
                pos = offset % total_stops
                grid_row.append(stop_map[pos])
            grid.append(grid_row)

        display_grid = [[s.value for s in row] for row in grid]

        # --- Step 3: Evaluate payline wins ---
        payline_wins = []
        total_payout = 0.0

        active_paylines = config.paylines[:num_lines]
        for payline in active_paylines:
            win = self._evaluate_payline(grid, payline, bet_per_line)
            if win:
                payline_wins.append(win)
                total_payout += win["payout"]

        # --- Step 4: Evaluate scatter pays ---
        scatter_win = None
        if config.scatter_symbol and config.scatter_pays:
            scatter_count = sum(
                1
                for row in grid
                for sym in row
                if sym == config.scatter_symbol
            )
            for count, multiplier in sorted(config.scatter_pays, key=lambda x: -x[0]):
                if scatter_count >= count:
                    scatter_payout = bet_amount * multiplier
                    scatter_win = {
                        "symbol": config.scatter_symbol.value,
                        "count": scatter_count,
                        "multiplier": multiplier,
                        "payout": scatter_payout,
                    }
                    total_payout += scatter_payout
                    break

        # --- Step 5: Compute verification hash ---
        hash_data = json.dumps({
            "spin_id": spin_id,
            "stops": stop_positions,
            "grid": display_grid,
            "payout": total_payout,
        }, sort_keys=True).encode()
        verification_hash = hashlib.sha256(hash_data).hexdigest()

        # Update counters
        self._spin_count += 1
        self._total_bet += bet_amount
        self._total_payout += total_payout

        result = SpinResult(
            spin_id=spin_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            stop_positions=stop_positions,
            display_grid=display_grid,
            payline_wins=payline_wins,
            scatter_win=scatter_win,
            total_payout=round(total_payout, 2),
            bet_amount=bet_amount,
            bet_per_line=bet_per_line,
            num_lines=num_lines,
            rng_bytes_consumed=total_rng_bytes,
            verification_hash=verification_hash,
        )

        self._audit("SPIN", {
            "spin_id": spin_id,
            "bet": bet_amount,
            "payout": total_payout,
            "stop_positions": stop_positions,
            "wins": len(payline_wins),
            "scatter": scatter_win is not None,
            "rng_bytes": total_rng_bytes,
        })

        return result

    def _evaluate_payline(
        self, grid: List[List[Symbol]], payline: Payline, bet_per_line: float
    ) -> Optional[dict]:
        """
        Evaluate a single payline for wins.

        GLI-11 5.4.3: Payline evaluation must count consecutive matching
        symbols from leftmost reel. WILD substitutes for any symbol
        except SCATTER.
        """
        config = self._config
        # Extract symbols on this payline
        line_symbols = [
            grid[payline.positions[reel]][reel]
            for reel in range(config.num_reels)
        ]

        # Find first non-wild symbol (determines the matching symbol)
        match_symbol = None
        for sym in line_symbols:
            if sym != config.wild_symbol:
                match_symbol = sym
                break

        if match_symbol is None:
            # All wilds - treat as highest-paying symbol
            match_symbol = Symbol.SEVEN  # Best symbol for all-wild line

        if match_symbol == config.scatter_symbol:
            return None  # Scatters don't pay on paylines

        # Count consecutive matches from left (wilds count as matches)
        consecutive = 0
        for sym in line_symbols:
            if sym == match_symbol or sym == config.wild_symbol:
                consecutive += 1
            else:
                break

        # Look up paytable
        entries = self._paytable_lookup.get(match_symbol, [])
        for entry in entries:  # Already sorted by -count
            if consecutive >= entry.count:
                payout = bet_per_line * entry.multiplier
                return {
                    "payline_id": payline.line_id,
                    "symbol": match_symbol.value,
                    "count": consecutive,
                    "multiplier": entry.multiplier,
                    "payout": round(payout, 2),
                    "positions": list(payline.positions),
                    "line_symbols": [s.value for s in line_symbols],
                }
        return None

    def calculate_theoretical_rtp(self, num_simulations: int = 1_000_000) -> dict:
        """
        Calculate theoretical RTP via Monte Carlo simulation.

        GLI-11 5.4.5: Theoretical RTP must be verifiable from the
        configuration. This method simulates spins to approximate
        the mathematical RTP.

        Returns:
            Dict with RTP percentage, confidence interval, and breakdown
        """
        config = self._config
        bet_per_spin = 1.0
        num_lines = len(config.paylines)
        bet_per_line = bet_per_spin / num_lines

        total_payout = 0.0
        payline_payouts = 0.0
        scatter_payouts = 0.0
        win_count = 0

        for _ in range(num_simulations):
            result = self.spin(bet_per_spin, num_lines)
            total_payout += result.total_payout
            if result.total_payout > 0:
                win_count += 1
            payline_payouts += sum(w["payout"] for w in result.payline_wins)
            if result.scatter_win:
                scatter_payouts += result.scatter_win["payout"]

        total_wagered = num_simulations * bet_per_spin
        rtp = (total_payout / total_wagered) * 100

        # Standard error for confidence interval
        import math
        avg_payout = total_payout / num_simulations
        variance = sum(
            (self.spin(bet_per_spin, num_lines).total_payout - avg_payout) ** 2
            for _ in range(min(10000, num_simulations))
        ) / min(10000, num_simulations)
        std_err = math.sqrt(variance / num_simulations)
        ci_95 = 1.96 * std_err / bet_per_spin * 100

        return {
            "rtp_percent": round(rtp, 4),
            "confidence_interval_95": round(ci_95, 4),
            "simulations": num_simulations,
            "hit_frequency": round(win_count / num_simulations * 100, 2),
            "payline_rtp": round(payline_payouts / total_wagered * 100, 4),
            "scatter_rtp": round(scatter_payouts / total_wagered * 100, 4),
            "total_wagered": total_wagered,
            "total_returned": round(total_payout, 2),
        }

    def get_stats(self) -> dict:
        """Return cumulative stats for the generator."""
        actual_rtp = (
            (self._total_payout / self._total_bet * 100)
            if self._total_bet > 0 else 0.0
        )
        return {
            "game": self._config.name,
            "total_spins": self._spin_count,
            "total_bet": round(self._total_bet, 2),
            "total_payout": round(self._total_payout, 2),
            "actual_rtp_percent": round(actual_rtp, 4),
            "num_reels": self._config.num_reels,
            "num_paylines": len(self._config.paylines),
        }

    def _audit(self, event_type: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "SlotOutcomeGenerator",
            "game": self._config.name,
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
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Slot outcome generator self-test."""
    print("=== Slot Outcome Generator Self-Test ===\n")

    gen = SlotOutcomeGenerator(config=CLASSIC_5REEL)

    # Test 1: Basic spin
    result = gen.spin(bet_amount=1.00)
    assert len(result.stop_positions) == 5
    assert len(result.display_grid) == 3  # 3 rows
    assert len(result.display_grid[0]) == 5  # 5 reels
    assert result.bet_amount == 1.00
    print(f"[PASS] Basic spin: stops={result.stop_positions}")
    for row in result.display_grid:
        print(f"  {row}")

    # Test 2: Payout is non-negative
    for _ in range(100):
        r = gen.spin(1.0)
        assert r.total_payout >= 0
    print("[PASS] 100 spins: all payouts non-negative")

    # Test 3: Stop positions within range
    for _ in range(1000):
        r = gen.spin(1.0)
        for reel_idx, stop in enumerate(r.stop_positions):
            assert 0 <= stop < CLASSIC_5REEL.reels[reel_idx].total_stops
    print("[PASS] 1000 spins: all stop positions within reel strip range")

    # Test 4: Verification hash consistency
    r1 = gen.spin(1.0)
    hash_data = json.dumps({
        "spin_id": r1.spin_id,
        "stops": r1.stop_positions,
        "grid": r1.display_grid,
        "payout": r1.total_payout,
    }, sort_keys=True).encode()
    expected_hash = hashlib.sha256(hash_data).hexdigest()
    assert r1.verification_hash == expected_hash
    print("[PASS] Verification hash is consistent")

    # Test 5: Stats tracking
    stats = gen.get_stats()
    assert stats["total_spins"] > 0
    assert stats["total_bet"] > 0
    print(f"[PASS] Stats: {stats['total_spins']} spins, "
          f"RTP={stats['actual_rtp_percent']:.2f}%")

    # Test 6: Reel strip probabilities sum to 1.0
    for reel_idx, reel in enumerate(CLASSIC_5REEL.reels):
        total_prob = sum(reel.probability(s) for s in Symbol)
        assert abs(total_prob - 1.0) < 1e-10, f"Reel {reel_idx} probs sum to {total_prob}"
    print("[PASS] All reel strip probabilities sum to 1.0")

    # Test 7: Multiple bet amounts
    r_small = gen.spin(0.10)
    r_large = gen.spin(100.0)
    assert r_small.bet_per_line == 0.10 / 20
    assert r_large.bet_per_line == 100.0 / 20
    print("[PASS] Variable bet amounts handled correctly")

    print(f"\nSpin ID: {result.spin_id}")
    print(f"Verification hash: {result.verification_hash[:32]}...")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
