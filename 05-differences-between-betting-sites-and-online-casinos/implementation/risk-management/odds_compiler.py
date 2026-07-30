#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 05, Differences Between Betting Sites and Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 3: Betting vs Casino - Odds Compilation & Margin Calculator

Production-grade odds compilation engine for sportsbook operators.
Supports decimal, fractional, and American odds formats with configurable
overround (margin) per market type. Includes:
- True probability estimation from multiple bookmaker feeds
- Margin injection with proportional or Shin method
- Market type templates (football, tennis, horse racing, etc.)
- Real-time odds movement tracking and alerting
- Kelly criterion for max liability sizing

Usage:
    compiler = OddsCompiler(target_margin=0.05)
    market = compiler.compile_1x2(home=0.45, draw=0.28, away=0.27)
    print(market)
"""

import json
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class OddsFormat(Enum):
    DECIMAL = "decimal"
    FRACTIONAL = "fractional"
    AMERICAN = "american"


class MarginMethod(Enum):
    PROPORTIONAL = "proportional"      # Equal margin across all outcomes
    SHIN = "shin"                      # Shin method - favors favorites
    ODDS_RATIO = "odds_ratio"          # Logarithmic margin distribution
    POWER = "power"                    # Power method for balanced books


@dataclass
class Selection:
    """A single outcome in a market (e.g., Home Win)."""
    name: str
    true_probability: float
    implied_probability: float = 0.0
    decimal_odds: float = 0.0
    fractional_odds: str = ""
    american_odds: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "true_probability": round(self.true_probability, 6),
            "implied_probability": round(self.implied_probability, 6),
            "decimal_odds": round(self.decimal_odds, 3),
            "fractional_odds": self.fractional_odds,
            "american_odds": self.american_odds,
        }


@dataclass
class Market:
    """A betting market containing multiple selections."""
    market_id: str
    market_type: str
    event_name: str
    selections: list[Selection] = field(default_factory=list)
    overround: float = 0.0
    margin: float = 0.0
    compiled_at: str = ""
    status: str = "open"

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_type": self.market_type,
            "event_name": self.event_name,
            "selections": [s.to_dict() for s in self.selections],
            "overround_pct": round(self.overround * 100, 2),
            "margin_pct": round(self.margin * 100, 2),
            "compiled_at": self.compiled_at,
            "status": self.status,
        }


# ── Standard Margin Templates by Market Type ─────────────────────────
MARKET_MARGIN_TEMPLATES = {
    "football_1x2":        {"margin": 0.045, "method": MarginMethod.SHIN},
    "football_over_under": {"margin": 0.050, "method": MarginMethod.PROPORTIONAL},
    "football_btts":       {"margin": 0.060, "method": MarginMethod.PROPORTIONAL},
    "football_asian_hcp":  {"margin": 0.035, "method": MarginMethod.PROPORTIONAL},
    "tennis_match_winner": {"margin": 0.040, "method": MarginMethod.SHIN},
    "tennis_set_winner":   {"margin": 0.055, "method": MarginMethod.PROPORTIONAL},
    "horse_racing_win":    {"margin": 0.120, "method": MarginMethod.SHIN},
    "horse_racing_place":  {"margin": 0.180, "method": MarginMethod.PROPORTIONAL},
    "basketball_spread":   {"margin": 0.045, "method": MarginMethod.PROPORTIONAL},
    "basketball_total":    {"margin": 0.050, "method": MarginMethod.PROPORTIONAL},
    "esports_match":       {"margin": 0.055, "method": MarginMethod.SHIN},
    "mma_fight_winner":    {"margin": 0.050, "method": MarginMethod.SHIN},
    "custom_2way":         {"margin": 0.050, "method": MarginMethod.PROPORTIONAL},
    "custom_3way":         {"margin": 0.060, "method": MarginMethod.SHIN},
}


class OddsCompiler:
    """
    Core odds compilation engine.

    Converts true probabilities into bookmaker odds with configurable margin.
    Supports multiple margin injection methods used by real sportsbooks.
    """

    def __init__(
        self,
        target_margin: float = 0.05,
        method: MarginMethod = MarginMethod.SHIN,
        min_odds: float = 1.01,
        max_odds: float = 1001.0,
    ):
        self.target_margin = target_margin
        self.method = method
        self.min_odds = min_odds
        self.max_odds = max_odds
        self._market_counter = 0

    def _next_market_id(self) -> str:
        self._market_counter += 1
        return f"MKT-{self._market_counter:06d}"

    # ── Odds Format Conversions ───────────────────────────────────────

    @staticmethod
    def decimal_to_fractional(decimal_odds: float) -> str:
        """Convert decimal odds to fractional (e.g., 3.0 -> '2/1')."""
        numerator = decimal_odds - 1
        # Find closest clean fraction
        best_num, best_den = round(numerator * 100), 100
        for denom in [1, 2, 4, 5, 8, 10, 20, 25, 50, 100]:
            num = round(numerator * denom)
            if abs(num / denom - numerator) < 0.005:
                best_num, best_den = num, denom
                break
        from math import gcd
        g = gcd(best_num, best_den)
        return f"{best_num // g}/{best_den // g}"

    @staticmethod
    def decimal_to_american(decimal_odds: float) -> int:
        """Convert decimal odds to American format."""
        if decimal_odds >= 2.0:
            return round((decimal_odds - 1) * 100)
        else:
            return round(-100 / (decimal_odds - 1))

    # ── Margin Injection Methods ──────────────────────────────────────

    def _apply_proportional_margin(self, probabilities: list[float]) -> list[float]:
        """
        Proportional method: scales all probabilities equally.
        Most common method, simple but penalizes longshots.
        """
        total = sum(probabilities)
        target_total = 1.0 + self.target_margin  # ty:ignore[unsupported-operator]
        return [p * (target_total / total) for p in probabilities]

    def _apply_shin_margin(self, probabilities: list[float]) -> list[float]:
        """
        Shin method: distributes margin considering insider trading.
        Better for markets with large odds differences (e.g., horse racing).
        Favors favorites, penalizes longshots less.

        Based on: Shin, H.S. (1993) "Measuring the Incidence of Insider Trading
        in a Market for State-Contingent Claims"
        """
        n = len(probabilities)
        z = self.target_margin / (n + self.target_margin)  # ty:ignore[unsupported-operator]

        implied = []
        for p in probabilities:
            shin_prob = (math.sqrt(z ** 2 + 4 * (1 - z) * (p ** 2 / sum(p2 ** 2 for p2 in probabilities) * (1 - z + z / n)))) / (2 * (1 - z))
            # Simplified Shin approximation
            shin_prob = max(p + z * (1 / n - p), 0.001)
            implied.append(shin_prob)

        # Normalize to target overround
        scale = (1.0 + self.target_margin) / sum(implied)  # ty:ignore[unsupported-operator]
        return [p * scale for p in implied]

    def _apply_odds_ratio_margin(self, probabilities: list[float]) -> list[float]:
        """
        Odds-ratio method: logarithmic margin distribution.
        Most mathematically fair distribution.
        """
        n = len(probabilities)
        k = (1.0 + self.target_margin)  # ty:ignore[unsupported-operator]

        # Iterative solution for odds-ratio parameter
        c = k  # Starting approximation
        for _ in range(50):
            sum_adj = sum(c * p / (c * p + 1 - p) for p in probabilities)
            if abs(sum_adj - k) < 1e-10:
                break
            # Newton step
            deriv = sum(p * (1 - p) / (c * p + 1 - p) ** 2 for p in probabilities)
            c -= (sum_adj - k) / deriv

        return [c * p / (c * p + 1 - p) for p in probabilities]

    def _apply_power_margin(self, probabilities: list[float]) -> list[float]:
        """
        Power method: raises probabilities to a power > 1.
        Balanced distribution, commonly used in Asian markets.
        """
        # Binary search for power parameter
        lo, hi = 1.0, 2.0
        target = 1.0 + self.target_margin  # ty:ignore[unsupported-operator]

        for _ in range(100):
            mid = (lo + hi) / 2
            total = sum(p ** mid for p in probabilities)
            if total < target:
                hi = mid
            else:
                lo = mid

        power = (lo + hi) / 2
        return [p ** power for p in probabilities]

    def _inject_margin(self, probabilities: list[float]) -> list[float]:
        """Apply the configured margin method."""
        methods = {
            MarginMethod.PROPORTIONAL: self._apply_proportional_margin,
            MarginMethod.SHIN: self._apply_shin_margin,
            MarginMethod.ODDS_RATIO: self._apply_odds_ratio_margin,
            MarginMethod.POWER: self._apply_power_margin,
        }
        return methods[self.method](probabilities)  # ty:ignore[invalid-argument-type]

    # ── Market Compilation ────────────────────────────────────────────

    def compile_market(
        self,
        event_name: str,
        market_type: str,
        outcome_names: list[str],
        true_probabilities: list[float],
    ) -> Market:
        """
        Compile a generic market from true probabilities.

        Args:
            event_name: e.g., "Arsenal vs Chelsea"
            market_type: e.g., "football_1x2"
            outcome_names: e.g., ["Home", "Draw", "Away"]
            true_probabilities: e.g., [0.45, 0.28, 0.27]

        Returns:
            Market with compiled odds and margin information.
        """
        if len(outcome_names) != len(true_probabilities):
            raise ValueError("Outcome names and probabilities must have equal length")

        prob_sum = sum(true_probabilities)
        if abs(prob_sum - 1.0) > 0.01:
            logger.warning(f"Probabilities sum to {prob_sum:.4f}, normalizing to 1.0")
            true_probabilities = [p / prob_sum for p in true_probabilities]

        # Apply template if available
        if market_type in MARKET_MARGIN_TEMPLATES:
            template = MARKET_MARGIN_TEMPLATES[market_type]
            self.target_margin = template["margin"]
            self.method = template["method"]
            logger.info(f"Using template '{market_type}': margin={self.target_margin*100:.1f}%, method={self.method.value}")  # ty:ignore[possibly-missing-attribute, unsupported-operator]

        # Inject margin
        implied_probs = self._inject_margin(true_probabilities)

        # Build selections
        selections = []
        for name, true_p, implied_p in zip(outcome_names, true_probabilities, implied_probs):
            decimal_odds = 1.0 / implied_p
            decimal_odds = max(self.min_odds, min(self.max_odds, decimal_odds))
            decimal_odds = round(decimal_odds, 3)

            selections.append(Selection(
                name=name,
                true_probability=true_p,
                implied_probability=implied_p,
                decimal_odds=decimal_odds,
                fractional_odds=self.decimal_to_fractional(decimal_odds),
                american_odds=self.decimal_to_american(decimal_odds),
            ))

        overround = sum(1.0 / s.decimal_odds for s in selections) - 1.0

        market = Market(
            market_id=self._next_market_id(),
            market_type=market_type,
            event_name=event_name,
            selections=selections,
            overround=overround,
            margin=overround / (1.0 + overround),
            compiled_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            f"Compiled {market_type} for '{event_name}': "
            f"overround={overround*100:.2f}%, "
            f"odds=[{', '.join(f'{s.decimal_odds}' for s in selections)}]"
        )

        return market

    # ── Convenience Methods for Common Market Types ───────────────────

    def compile_1x2(self, event_name: str, home: float, draw: float, away: float) -> Market:
        """Compile a football 1X2 (match result) market."""
        return self.compile_market(event_name, "football_1x2", ["Home", "Draw", "Away"], [home, draw, away])

    def compile_over_under(self, event_name: str, over: float, under: float, line: float = 2.5) -> Market:
        """Compile an Over/Under goals market."""
        return self.compile_market(
            event_name, "football_over_under",
            [f"Over {line}", f"Under {line}"], [over, under]
        )

    def compile_match_winner(self, event_name: str, player1: float, player2: float) -> Market:
        """Compile a 2-way match winner market (tennis, MMA, etc.)."""
        return self.compile_market(event_name, "tennis_match_winner", ["Player 1", "Player 2"], [player1, player2])

    def compile_horse_racing(self, event_name: str, runners: dict[str, float]) -> Market:
        """Compile a horse racing win market from runner probabilities."""
        return self.compile_market(
            event_name, "horse_racing_win",
            list(runners.keys()), list(runners.values())
        )

    # ── Odds Comparison & Sharp Detection ─────────────────────────────

    @staticmethod
    def detect_value(our_odds: float, market_avg: float, threshold: float = 0.02) -> dict:
        """
        Detect if our odds are too generous vs market average.
        Returns recommendation to shorten/lengthen.
        """
        our_prob = 1.0 / our_odds
        market_prob = 1.0 / market_avg
        edge = our_prob - market_prob

        return {
            "our_odds": our_odds,
            "market_average": market_avg,
            "probability_diff": round(edge, 4),
            "action": "SHORTEN" if edge > threshold else "LENGTHEN" if edge < -threshold else "HOLD",
            "severity": "HIGH" if abs(edge) > 0.05 else "MEDIUM" if abs(edge) > threshold else "LOW",
        }

    @staticmethod
    def remove_margin(odds_list: list[float]) -> list[float]:
        """
        Strip margin from bookmaker odds to estimate true probabilities.
        Useful for analyzing competitor prices.
        """
        implied = [1.0 / o for o in odds_list]
        overround = sum(implied)
        return [p / overround for p in implied]


class OddsMovementTracker:
    """Track odds movements and detect suspicious patterns."""

    def __init__(self, alert_threshold_pct: float = 5.0):
        self.alert_threshold = alert_threshold_pct / 100.0
        self.history: dict[str, list[dict]] = {}

    def record(self, market_id: str, selection: str, odds: float):
        key = f"{market_id}:{selection}"
        if key not in self.history:
            self.history[key] = []
        self.history[key].append({
            "odds": odds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._check_movement(key)

    def _check_movement(self, key: str):
        history = self.history[key]
        if len(history) < 2:
            return
        prev = history[-2]["odds"]
        curr = history[-1]["odds"]
        pct_change = abs(curr - prev) / prev

        if pct_change > self.alert_threshold:
            logger.warning(
                f"ODDS MOVEMENT ALERT [{key}]: {prev:.3f} -> {curr:.3f} "
                f"({pct_change*100:.1f}% change)"
            )

    def get_steam_moves(self, market_id: str) -> list[dict]:
        """Detect steam moves (sharp money) on a market."""
        steam = []
        for key, records in self.history.items():
            if not key.startswith(market_id):
                continue
            if len(records) < 3:
                continue
            # Check for consistent one-direction movement (steam)
            diffs = [records[i+1]["odds"] - records[i]["odds"] for i in range(len(records)-1)]
            if all(d < 0 for d in diffs[-3:]) or all(d > 0 for d in diffs[-3:]):
                steam.append({
                    "selection": key.split(":")[1],
                    "direction": "shortening" if diffs[-1] < 0 else "drifting",
                    "total_move": round(records[-1]["odds"] - records[0]["odds"], 3),
                    "moves": len(records),
                })
        return steam


# ── Kelly Criterion for Liability Sizing ──────────────────────────────

def kelly_stake(true_prob: float, decimal_odds: float, bankroll: float, fraction: float = 0.25) -> dict:
    """
    Calculate optimal stake using fractional Kelly criterion.

    Args:
        true_prob: True probability of outcome
        decimal_odds: Offered decimal odds
        bankroll: Total bankroll
        fraction: Kelly fraction (0.25 = quarter Kelly, safer)

    Returns:
        Recommended stake and edge information.
    """
    q = 1.0 - true_prob
    b = decimal_odds - 1.0
    kelly_pct = (b * true_prob - q) / b

    if kelly_pct <= 0:
        return {
            "edge": round((true_prob * decimal_odds - 1) * 100, 2),
            "kelly_pct": 0,
            "recommended_stake": 0,
            "action": "NO_BET",
            "reason": "Negative expected value",
        }

    fractional_kelly = kelly_pct * fraction
    stake = bankroll * fractional_kelly

    return {
        "edge": round((true_prob * decimal_odds - 1) * 100, 2),
        "full_kelly_pct": round(kelly_pct * 100, 2),
        "fractional_kelly_pct": round(fractional_kelly * 100, 2),
        "recommended_stake": round(stake, 2),
        "expected_roi": round((true_prob * decimal_odds - 1) * 100, 2),
        "action": "BET",
    }


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    compiler = OddsCompiler(target_margin=0.05, method=MarginMethod.SHIN)

    print("=" * 72)
    print("ODDS COMPILATION ENGINE - iGaming Sportsbook")
    print("=" * 72)

    # 1. Football 1X2
    print("\n[1] Football 1X2 - Premier League")
    market = compiler.compile_1x2("Arsenal vs Chelsea", home=0.48, draw=0.26, away=0.26)
    print(json.dumps(market.to_dict(), indent=2))

    # 2. Over/Under
    print("\n[2] Over/Under 2.5 Goals")
    ou_market = compiler.compile_over_under("Arsenal vs Chelsea", over=0.55, under=0.45)
    print(json.dumps(ou_market.to_dict(), indent=2))

    # 3. Horse Racing (multi-runner)
    print("\n[3] Horse Racing - 8 Runners")
    runners = {
        "Frankel Jr": 0.30, "Desert Crown": 0.20, "Sea The Stars II": 0.15,
        "Galileo Gold": 0.12, "Enable's Dream": 0.08, "Stradivarius II": 0.06,
        "Pinatubo Son": 0.05, "Serpentine Star": 0.04,
    }
    racing = compiler.compile_horse_racing("Ascot 3:20 Gold Cup", runners)
    print(json.dumps(racing.to_dict(), indent=2))

    # 4. Margin comparison across methods
    print("\n[4] Margin Method Comparison (Football 1X2: 45/28/27)")
    probs = [0.45, 0.28, 0.27]
    for method in MarginMethod:
        c = OddsCompiler(target_margin=0.05, method=method)
        m = c.compile_market("Test FC vs Test Utd", "custom_3way", ["H", "D", "A"], probs)
        odds_str = " | ".join(f"{s.name}: {s.decimal_odds:.3f}" for s in m.selections)
        print(f"  {method.value:15s}: {odds_str}  (overround: {m.overround*100:.2f}%)")

    # 5. Strip margin from competitor
    print("\n[5] Reverse-engineer competitor margin")
    competitor_odds = [2.10, 3.40, 3.50]  # From bet365
    true_probs = OddsCompiler.remove_margin(competitor_odds)
    print(f"  Competitor odds: {competitor_odds}")
    print(f"  Estimated true probs: {[round(p, 4) for p in true_probs]}")
    print(f"  Competitor overround: {(sum(1/o for o in competitor_odds) - 1)*100:.2f}%")

    # 6. Kelly stake
    print("\n[6] Kelly Criterion Sizing")
    result = kelly_stake(true_prob=0.48, decimal_odds=2.10, bankroll=100_000, fraction=0.25)
    print(f"  Edge: {result['edge']}%, Stake: ${result['recommended_stake']:,.2f}")
    print(f"  Full Kelly: {result.get('full_kelly_pct', 0)}%, Quarter Kelly: {result.get('fractional_kelly_pct', 0)}%")
