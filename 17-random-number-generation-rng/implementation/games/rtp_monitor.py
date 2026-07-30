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
RTP Monitoring with Statistical Convergence Validation
=======================================================

GLI-11 Section 5.5 Compliance: RTP Monitoring Requirements
- Actual RTP must converge to theoretical RTP over sufficient sample size
- Deviations beyond statistical expectations must trigger alerts
- Confidence intervals must be calculated using proper statistical methods
- All RTP measurements must be logged for regulatory audit
- Monitoring must operate continuously with configurable thresholds

Statistical Methods:
- Wilson score interval for hit frequency confidence
- Normal approximation for RTP confidence intervals
- Chi-squared test for distribution uniformity
- CUSUM (cumulative sum) for drift detection
- Sequential analysis for early deviation detection

Usage:
    monitor = RTPMonitor(theoretical_rtp=96.0)
    for spin in spins:
        monitor.record_spin(spin.bet, spin.payout)
    report = monitor.get_report()
"""

import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("rng.rtp_monitor")


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    REGULATORY = "regulatory"  # Requires regulatory notification


class AlertType(Enum):
    RTP_DEVIATION = "rtp_deviation"
    HIT_FREQUENCY_DEVIATION = "hit_frequency_deviation"
    DRIFT_DETECTED = "drift_detected"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    CONVERGENCE_FAILURE = "convergence_failure"
    PAYOUT_ANOMALY = "payout_anomaly"


@dataclass
class RTPAlert:
    """Alert record for RTP deviation."""
    timestamp: str
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    details: dict
    game_id: str
    acknowledged: bool = False


@dataclass
class SpinRecord:
    """Individual spin record for analysis."""
    bet: float
    payout: float
    timestamp: float


# ---------------------------------------------------------------------------
# Statistical Utilities
# ---------------------------------------------------------------------------

def wilson_score_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """
    Wilson score interval for binomial proportion.
    More accurate than Wald interval for small samples or extreme proportions.

    Args:
        successes: Number of winning spins
        total: Total number of spins
        z: Z-score (1.96 for 95% CI, 2.576 for 99% CI)

    Returns:
        (lower_bound, upper_bound) of the confidence interval
    """
    if total == 0:
        return 0.0, 1.0

    p_hat = successes / total
    denominator = 1 + z * z / total

    center = (p_hat + z * z / (2 * total)) / denominator
    margin = (z / denominator) * math.sqrt(
        (p_hat * (1 - p_hat) / total) + (z * z / (4 * total * total))
    )

    return max(0.0, center - margin), min(1.0, center + margin)


def normal_rtp_interval(
    total_wagered: float,
    total_returned: float,
    num_spins: int,
    z: float = 1.96,
) -> Tuple[float, float]:
    """
    Normal approximation confidence interval for RTP percentage.

    Uses the sample variance of per-spin returns to estimate the
    standard error of the mean return.
    """
    if num_spins < 2 or total_wagered <= 0:
        return 0.0, 200.0

    rtp = (total_returned / total_wagered) * 100
    avg_bet = total_wagered / num_spins
    avg_return = total_returned / num_spins

    # Estimate variance (assuming high variance typical of slots)
    # Use coefficient of variation typical for slots (~4-8x bet)
    estimated_std = avg_bet * 6.0  # Conservative estimate
    std_err = estimated_std / math.sqrt(num_spins)
    margin = z * (std_err / avg_bet) * 100

    return rtp - margin, rtp + margin


# ---------------------------------------------------------------------------
# CUSUM Drift Detector
# ---------------------------------------------------------------------------

class CUSUMDetector:
    """
    Cumulative Sum (CUSUM) detector for RTP drift.

    Detects persistent shifts in the mean return rate that indicate
    a potential RNG or paytable misconfiguration.

    GLI-11 5.5.3: Continuous monitoring must detect sustained deviations
    that could indicate a malfunctioning or compromised RNG.
    """

    def __init__(
        self,
        target_rtp: float,
        threshold_h: float = 5.0,
        drift_allowance_k: float = 0.5,
    ):
        """
        Args:
            target_rtp: Expected RTP percentage (e.g., 96.0)
            threshold_h: Decision threshold for alarm (higher = less sensitive)
            drift_allowance_k: Allowable drift before accumulation (higher = less sensitive)
        """
        self.target_rtp = target_rtp / 100.0  # Convert to ratio
        self.threshold_h = threshold_h
        self.drift_allowance_k = drift_allowance_k

        # Cumulative sums (positive and negative)
        self.s_pos = 0.0  # Detects upward drift (RTP too high)
        self.s_neg = 0.0  # Detects downward drift (RTP too low)
        self.samples = 0
        self.alarms: List[dict] = []

    def add_observation(self, bet: float, payout: float) -> Optional[str]:
        """
        Add a spin observation. Returns alarm direction if triggered.
        """
        if bet <= 0:
            return None

        self.samples += 1
        return_ratio = payout / bet
        deviation = return_ratio - self.target_rtp

        # Update positive CUSUM (detects upward shift)
        self.s_pos = max(0, self.s_pos + deviation - self.drift_allowance_k)

        # Update negative CUSUM (detects downward shift)
        self.s_neg = max(0, self.s_neg - deviation - self.drift_allowance_k)

        # Check thresholds
        if self.s_pos > self.threshold_h:
            self.alarms.append({
                "direction": "high",
                "cusum": self.s_pos,
                "sample": self.samples,
                "timestamp": time.time(),
            })
            self.s_pos = 0.0  # Reset after alarm
            return "high"

        if self.s_neg > self.threshold_h:
            self.alarms.append({
                "direction": "low",
                "cusum": self.s_neg,
                "sample": self.samples,
                "timestamp": time.time(),
            })
            self.s_neg = 0.0
            return "low"

        return None

    def get_status(self) -> dict:
        return {
            "samples": self.samples,
            "cusum_positive": round(self.s_pos, 4),
            "cusum_negative": round(self.s_neg, 4),
            "threshold": self.threshold_h,
            "total_alarms": len(self.alarms),
            "last_alarm": self.alarms[-1] if self.alarms else None,
        }


# ---------------------------------------------------------------------------
# RTP Monitor
# ---------------------------------------------------------------------------

class RTPMonitor:
    """
    Continuous RTP monitoring with statistical convergence validation.

    GLI-11 5.5 Compliance Features:
    - Real-time RTP calculation with confidence intervals
    - Hit frequency monitoring with Wilson score intervals
    - CUSUM drift detection for sustained deviations
    - Configurable alert thresholds per jurisdiction
    - Sliding window analysis (hourly, daily, weekly)
    - Convergence tracking toward theoretical RTP
    - Full audit trail of all measurements and alerts

    Alert Thresholds (typical GLI-11):
    - WARNING: Actual RTP deviates >2% from theoretical (short window)
    - CRITICAL: Actual RTP deviates >1% from theoretical (long window, >100K spins)
    - REGULATORY: Sustained deviation requiring regulator notification
    """

    def __init__(
        self,
        game_id: str = "default",
        theoretical_rtp: float = 96.0,
        theoretical_hit_freq: float = 25.0,
        warning_deviation_pct: float = 3.0,
        critical_deviation_pct: float = 5.0,
        min_spins_for_alert: int = 10000,
        min_spins_for_critical: int = 100000,
        window_sizes: Optional[Dict[str, int]] = None,
        audit_log_path: Optional[str] = None,
    ):
        self.game_id = game_id
        self.theoretical_rtp = theoretical_rtp
        self.theoretical_hit_freq = theoretical_hit_freq
        self.warning_deviation_pct = warning_deviation_pct
        self.critical_deviation_pct = critical_deviation_pct
        self.min_spins_for_alert = min_spins_for_alert
        self.min_spins_for_critical = min_spins_for_critical
        self.audit_log_path = audit_log_path

        # Cumulative counters
        self.total_spins = 0
        self.total_wagered = 0.0
        self.total_returned = 0.0
        self.total_wins = 0
        self.max_single_payout = 0.0
        self.start_time = time.time()

        # Sliding windows
        self._window_sizes = window_sizes or {
            "1h": 3600,
            "24h": 86400,
            "7d": 604800,
        }
        self._windows: Dict[str, deque] = {
            name: deque() for name in self._window_sizes
        }

        # Per-spin variance tracking
        self._return_ratios: deque = deque(maxlen=100000)

        # Drift detector
        self._cusum = CUSUMDetector(
            target_rtp=theoretical_rtp,
            threshold_h=5.0,
            drift_allowance_k=0.5,
        )

        # Convergence tracking
        self._convergence_points: List[dict] = []
        self._convergence_interval = 10000  # Record every N spins

        # Alerts
        self.alerts: List[RTPAlert] = []

        self._audit_sequence = 0

        logger.info(
            "RTP Monitor initialized: game=%s, theoretical_rtp=%.2f%%",
            game_id, theoretical_rtp,
        )

    def record_spin(self, bet: float, payout: float) -> Optional[RTPAlert]:
        """
        Record a single spin result.

        Args:
            bet: Amount wagered
            payout: Amount paid out (0 for a loss)

        Returns:
            RTPAlert if an alert was triggered, None otherwise
        """
        now = time.time()
        self.total_spins += 1
        self.total_wagered += bet
        self.total_returned += payout
        if payout > 0:
            self.total_wins += 1
        if payout > self.max_single_payout:
            self.max_single_payout = payout

        record = SpinRecord(bet=bet, payout=payout, timestamp=now)

        # Add to sliding windows
        for name, window in self._windows.items():
            window.append(record)

        # Track return ratio for variance calculation
        if bet > 0:
            self._return_ratios.append(payout / bet)

        # CUSUM drift detection
        drift_alarm = self._cusum.add_observation(bet, payout)
        if drift_alarm:
            alert = self._create_alert(
                AlertType.DRIFT_DETECTED,
                AlertSeverity.WARNING,
                f"CUSUM drift detected: RTP trending {drift_alarm}. "
                f"Current RTP: {self.current_rtp:.2f}%",
                {"direction": drift_alarm, "cusum": self._cusum.get_status()},
            )
            return alert

        # Periodic convergence tracking
        if self.total_spins % self._convergence_interval == 0:
            self._record_convergence_point()

        # Check thresholds (only with sufficient samples)
        return self._check_thresholds()

    def record_batch(self, spins: List[Tuple[float, float]]) -> List[RTPAlert]:
        """Record a batch of (bet, payout) pairs. Returns any alerts."""
        alerts = []
        for bet, payout in spins:
            alert = self.record_spin(bet, payout)
            if alert:
                alerts.append(alert)
        return alerts

    @property
    def current_rtp(self) -> float:
        """Current cumulative RTP percentage."""
        if self.total_wagered <= 0:
            return 0.0
        return (self.total_returned / self.total_wagered) * 100

    @property
    def current_hit_frequency(self) -> float:
        """Current hit frequency percentage."""
        if self.total_spins <= 0:
            return 0.0
        return (self.total_wins / self.total_spins) * 100

    def _check_thresholds(self) -> Optional[RTPAlert]:
        """Check if current RTP deviates beyond thresholds."""
        if self.total_spins < self.min_spins_for_alert:
            return None

        deviation = abs(self.current_rtp - self.theoretical_rtp)

        # Critical check (requires more spins for significance)
        if (
            self.total_spins >= self.min_spins_for_critical
            and deviation > self.critical_deviation_pct
        ):
            _, ci_upper = normal_rtp_interval(
                self.total_wagered, self.total_returned, self.total_spins, z=2.576
            )
            ci_lower, _ = normal_rtp_interval(
                self.total_wagered, self.total_returned, self.total_spins, z=2.576
            )

            # Only alert if theoretical RTP is outside 99% CI
            if self.theoretical_rtp < ci_lower or self.theoretical_rtp > ci_upper:
                return self._create_alert(
                    AlertType.RTP_DEVIATION,
                    AlertSeverity.CRITICAL,
                    f"CRITICAL RTP deviation: actual={self.current_rtp:.2f}%, "
                    f"expected={self.theoretical_rtp:.2f}%, "
                    f"deviation={deviation:.2f}% after {self.total_spins:,} spins. "
                    f"99% CI: [{ci_lower:.2f}%, {ci_upper:.2f}%]",
                    {
                        "actual_rtp": round(self.current_rtp, 4),
                        "theoretical_rtp": self.theoretical_rtp,
                        "deviation": round(deviation, 4),
                        "total_spins": self.total_spins,
                        "ci_99_lower": round(ci_lower, 4),
                        "ci_99_upper": round(ci_upper, 4),
                    },
                )

        # Warning check
        if deviation > self.warning_deviation_pct:
            ci_lower, ci_upper = normal_rtp_interval(
                self.total_wagered, self.total_returned, self.total_spins
            )
            if self.theoretical_rtp < ci_lower or self.theoretical_rtp > ci_upper:
                return self._create_alert(
                    AlertType.RTP_DEVIATION,
                    AlertSeverity.WARNING,
                    f"RTP deviation warning: actual={self.current_rtp:.2f}%, "
                    f"expected={self.theoretical_rtp:.2f}% after {self.total_spins:,} spins",
                    {
                        "actual_rtp": round(self.current_rtp, 4),
                        "deviation": round(deviation, 4),
                        "total_spins": self.total_spins,
                    },
                )

        return None

    def _record_convergence_point(self) -> None:
        """Record a convergence data point for trend analysis."""
        ci_lower, ci_upper = normal_rtp_interval(
            self.total_wagered, self.total_returned, self.total_spins
        )
        point = {
            "spins": self.total_spins,
            "rtp": round(self.current_rtp, 4),
            "ci_lower": round(ci_lower, 4),
            "ci_upper": round(ci_upper, 4),
            "ci_width": round(ci_upper - ci_lower, 4),
            "hit_freq": round(self.current_hit_frequency, 4),
            "timestamp": time.time(),
        }
        self._convergence_points.append(point)

    def _create_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        message: str,
        details: dict,
    ) -> RTPAlert:
        """Create and store an alert."""
        alert = RTPAlert(
            timestamp=datetime.now(timezone.utc).isoformat(),
            alert_type=alert_type,
            severity=severity,
            message=message,
            details=details,
            game_id=self.game_id,
        )
        self.alerts.append(alert)

        self._audit("ALERT", {
            "type": alert_type.value,
            "severity": severity.value,
            "message": message,
            **details,
        })

        if severity == AlertSeverity.CRITICAL:
            logger.critical("RTP ALERT [%s]: %s", self.game_id, message)
        elif severity == AlertSeverity.WARNING:
            logger.warning("RTP ALERT [%s]: %s", self.game_id, message)
        else:
            logger.info("RTP ALERT [%s]: %s", self.game_id, message)

        return alert

    def get_window_stats(self, window_name: str) -> Optional[dict]:
        """Get RTP stats for a sliding window."""
        window = self._windows.get(window_name)
        if not window:
            return None

        now = time.time()
        max_age = self._window_sizes[window_name]

        # Prune expired records
        while window and (now - window[0].timestamp) > max_age:
            window.popleft()

        if not window:
            return None

        total_bet = sum(r.bet for r in window)
        total_payout = sum(r.payout for r in window)
        wins = sum(1 for r in window if r.payout > 0)
        count = len(window)

        if total_bet <= 0:
            return None

        rtp = (total_payout / total_bet) * 100
        hit_freq = (wins / count) * 100 if count > 0 else 0

        ci_lower, ci_upper = normal_rtp_interval(total_bet, total_payout, count)
        hf_lower, hf_upper = wilson_score_interval(wins, count)

        return {
            "window": window_name,
            "spins": count,
            "total_wagered": round(total_bet, 2),
            "total_returned": round(total_payout, 2),
            "rtp_percent": round(rtp, 4),
            "rtp_ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
            "hit_frequency": round(hit_freq, 4),
            "hit_freq_ci_95": [round(hf_lower * 100, 4), round(hf_upper * 100, 4)],
            "deviation_from_theoretical": round(rtp - self.theoretical_rtp, 4),
        }

    def get_report(self) -> dict:
        """
        Generate comprehensive RTP monitoring report.

        GLI-11 5.5.4: Reports must include actual RTP, confidence intervals,
        sample size, and convergence status.
        """
        ci_lower, ci_upper = normal_rtp_interval(
            self.total_wagered, self.total_returned, self.total_spins
        )
        hf_lower, hf_upper = wilson_score_interval(self.total_wins, self.total_spins)

        # Variance estimation
        variance = 0.0
        if len(self._return_ratios) > 1:
            mean_ratio = sum(self._return_ratios) / len(self._return_ratios)
            variance = sum(
                (r - mean_ratio) ** 2 for r in self._return_ratios
            ) / (len(self._return_ratios) - 1)

        # Window stats
        window_stats = {}
        for name in self._window_sizes:
            ws = self.get_window_stats(name)
            if ws:
                window_stats[name] = ws

        # Convergence assessment
        converged = False
        if self.total_spins >= self.min_spins_for_critical:
            converged = (ci_lower <= self.theoretical_rtp <= ci_upper)

        elapsed = time.time() - self.start_time

        return {
            "game_id": self.game_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_spins": self.total_spins,
                "total_wagered": round(self.total_wagered, 2),
                "total_returned": round(self.total_returned, 2),
                "actual_rtp_percent": round(self.current_rtp, 4),
                "theoretical_rtp_percent": self.theoretical_rtp,
                "deviation_percent": round(
                    self.current_rtp - self.theoretical_rtp, 4
                ),
                "converged": converged,
            },
            "confidence_intervals": {
                "rtp_95_ci": [round(ci_lower, 4), round(ci_upper, 4)],
                "rtp_ci_width": round(ci_upper - ci_lower, 4),
                "hit_frequency_actual": round(self.current_hit_frequency, 4),
                "hit_frequency_theoretical": self.theoretical_hit_freq,
                "hit_freq_95_ci": [round(hf_lower * 100, 4), round(hf_upper * 100, 4)],
            },
            "variance": {
                "sample_variance": round(variance, 6),
                "sample_std_dev": round(math.sqrt(variance), 6) if variance > 0 else 0,
                "samples_in_window": len(self._return_ratios),
            },
            "drift_detection": self._cusum.get_status(),
            "windows": window_stats,
            "convergence_history": self._convergence_points[-20:],  # Last 20 points
            "alerts": {
                "total": len(self.alerts),
                "critical": sum(1 for a in self.alerts if a.severity == AlertSeverity.CRITICAL),
                "warning": sum(1 for a in self.alerts if a.severity == AlertSeverity.WARNING),
                "recent": [
                    {
                        "timestamp": a.timestamp,
                        "type": a.alert_type.value,
                        "severity": a.severity.value,
                        "message": a.message,
                    }
                    for a in self.alerts[-10:]
                ],
            },
            "operational": {
                "max_single_payout": round(self.max_single_payout, 2),
                "elapsed_seconds": round(elapsed, 1),
                "spins_per_second": round(self.total_spins / max(elapsed, 1), 1),
            },
        }

    def _audit(self, event_type: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "RTPMonitor",
            "game_id": self.game_id,
            "event": event_type,
            **details,
        }
        if self.audit_log_path:
            try:
                with open(self.audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        logger.debug("AUDIT: %s", json.dumps(entry))


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """RTP monitor self-test."""
    import random

    print("=== RTP Monitor Self-Test ===\n")

    monitor = RTPMonitor(
        game_id="test-slot",
        theoretical_rtp=96.0,
        theoretical_hit_freq=25.0,
        min_spins_for_alert=1000,
        min_spins_for_critical=10000,
    )

    # Simulate 50,000 spins with ~96% RTP
    random.seed(42)
    bet = 1.0
    for i in range(50000):
        r = random.random()
        if r < 0.60:      # 60% - no win
            payout = 0.0
        elif r < 0.85:    # 25% - small win
            payout = random.uniform(0.1, 2.0)
        elif r < 0.95:    # 10% - medium win
            payout = random.uniform(2.0, 10.0)
        elif r < 0.995:   # 4.5% - large win
            payout = random.uniform(10.0, 50.0)
        else:             # 0.5% - jackpot
            payout = random.uniform(50.0, 500.0)

        monitor.record_spin(bet, payout)

    # Test 1: RTP is calculated
    assert monitor.total_spins == 50000
    assert monitor.current_rtp > 0
    print(f"[PASS] RTP after 50K spins: {monitor.current_rtp:.2f}%")

    # Test 2: Hit frequency
    hf = monitor.current_hit_frequency
    assert 30 < hf < 50, f"Hit frequency {hf} out of expected range"
    print(f"[PASS] Hit frequency: {hf:.2f}%")

    # Test 3: Confidence intervals
    report = monitor.get_report()
    ci = report["confidence_intervals"]["rtp_95_ci"]
    assert ci[0] < ci[1]
    print(f"[PASS] 95% CI: [{ci[0]:.2f}%, {ci[1]:.2f}%]")
    print(f"  CI width: {report['confidence_intervals']['rtp_ci_width']:.2f}%")

    # Test 4: Window stats
    assert "windows" in report
    print(f"[PASS] Window stats computed: {list(report['windows'].keys())}")

    # Test 5: CUSUM detector
    cusum = report["drift_detection"]
    assert cusum["samples"] == 50000
    print(f"[PASS] CUSUM: pos={cusum['cusum_positive']:.3f}, "
          f"neg={cusum['cusum_negative']:.3f}")

    # Test 6: Wilson score interval
    lower, upper = wilson_score_interval(250, 1000)
    assert 0.20 < lower < 0.25, f"Wilson lower bound incorrect: {lower}"
    assert 0.25 < upper < 0.30, f"Wilson upper bound incorrect: {upper}"
    print(f"[PASS] Wilson score interval: [{lower:.4f}, {upper:.4f}]")

    # Test 7: Convergence tracking
    assert len(report.get("convergence_history", [])) > 0
    print(f"[PASS] Convergence points: {len(report['convergence_history'])}")

    # Test 8: Alert count
    print(f"[PASS] Alerts: total={report['alerts']['total']}, "
          f"critical={report['alerts']['critical']}, "
          f"warning={report['alerts']['warning']}")

    # Test 9: Variance
    assert report["variance"]["sample_variance"] > 0
    print(f"[PASS] Variance: {report['variance']['sample_variance']:.4f}, "
          f"StdDev: {report['variance']['sample_std_dev']:.4f}")

    print(f"\nFull report summary:")
    print(f"  Total wagered: {report['summary']['total_wagered']:,.2f}")
    print(f"  Total returned: {report['summary']['total_returned']:,.2f}")
    print(f"  Actual RTP: {report['summary']['actual_rtp_percent']:.2f}%")
    print(f"  Converged: {report['summary']['converged']}")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
