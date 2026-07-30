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
Bot Detection Engine
Chapter 4 - Online Poker Platform Architecture

Detects automated play (bots) through behavioral analysis:
- Reaction time distribution (bots have unnaturally consistent timing)
- Action pattern entropy (bots are more predictable)
- Session duration anomalies (bots play marathon sessions)
- Bet sizing patterns (bots use fixed ratios)
- Multi-tabling correlation (synchronized actions across tables)
- Mouse movement / input analysis (client-side telemetry)

Scoring model outputs a risk score 0.0-1.0 with configurable thresholds.

Dependencies: None (stdlib only - numpy optional for production)
"""

import math
import time
import logging
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("poker.bot_detection")


# ─── Configuration ────────────────────────────────────────────────────

class BotDetectorConfig:
    # Reaction time thresholds (milliseconds)
    MIN_HUMAN_REACTION_MS = 300         # Below this is suspicious
    MAX_REASONABLE_REACTION_MS = 30000  # Above this is AFK, not bot

    # Coefficient of variation thresholds
    # Humans typically have CV > 0.4 for reaction times
    # Bots typically have CV < 0.15
    REACTION_CV_SUSPICIOUS = 0.20
    REACTION_CV_CRITICAL = 0.10

    # Entropy thresholds (bits)
    # Higher entropy = more random/human-like decisions
    ACTION_ENTROPY_SUSPICIOUS = 1.5
    ACTION_ENTROPY_CRITICAL = 1.0

    # Session duration (hours)
    MAX_CONTINUOUS_SESSION_H = 8
    SUSPICIOUS_SESSION_H = 12

    # Bet sizing consistency (lower = more bot-like)
    BET_SIZE_CV_SUSPICIOUS = 0.15

    # Risk score thresholds
    THRESHOLD_FLAG = 0.5       # Flag for review
    THRESHOLD_RESTRICT = 0.7   # Restrict to play money
    THRESHOLD_BAN = 0.85       # Automated ban pending review

    # Analysis windows
    MIN_ACTIONS_FOR_ANALYSIS = 50
    ROLLING_WINDOW_ACTIONS = 500


# ─── Risk Level ───────────────────────────────────────────────────────

class RiskLevel(Enum):
    CLEAR = "clear"
    MONITORING = "monitoring"
    FLAGGED = "flagged"
    RESTRICTED = "restricted"
    BANNED = "banned"


# ─── Data Structures ─────────────────────────────────────────────────

@dataclass
class ActionTelemetry:
    """Telemetry data for a single player action."""
    player_id: str
    table_id: str
    hand_id: str
    action: str              # fold, check, call, bet, raise, all_in
    amount: int
    pot_size: int
    reaction_time_ms: int    # Time from action prompt to response
    timestamp: float
    # Client-side telemetry (optional)
    mouse_movements: int = 0       # Number of mouse movements before action
    mouse_distance_px: float = 0   # Total mouse travel distance
    click_precision_px: float = 0  # Distance from button center


@dataclass
class PlayerProfile:
    """Accumulated behavioral profile for a player."""
    player_id: str
    reaction_times: deque = field(default_factory=lambda: deque(maxlen=500))
    action_counts: dict = field(default_factory=lambda: defaultdict(int))
    bet_sizes: deque = field(default_factory=lambda: deque(maxlen=200))
    bet_to_pot_ratios: deque = field(default_factory=lambda: deque(maxlen=200))
    session_start: float = field(default_factory=time.time)
    total_actions: int = 0
    tables_active: set = field(default_factory=set)
    cross_table_timestamps: deque = field(default_factory=lambda: deque(maxlen=100))
    mouse_movement_counts: deque = field(default_factory=lambda: deque(maxlen=200))
    last_updated: float = field(default_factory=time.time)
    risk_scores: list = field(default_factory=list)
    flags: list = field(default_factory=list)


# ─── Bot Detector ─────────────────────────────────────────────────────

class BotDetector:
    """
    Real-time bot detection engine.

    Usage:
        detector = BotDetector()
        for action in action_stream:
            result = detector.analyze_action(action)
            if result["risk_level"] == RiskLevel.FLAGGED:
                alert_compliance_team(result)
    """

    def __init__(self, config: BotDetectorConfig = None):  # ty:ignore[invalid-parameter-default]
        self.config = config or BotDetectorConfig()
        self.profiles: dict[str, PlayerProfile] = {}
        self._alert_callbacks = []

    def on_alert(self, callback):
        """Register callback for risk alerts."""
        self._alert_callbacks.append(callback)

    def analyze_action(self, telemetry: ActionTelemetry) -> dict:
        """
        Analyze a single action and update the player's risk profile.

        Returns:
            {
                "player_id": str,
                "risk_score": float (0.0 - 1.0),
                "risk_level": RiskLevel,
                "signals": [{"name": str, "score": float, "detail": str}, ...],
                "recommendation": str,
            }
        """
        profile = self._get_or_create_profile(telemetry.player_id)
        self._update_profile(profile, telemetry)

        # Not enough data yet
        if profile.total_actions < self.config.MIN_ACTIONS_FOR_ANALYSIS:
            return {
                "player_id": telemetry.player_id,
                "risk_score": 0.0,
                "risk_level": RiskLevel.CLEAR,
                "signals": [],
                "recommendation": f"Collecting data ({profile.total_actions}/{self.config.MIN_ACTIONS_FOR_ANALYSIS})",
            }

        # Run all detection signals
        signals = []
        signals.append(self._analyze_reaction_time(profile))
        signals.append(self._analyze_action_entropy(profile))
        signals.append(self._analyze_session_duration(profile))
        signals.append(self._analyze_bet_sizing(profile))
        signals.append(self._analyze_multi_table_correlation(profile))
        signals.append(self._analyze_input_behavior(profile))

        # Weighted composite score
        weights = {
            "reaction_time": 0.25,
            "action_entropy": 0.20,
            "session_duration": 0.10,
            "bet_sizing": 0.15,
            "multi_table": 0.15,
            "input_behavior": 0.15,
        }

        risk_score = sum(
            s["score"] * weights.get(s["name"], 0.1)
            for s in signals
        )
        risk_score = min(1.0, max(0.0, risk_score))

        # Determine risk level
        if risk_score >= self.config.THRESHOLD_BAN:
            risk_level = RiskLevel.BANNED
        elif risk_score >= self.config.THRESHOLD_RESTRICT:
            risk_level = RiskLevel.RESTRICTED
        elif risk_score >= self.config.THRESHOLD_FLAG:
            risk_level = RiskLevel.FLAGGED
        elif risk_score >= 0.3:
            risk_level = RiskLevel.MONITORING
        else:
            risk_level = RiskLevel.CLEAR

        recommendation = self._get_recommendation(risk_level, signals)

        result = {
            "player_id": telemetry.player_id,
            "risk_score": round(risk_score, 3),
            "risk_level": risk_level,
            "signals": [s for s in signals if s["score"] > 0.1],
            "recommendation": recommendation,
            "actions_analyzed": profile.total_actions,
        }

        # Store score history
        profile.risk_scores.append(risk_score)
        if len(profile.risk_scores) > 100:
            profile.risk_scores = profile.risk_scores[-50:]

        # Fire alerts
        if risk_level in (RiskLevel.FLAGGED, RiskLevel.RESTRICTED, RiskLevel.BANNED):
            for callback in self._alert_callbacks:
                callback(result)

        return result

    # ─── Signal Analyzers ─────────────────────────────────────────

    def _analyze_reaction_time(self, profile: PlayerProfile) -> dict:
        """
        Analyze reaction time distribution.

        Bots tend to have:
        - Very low coefficient of variation (consistent timing)
        - Reaction times clustering at specific intervals
        - Very few sub-500ms reactions mixed with machine-precise timing
        """
        times = list(profile.reaction_times)
        if len(times) < 20:
            return {"name": "reaction_time", "score": 0.0, "detail": "Insufficient data"}

        mean_rt = statistics.mean(times)
        stdev_rt = statistics.stdev(times) if len(times) > 1 else 0
        cv = stdev_rt / mean_rt if mean_rt > 0 else 0

        score = 0.0
        details = []

        # Low coefficient of variation = suspicious
        if cv < self.config.REACTION_CV_CRITICAL:
            score += 0.9
            details.append(f"Very low CV: {cv:.3f} (threshold: {self.config.REACTION_CV_CRITICAL})")
        elif cv < self.config.REACTION_CV_SUSPICIOUS:
            score += 0.5
            details.append(f"Low CV: {cv:.3f} (threshold: {self.config.REACTION_CV_SUSPICIOUS})")

        # Check for unnaturally fast reactions
        fast_count = sum(1 for t in times if t < self.config.MIN_HUMAN_REACTION_MS)
        fast_ratio = fast_count / len(times)
        if fast_ratio > 0.1:
            score += 0.3
            details.append(f"Fast reactions: {fast_ratio:.1%} < {self.config.MIN_HUMAN_REACTION_MS}ms")

        # Check for periodic patterns (GCD of reaction times)
        rounded = [round(t / 50) * 50 for t in times[:100]]
        most_common = max(set(rounded), key=rounded.count)
        repeat_ratio = rounded.count(most_common) / len(rounded)
        if repeat_ratio > 0.3:
            score += 0.2
            details.append(f"Timing pattern detected: {most_common}ms occurs {repeat_ratio:.1%}")

        return {
            "name": "reaction_time",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal (CV={cv:.3f}, mean={mean_rt:.0f}ms)",
            "metrics": {"mean_ms": round(mean_rt), "stdev_ms": round(stdev_rt), "cv": round(cv, 3)},
        }

    def _analyze_action_entropy(self, profile: PlayerProfile) -> dict:
        """
        Measure Shannon entropy of action distribution.

        Human players show higher entropy (more varied decisions).
        Bots following fixed strategies show lower entropy.
        """
        total = sum(profile.action_counts.values())
        if total < 30:
            return {"name": "action_entropy", "score": 0.0, "detail": "Insufficient data"}

        # Calculate Shannon entropy
        entropy = 0.0
        for count in profile.action_counts.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)

        # Max entropy for 6 actions = log2(6) ≈ 2.585
        max_entropy = math.log2(max(len(profile.action_counts), 1))
        normalized = entropy / max_entropy if max_entropy > 0 else 0

        score = 0.0
        details = []

        if entropy < self.config.ACTION_ENTROPY_CRITICAL:
            score = 0.8
            details.append(f"Very low entropy: {entropy:.2f} bits (threshold: {self.config.ACTION_ENTROPY_CRITICAL})")
        elif entropy < self.config.ACTION_ENTROPY_SUSPICIOUS:
            score = 0.4
            details.append(f"Low entropy: {entropy:.2f} bits (threshold: {self.config.ACTION_ENTROPY_SUSPICIOUS})")

        # Check for VPIP (voluntarily put in pot) anomalies
        # Bots often have extreme VPIP (very tight or very loose)
        voluntary = profile.action_counts.get("call", 0) + profile.action_counts.get("raise", 0) + \
                    profile.action_counts.get("bet", 0) + profile.action_counts.get("all_in", 0)
        vpip = voluntary / total if total > 0 else 0

        if vpip < 0.08 or vpip > 0.65:
            score += 0.2
            details.append(f"Extreme VPIP: {vpip:.1%}")

        return {
            "name": "action_entropy",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal (entropy={entropy:.2f}, VPIP={vpip:.1%})",
            "metrics": {
                "entropy_bits": round(entropy, 3),
                "normalized": round(normalized, 3),
                "vpip": round(vpip, 3),
                "action_distribution": dict(profile.action_counts),
            },
        }

    def _analyze_session_duration(self, profile: PlayerProfile) -> dict:
        """
        Detect marathon sessions indicative of automated play.
        """
        duration_h = (time.time() - profile.session_start) / 3600
        score = 0.0
        details = []

        if duration_h > self.config.SUSPICIOUS_SESSION_H:
            score = 0.7
            details.append(f"Marathon session: {duration_h:.1f}h (threshold: {self.config.SUSPICIOUS_SESSION_H}h)")
        elif duration_h > self.config.MAX_CONTINUOUS_SESSION_H:
            score = 0.3
            details.append(f"Long session: {duration_h:.1f}h (threshold: {self.config.MAX_CONTINUOUS_SESSION_H}h)")

        # Actions per hour (bots tend to be very consistent)
        aph = profile.total_actions / max(duration_h, 0.01)
        if aph > 600:  # More than 10 actions per minute sustained
            score += 0.3
            details.append(f"High action rate: {aph:.0f}/hour")

        return {
            "name": "session_duration",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal ({duration_h:.1f}h, {aph:.0f} actions/h)",
            "metrics": {"duration_hours": round(duration_h, 2), "actions_per_hour": round(aph)},
        }

    def _analyze_bet_sizing(self, profile: PlayerProfile) -> dict:
        """
        Analyze bet sizing patterns. Bots often use fixed pot ratios.
        """
        ratios = list(profile.bet_to_pot_ratios)
        if len(ratios) < 15:
            return {"name": "bet_sizing", "score": 0.0, "detail": "Insufficient bet data"}

        mean_ratio = statistics.mean(ratios)
        stdev_ratio = statistics.stdev(ratios) if len(ratios) > 1 else 0
        cv = stdev_ratio / mean_ratio if mean_ratio > 0 else 0

        score = 0.0
        details = []

        if cv < self.config.BET_SIZE_CV_SUSPICIOUS:
            score = 0.7
            details.append(f"Very consistent bet sizing (CV={cv:.3f})")

        # Check for exact pot-fraction betting (1/2, 2/3, 3/4 pot)
        common_fractions = [0.33, 0.50, 0.67, 0.75, 1.0]
        for frac in common_fractions:
            near_fraction = sum(1 for r in ratios if abs(r - frac) < 0.02)
            if near_fraction / len(ratios) > 0.5:
                score += 0.3
                details.append(f"Fixed {frac:.0%} pot betting: {near_fraction}/{len(ratios)}")
                break

        return {
            "name": "bet_sizing",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal (CV={cv:.3f}, mean ratio={mean_ratio:.2f})",
            "metrics": {"cv": round(cv, 3), "mean_pot_ratio": round(mean_ratio, 3)},
        }

    def _analyze_multi_table_correlation(self, profile: PlayerProfile) -> dict:
        """
        Detect synchronized actions across multiple tables,
        indicating programmatic multi-tabling.
        """
        timestamps = list(profile.cross_table_timestamps)
        if len(timestamps) < 10 or len(profile.tables_active) < 2:
            return {"name": "multi_table", "score": 0.0, "detail": "Single table or insufficient data"}

        # Calculate time deltas between consecutive actions across tables
        timestamps.sort()
        deltas = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps) - 1)]

        if not deltas:
            return {"name": "multi_table", "score": 0.0, "detail": "No cross-table data"}

        mean_delta = statistics.mean(deltas)
        score = 0.0
        details = []

        # Very rapid table switching (< 200ms between actions on different tables)
        rapid = sum(1 for d in deltas if d < 0.2)
        rapid_ratio = rapid / len(deltas)
        if rapid_ratio > 0.3:
            score = 0.6
            details.append(f"Rapid table switching: {rapid_ratio:.1%} actions < 200ms apart")

        # Perfectly periodic switching
        if len(deltas) > 5:
            delta_cv = statistics.stdev(deltas) / mean_delta if mean_delta > 0 else 0
            if delta_cv < 0.1:
                score += 0.4
                details.append(f"Periodic switching pattern (CV={delta_cv:.3f})")

        return {
            "name": "multi_table",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal ({len(profile.tables_active)} tables)",
            "metrics": {"tables": len(profile.tables_active), "mean_switch_ms": round(mean_delta * 1000)},
        }

    def _analyze_input_behavior(self, profile: PlayerProfile) -> dict:
        """
        Analyze mouse/input patterns from client telemetry.
        Bots have zero or minimal mouse movement.
        """
        movements = list(profile.mouse_movement_counts)
        if len(movements) < 20:
            return {"name": "input_behavior", "score": 0.0, "detail": "No input telemetry"}

        mean_movements = statistics.mean(movements)
        score = 0.0
        details = []

        # Zero mouse movement is highly suspicious (keyboard-only or API)
        zero_count = sum(1 for m in movements if m == 0)
        zero_ratio = zero_count / len(movements)
        if zero_ratio > 0.8:
            score = 0.8
            details.append(f"No mouse movement: {zero_ratio:.1%} of actions")
        elif mean_movements < 3:
            score = 0.4
            details.append(f"Minimal mouse movement: avg {mean_movements:.1f} per action")

        return {
            "name": "input_behavior",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else f"Normal (avg {mean_movements:.1f} movements)",
        }

    # ─── Helpers ──────────────────────────────────────────────────

    def _get_or_create_profile(self, player_id: str) -> PlayerProfile:
        if player_id not in self.profiles:
            self.profiles[player_id] = PlayerProfile(player_id=player_id)
        return self.profiles[player_id]

    def _update_profile(self, profile: PlayerProfile, telemetry: ActionTelemetry):
        profile.reaction_times.append(telemetry.reaction_time_ms)
        profile.action_counts[telemetry.action] += 1
        profile.total_actions += 1
        profile.tables_active.add(telemetry.table_id)
        profile.cross_table_timestamps.append(telemetry.timestamp)
        profile.last_updated = telemetry.timestamp

        if telemetry.amount > 0 and telemetry.pot_size > 0:
            profile.bet_sizes.append(telemetry.amount)
            profile.bet_to_pot_ratios.append(telemetry.amount / telemetry.pot_size)

        if telemetry.mouse_movements >= 0:
            profile.mouse_movement_counts.append(telemetry.mouse_movements)

    def _get_recommendation(self, risk_level: RiskLevel, signals: list) -> str:
        top_signals = sorted(signals, key=lambda s: s["score"], reverse=True)[:3]
        signal_summary = ", ".join(s["name"] for s in top_signals if s["score"] > 0.3)

        recommendations = {
            RiskLevel.CLEAR: "No action needed",
            RiskLevel.MONITORING: f"Continue monitoring ({signal_summary})",
            RiskLevel.FLAGGED: f"Manual review recommended. Signals: {signal_summary}",
            RiskLevel.RESTRICTED: f"Restrict to play money pending review. Signals: {signal_summary}",
            RiskLevel.BANNED: f"Automated ban. Escalate to compliance. Signals: {signal_summary}",
        }
        return recommendations.get(risk_level, "Unknown")

    def get_player_report(self, player_id: str) -> dict:
        """Generate a full report for a player."""
        profile = self.profiles.get(player_id)
        if not profile:
            return {"player_id": player_id, "status": "No data"}

        return {
            "player_id": player_id,
            "total_actions": profile.total_actions,
            "session_duration_h": round((time.time() - profile.session_start) / 3600, 2),
            "tables_played": len(profile.tables_active),
            "action_distribution": dict(profile.action_counts),
            "avg_reaction_ms": round(statistics.mean(profile.reaction_times)) if profile.reaction_times else 0,
            "reaction_cv": round(
                statistics.stdev(profile.reaction_times) / statistics.mean(profile.reaction_times), 3
            ) if len(profile.reaction_times) > 1 and statistics.mean(profile.reaction_times) > 0 else 0,
            "risk_score_history": profile.risk_scores[-10:],
            "flags": profile.flags,
        }


# ─── Example / Demo ──────────────────────────────────────────────────

if __name__ == "__main__":
    import random

    detector = BotDetector()

    def alert_handler(result):
        print(f"\n*** ALERT: Player {result['player_id']} - "
              f"Risk: {result['risk_score']:.2f} ({result['risk_level'].value}) ***")
        for signal in result["signals"]:
            print(f"    {signal['name']}: {signal['score']:.2f} - {signal['detail']}")
        print(f"    Recommendation: {result['recommendation']}")

    detector.on_alert(alert_handler)

    # ── Simulate normal human player ──────────────────────────────
    print("=== Simulating HUMAN player ===")
    for i in range(100):
        telemetry = ActionTelemetry(
            player_id="human_player_1",
            table_id=f"table_{random.randint(1, 2)}",
            hand_id=f"hand_{i}",
            action=random.choice(["fold", "fold", "call", "raise", "check", "bet"]),
            amount=random.randint(0, 500),
            pot_size=random.randint(200, 2000),
            reaction_time_ms=random.gauss(2500, 1200),  # Human: high variance  # ty:ignore[invalid-argument-type]
            timestamp=time.time() + i * random.uniform(5, 30),
            mouse_movements=random.randint(3, 25),
        )
        result = detector.analyze_action(telemetry)

    print(f"Final risk score: {result['risk_score']:.3f} ({result['risk_level'].value})")

    # ── Simulate bot player ───────────────────────────────────────
    print("\n=== Simulating BOT player ===")
    for i in range(100):
        telemetry = ActionTelemetry(
            player_id="bot_player_1",
            table_id=f"table_{i % 8 + 1}",  # 8 tables
            hand_id=f"hand_{i}",
            action=random.choice(["fold", "fold", "fold", "call", "raise"]),
            amount=random.randint(0, 500),
            pot_size=max(200, random.randint(200, 2000)),
            reaction_time_ms=random.gauss(800, 50),  # Bot: very low variance  # ty:ignore[invalid-argument-type]
            timestamp=time.time() + i * 0.5,          # Rapid, periodic
            mouse_movements=0,                         # No mouse
        )
        result = detector.analyze_action(telemetry)

    print(f"Final risk score: {result['risk_score']:.3f} ({result['risk_level'].value})")

    # ── Print reports ─────────────────────────────────────────────
    print("\n=== Player Reports ===")
    for pid in ["human_player_1", "bot_player_1"]:
        report = detector.get_player_report(pid)
        print(f"\n{pid}:")
        print(f"  Actions: {report['total_actions']}")
        print(f"  Avg reaction: {report['avg_reaction_ms']}ms (CV: {report['reaction_cv']})")
        print(f"  Actions: {report['action_distribution']}")
