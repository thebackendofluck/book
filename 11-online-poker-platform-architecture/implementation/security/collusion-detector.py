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
Collusion Detection Engine
Chapter 4 - Online Poker Platform Architecture

Detects collusive behavior between poker players:
- Chip dumping: Intentional losing to transfer chips
- Soft play: Failing to bet/raise against a partner
- Information sharing: Coordinated play indicating shared hole cards
- Graph analysis: Community detection in player interaction networks
- Session correlation: Players who always appear at the same tables

Uses weighted graph analysis and statistical anomaly detection.

Dependencies: None (stdlib only)
"""

import math
import time
import logging
import statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("poker.collusion_detection")


# ─── Configuration ────────────────────────────────────────────────────

class CollusionConfig:
    # Co-occurrence thresholds
    MIN_SHARED_HANDS = 30           # Minimum hands together before analysis
    CO_OCCURRENCE_SUSPICIOUS = 0.7  # % of sessions they appear together
    CO_OCCURRENCE_CRITICAL = 0.9

    # Chip dumping detection
    CHIP_DUMP_WIN_RATE_THRESHOLD = 0.80  # One player wins 80%+ of contested pots
    CHIP_DUMP_MIN_POTS = 10              # Minimum contested pots for analysis
    CHIP_DUMP_FOLD_TO_RAISE = 0.90       # Folds to raise 90%+ of the time

    # Soft play detection
    SOFT_PLAY_CHECK_RATIO = 0.75    # Checks behind 75%+ when heads-up
    SOFT_PLAY_MIN_SITUATIONS = 15

    # Network analysis
    GRAPH_EDGE_WEIGHT_THRESHOLD = 0.5  # Minimum edge weight for suspicion
    CLUSTER_SIZE_MIN = 2
    CLUSTER_SIZE_MAX = 8               # Collusion rings rarely exceed 8

    # Risk thresholds
    THRESHOLD_FLAG = 0.5
    THRESHOLD_INVESTIGATE = 0.7
    THRESHOLD_FREEZE = 0.85

    # IP and device correlation
    SHARED_IP_WEIGHT = 0.3
    SHARED_DEVICE_WEIGHT = 0.4


class CollusionRisk(Enum):
    CLEAR = "clear"
    MONITORING = "monitoring"
    FLAGGED = "flagged"
    INVESTIGATE = "investigate"
    FROZEN = "frozen"


# ─── Data Structures ─────────────────────────────────────────────────

@dataclass
class HandOutcome:
    """Record of a hand outcome between two players."""
    hand_id: str
    table_id: str
    timestamp: float
    player_a: str
    player_b: str
    winner: str               # player_id of winner
    pot_size: int
    was_contested: bool       # Both players had action (not just one folded pre)
    player_a_action: str      # Last significant action
    player_b_action: str
    went_to_showdown: bool


@dataclass
class PlayerPairStats:
    """Statistics for a pair of players."""
    player_a: str
    player_b: str
    hands_together: int = 0
    sessions_together: int = 0
    total_sessions_a: int = 0
    total_sessions_b: int = 0
    contested_pots: int = 0
    wins_a: int = 0
    wins_b: int = 0
    chip_flow_a_to_b: int = 0   # Net chips from A to B
    folds_a_to_b_raise: int = 0 # A folds to B's raise
    folds_b_to_a_raise: int = 0
    raises_a_to_b: int = 0      # A raises when B is in pot
    raises_b_to_a: int = 0
    checks_behind_a: int = 0    # A checks when could bet vs B
    checks_behind_b: int = 0
    check_opportunities_a: int = 0
    check_opportunities_b: int = 0
    showdowns: int = 0
    shared_ips: set = field(default_factory=set)
    shared_devices: set = field(default_factory=set)
    hand_outcomes: list = field(default_factory=list)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


# ─── Collusion Detector ──────────────────────────────────────────────

class CollusionDetector:
    """
    Detects collusion between poker players using interaction network analysis.

    Key detection methods:
    1. Chip dumping: Asymmetric win rates and intentional losing
    2. Soft play: Not value-betting or raising against partner
    3. Co-occurrence: Suspicious frequency of appearing at same tables
    4. Network clustering: Graph-based community detection for rings
    """

    def __init__(self, config: CollusionConfig = None):  # ty:ignore[invalid-parameter-default]
        self.config = config or CollusionConfig()
        self.pair_stats: dict[tuple, PlayerPairStats] = {}
        # Adjacency list for player interaction graph
        self.graph: dict[str, dict[str, float]] = defaultdict(dict)
        self._session_tracker: dict[str, set] = defaultdict(set)  # session_id -> {player_ids}

    def _pair_key(self, player_a: str, player_b: str) -> tuple:
        """Canonical pair key (sorted for consistency)."""
        return tuple(sorted([player_a, player_b]))

    def _get_pair_stats(self, player_a: str, player_b: str) -> PlayerPairStats:
        key = self._pair_key(player_a, player_b)
        if key not in self.pair_stats:
            self.pair_stats[key] = PlayerPairStats(player_a=key[0], player_b=key[1])
        return self.pair_stats[key]

    # ─── Data Ingestion ───────────────────────────────────────────

    def record_hand(self, outcome: HandOutcome):
        """Record a hand outcome for analysis."""
        stats = self._get_pair_stats(outcome.player_a, outcome.player_b)
        stats.hands_together += 1
        stats.last_seen = outcome.timestamp
        stats.hand_outcomes.append(outcome)

        # Cap history
        if len(stats.hand_outcomes) > 1000:
            stats.hand_outcomes = stats.hand_outcomes[-500:]

        if outcome.was_contested:
            stats.contested_pots += 1
            if outcome.winner == stats.player_a:
                stats.wins_a += 1
                stats.chip_flow_a_to_b -= outcome.pot_size
            elif outcome.winner == stats.player_b:
                stats.wins_b += 1
                stats.chip_flow_a_to_b += outcome.pot_size

        if outcome.went_to_showdown:
            stats.showdowns += 1

    def record_fold_to_raise(self, folder: str, raiser: str):
        """Record when one player folds to another's raise."""
        stats = self._get_pair_stats(folder, raiser)
        if folder == stats.player_a:
            stats.folds_a_to_b_raise += 1
        else:
            stats.folds_b_to_a_raise += 1

    def record_raise(self, raiser: str, opponent: str):
        """Record a raise against a specific opponent."""
        stats = self._get_pair_stats(raiser, opponent)
        if raiser == stats.player_a:
            stats.raises_a_to_b += 1
        else:
            stats.raises_b_to_a += 1

    def record_check_behind(self, checker: str, opponent: str, had_opportunity: bool = True):
        """Record when a player checks behind vs a specific opponent."""
        stats = self._get_pair_stats(checker, opponent)
        if checker == stats.player_a:
            stats.checks_behind_a += 1
            if had_opportunity:
                stats.check_opportunities_a += 1
        else:
            stats.checks_behind_b += 1
            if had_opportunity:
                stats.check_opportunities_b += 1

    def record_session(self, session_id: str, player_ids: list):
        """Record which players were in a session together."""
        self._session_tracker[session_id] = set(player_ids)

        # Update co-occurrence for all pairs
        for i, pid_a in enumerate(player_ids):
            for pid_b in player_ids[i+1:]:
                stats = self._get_pair_stats(pid_a, pid_b)
                stats.sessions_together += 1

    def record_connection_info(self, player_a: str, player_b: str,
                                ip_address: str = None, device_id: str = None):  # ty:ignore[invalid-parameter-default]
        """Record shared IP or device between players."""
        stats = self._get_pair_stats(player_a, player_b)
        if ip_address:
            stats.shared_ips.add(ip_address)
        if device_id:
            stats.shared_devices.add(device_id)

    # ─── Analysis ─────────────────────────────────────────────────

    def analyze_pair(self, player_a: str, player_b: str) -> dict:
        """
        Analyze a specific player pair for collusion signals.

        Returns:
            {
                "players": (player_a, player_b),
                "risk_score": float,
                "risk_level": CollusionRisk,
                "signals": [...],
                "recommendation": str,
            }
        """
        stats = self._get_pair_stats(player_a, player_b)
        if stats.hands_together < self.config.MIN_SHARED_HANDS:
            return {
                "players": (player_a, player_b),
                "risk_score": 0.0,
                "risk_level": CollusionRisk.CLEAR,
                "signals": [],
                "recommendation": f"Insufficient data ({stats.hands_together}/{self.config.MIN_SHARED_HANDS} hands)",
            }

        signals = []
        signals.append(self._detect_chip_dumping(stats))
        signals.append(self._detect_soft_play(stats))
        signals.append(self._detect_co_occurrence(stats))
        signals.append(self._detect_shared_infrastructure(stats))

        # Weighted composite
        weights = {
            "chip_dumping": 0.35,
            "soft_play": 0.25,
            "co_occurrence": 0.20,
            "shared_infra": 0.20,
        }

        risk_score = sum(s["score"] * weights.get(s["name"], 0.1) for s in signals)
        risk_score = min(1.0, max(0.0, risk_score))

        if risk_score >= self.config.THRESHOLD_FREEZE:
            risk_level = CollusionRisk.FROZEN
        elif risk_score >= self.config.THRESHOLD_INVESTIGATE:
            risk_level = CollusionRisk.INVESTIGATE
        elif risk_score >= self.config.THRESHOLD_FLAG:
            risk_level = CollusionRisk.FLAGGED
        elif risk_score >= 0.3:
            risk_level = CollusionRisk.MONITORING
        else:
            risk_level = CollusionRisk.CLEAR

        # Update graph edge weight
        self.graph[player_a][player_b] = risk_score
        self.graph[player_b][player_a] = risk_score

        return {
            "players": (player_a, player_b),
            "risk_score": round(risk_score, 3),
            "risk_level": risk_level,
            "signals": [s for s in signals if s["score"] > 0.1],
            "hands_analyzed": stats.hands_together,
            "contested_pots": stats.contested_pots,
            "net_chip_flow": stats.chip_flow_a_to_b,
            "recommendation": self._get_recommendation(risk_level, signals),
        }

    def _detect_chip_dumping(self, stats: PlayerPairStats) -> dict:
        """
        Detect chip dumping: one-directional chip flow with suspicious patterns.

        Indicators:
        - Highly asymmetric win rate in contested pots
        - Large net chip transfer
        - One player consistently folds to the other's raises
        """
        if stats.contested_pots < self.config.CHIP_DUMP_MIN_POTS:
            return {"name": "chip_dumping", "score": 0.0, "detail": "Too few contested pots"}

        score = 0.0
        details = []

        # Win rate asymmetry
        total = stats.wins_a + stats.wins_b
        if total > 0:
            win_rate_a = stats.wins_a / total
            win_rate_b = stats.wins_b / total
            max_win_rate = max(win_rate_a, win_rate_b)

            if max_win_rate >= self.config.CHIP_DUMP_WIN_RATE_THRESHOLD:
                dominant = stats.player_a if win_rate_a > win_rate_b else stats.player_b
                score += 0.6
                details.append(f"Asymmetric win rate: {dominant} wins {max_win_rate:.1%} of contested pots")

        # Fold-to-raise asymmetry
        total_folds = stats.folds_a_to_b_raise + stats.folds_b_to_a_raise
        if total_folds > 5:
            max_folds = max(stats.folds_a_to_b_raise, stats.folds_b_to_a_raise)
            fold_ratio = max_folds / total_folds
            if fold_ratio >= self.config.CHIP_DUMP_FOLD_TO_RAISE:
                score += 0.4
                always_folder = stats.player_a if stats.folds_a_to_b_raise > stats.folds_b_to_a_raise else stats.player_b
                details.append(f"{always_folder} folds to raise {fold_ratio:.1%} of the time")

        # Large net chip flow
        if abs(stats.chip_flow_a_to_b) > 0 and stats.contested_pots > 0:
            avg_pot = abs(stats.chip_flow_a_to_b) / stats.contested_pots
            flow_direction = "A->B" if stats.chip_flow_a_to_b > 0 else "B->A"
            if abs(stats.chip_flow_a_to_b) > avg_pot * stats.contested_pots * 0.5:
                score += 0.2
                details.append(f"Net chip flow: {abs(stats.chip_flow_a_to_b)} ({flow_direction})")

        return {
            "name": "chip_dumping",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else "No anomalies",
            "metrics": {
                "contested_pots": stats.contested_pots,
                "wins_a": stats.wins_a,
                "wins_b": stats.wins_b,
                "net_flow": stats.chip_flow_a_to_b,
            },
        }

    def _detect_soft_play(self, stats: PlayerPairStats) -> dict:
        """
        Detect soft play: failing to value-bet or raise against a partner.

        Indicators:
        - High check-behind ratio when heads-up
        - Low aggression against specific opponent vs others
        - Missing value bets with strong hands
        """
        score = 0.0
        details = []

        # Check-behind analysis for player A
        if stats.check_opportunities_a >= self.config.SOFT_PLAY_MIN_SITUATIONS:
            check_ratio_a = stats.checks_behind_a / stats.check_opportunities_a
            if check_ratio_a >= self.config.SOFT_PLAY_CHECK_RATIO:
                score += 0.4
                details.append(f"{stats.player_a} checks behind {check_ratio_a:.1%} vs {stats.player_b}")

        # Check-behind analysis for player B
        if stats.check_opportunities_b >= self.config.SOFT_PLAY_MIN_SITUATIONS:
            check_ratio_b = stats.checks_behind_b / stats.check_opportunities_b
            if check_ratio_b >= self.config.SOFT_PLAY_CHECK_RATIO:
                score += 0.4
                details.append(f"{stats.player_b} checks behind {check_ratio_b:.1%} vs {stats.player_a}")

        # Mutual soft play (both players passive against each other)
        if (stats.check_opportunities_a >= 10 and stats.check_opportunities_b >= 10):
            ratio_a = stats.checks_behind_a / max(stats.check_opportunities_a, 1)
            ratio_b = stats.checks_behind_b / max(stats.check_opportunities_b, 1)
            if ratio_a > 0.5 and ratio_b > 0.5:
                score += 0.3
                details.append("Mutual soft play detected")

        # Low raise frequency against each other
        total_raises = stats.raises_a_to_b + stats.raises_b_to_a
        if stats.contested_pots > 20 and total_raises < stats.contested_pots * 0.05:
            score += 0.2
            details.append(f"Very low raise frequency: {total_raises}/{stats.contested_pots} pots")

        return {
            "name": "soft_play",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else "Normal aggression patterns",
        }

    def _detect_co_occurrence(self, stats: PlayerPairStats) -> dict:
        """
        Detect suspicious co-occurrence: players who always play at the same tables.
        """
        score = 0.0
        details = []

        if stats.sessions_together > 5:
            # Calculate co-occurrence ratio
            max_sessions = max(stats.total_sessions_a, stats.total_sessions_b, stats.sessions_together)
            if max_sessions > 0:
                co_ratio = stats.sessions_together / max_sessions
                if co_ratio >= self.config.CO_OCCURRENCE_CRITICAL:
                    score = 0.8
                    details.append(f"Very high co-occurrence: {co_ratio:.1%} ({stats.sessions_together} sessions)")
                elif co_ratio >= self.config.CO_OCCURRENCE_SUSPICIOUS:
                    score = 0.4
                    details.append(f"High co-occurrence: {co_ratio:.1%} ({stats.sessions_together} sessions)")

        return {
            "name": "co_occurrence",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else "Normal table selection",
        }

    def _detect_shared_infrastructure(self, stats: PlayerPairStats) -> dict:
        """
        Detect shared IP addresses or device fingerprints.
        """
        score = 0.0
        details = []

        if stats.shared_ips:
            score += self.config.SHARED_IP_WEIGHT * len(stats.shared_ips)
            details.append(f"Shared IPs: {len(stats.shared_ips)}")

        if stats.shared_devices:
            score += self.config.SHARED_DEVICE_WEIGHT * len(stats.shared_devices)
            details.append(f"Shared devices: {len(stats.shared_devices)}")

        return {
            "name": "shared_infra",
            "score": min(1.0, score),
            "detail": "; ".join(details) if details else "No shared infrastructure",
        }

    # ─── Network / Graph Analysis ─────────────────────────────────

    def detect_collusion_rings(self, min_edge_weight: float = None) -> list:  # ty:ignore[invalid-parameter-default]
        """
        Find clusters of connected suspicious players using BFS-based
        community detection on the interaction graph.

        Returns:
            List of collusion ring candidates:
            [
                {
                    "members": ["p1", "p2", "p3"],
                    "avg_risk": 0.72,
                    "max_risk": 0.85,
                    "edges": [(p1,p2,0.8), (p2,p3,0.7), ...],
                },
            ]
        """
        threshold = min_edge_weight or self.config.GRAPH_EDGE_WEIGHT_THRESHOLD
        visited = set()
        rings = []

        for player in self.graph:
            if player in visited:
                continue

            # BFS to find connected component above threshold
            cluster = []
            edges = []
            queue = [player]
            component_visited = set()

            while queue:
                current = queue.pop(0)
                if current in component_visited:
                    continue
                component_visited.add(current)
                visited.add(current)

                has_suspicious_edge = False
                for neighbor, weight in self.graph.get(current, {}).items():
                    if weight >= threshold:
                        has_suspicious_edge = True
                        edges.append((current, neighbor, round(weight, 3)))
                        if neighbor not in component_visited:
                            queue.append(neighbor)

                if has_suspicious_edge:
                    cluster.append(current)

            if len(cluster) >= self.config.CLUSTER_SIZE_MIN:
                edge_weights = [w for _, _, w in edges]
                rings.append({
                    "members": sorted(cluster),
                    "size": len(cluster),
                    "avg_risk": round(statistics.mean(edge_weights), 3) if edge_weights else 0,
                    "max_risk": round(max(edge_weights), 3) if edge_weights else 0,
                    "edges": edges,
                })

        # Sort by risk
        rings.sort(key=lambda r: r["max_risk"], reverse=True)

        logger.info(f"Found {len(rings)} potential collusion ring(s)")
        return rings

    # ─── Helpers ──────────────────────────────────────────────────

    def _get_recommendation(self, risk_level: CollusionRisk, signals: list) -> str:
        top = sorted(signals, key=lambda s: s["score"], reverse=True)
        summary = ", ".join(s["name"] for s in top[:2] if s["score"] > 0.3)

        return {
            CollusionRisk.CLEAR: "No action needed",
            CollusionRisk.MONITORING: f"Continue monitoring ({summary})",
            CollusionRisk.FLAGGED: f"Manual review recommended. Signals: {summary}",
            CollusionRisk.INVESTIGATE: f"Deep investigation required. Signals: {summary}. Pull hand histories.",
            CollusionRisk.FROZEN: f"Freeze accounts immediately. Signals: {summary}. Escalate to fraud team.",
        }.get(risk_level, "Unknown")


# ─── Example / Demo ──────────────────────────────────────────────────

if __name__ == "__main__":
    import random

    detector = CollusionDetector()

    # ── Simulate normal players ───────────────────────────────────
    print("=== Simulating normal player pair ===")
    for i in range(50):
        winner = random.choice(["normal_A", "normal_B"])
        detector.record_hand(HandOutcome(
            hand_id=f"hand_{i}",
            table_id="table_1",
            timestamp=time.time() + i,
            player_a="normal_A",
            player_b="normal_B",
            winner=winner,
            pot_size=random.randint(100, 2000),
            was_contested=random.random() > 0.3,
            player_a_action=random.choice(["call", "raise", "check"]),
            player_b_action=random.choice(["call", "raise", "check"]),
            went_to_showdown=random.random() > 0.5,
        ))
        if random.random() > 0.7:
            detector.record_raise("normal_A", "normal_B")
        if random.random() > 0.7:
            detector.record_raise("normal_B", "normal_A")

    result = detector.analyze_pair("normal_A", "normal_B")
    print(f"Risk: {result['risk_score']:.3f} ({result['risk_level'].value})")
    print(f"Recommendation: {result['recommendation']}")

    # ── Simulate colluding players ────────────────────────────────
    print("\n=== Simulating COLLUDING player pair ===")
    for i in range(50):
        # Player B almost always wins against player A (chip dumping)
        winner = "colluder_B" if random.random() < 0.85 else "colluder_A"
        detector.record_hand(HandOutcome(
            hand_id=f"hand_c_{i}",
            table_id="table_2",
            timestamp=time.time() + i,
            player_a="colluder_A",
            player_b="colluder_B",
            winner=winner,
            pot_size=random.randint(500, 5000),
            was_contested=True,
            player_a_action="fold" if winner == "colluder_B" else "call",
            player_b_action="raise",
            went_to_showdown=random.random() > 0.7,
        ))
        # A always folds to B's raise
        if random.random() > 0.1:
            detector.record_fold_to_raise("colluder_A", "colluder_B")
        # A never raises B (soft play)
        detector.record_check_behind("colluder_A", "colluder_B", had_opportunity=True)

    # They share an IP
    detector.record_connection_info("colluder_A", "colluder_B", ip_address="192.168.1.100")

    # High co-occurrence
    for s in range(20):
        detector.record_session(f"session_{s}", ["colluder_A", "colluder_B"])

    stats = detector._get_pair_stats("colluder_A", "colluder_B")
    stats.total_sessions_a = 22
    stats.total_sessions_b = 21

    result = detector.analyze_pair("colluder_A", "colluder_B")
    print(f"Risk: {result['risk_score']:.3f} ({result['risk_level'].value})")
    print(f"Net chip flow: {result['net_chip_flow']}")
    for signal in result["signals"]:
        print(f"  {signal['name']}: {signal['score']:.2f} - {signal['detail']}")
    print(f"Recommendation: {result['recommendation']}")

    # ── Detect rings ──────────────────────────────────────────────
    print("\n=== Collusion Ring Detection ===")
    rings = detector.detect_collusion_rings(min_edge_weight=0.3)
    for ring in rings:
        print(f"Ring: {ring['members']} (avg risk: {ring['avg_risk']}, max: {ring['max_risk']})")
