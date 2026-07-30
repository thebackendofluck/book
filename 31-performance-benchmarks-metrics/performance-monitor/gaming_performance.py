#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Gaming Performance Monitor Module
==================================

Gaming-specific performance monitoring for iGaming platforms.
Tracks game loading, real-time performance, betting systems, and live dealer metrics.
"""

from typing import Any, Dict, List, Optional


class GamingPerformanceMonitor:
    """Monitor gaming-specific performance metrics for iGaming platforms."""

    def __init__(self, gaming_config: Optional[Dict[str, Any]] = None):
        self.config = gaming_config or {}
        self.gaming_baselines = self._load_gaming_baselines()

    async def monitor_gaming_performance(self) -> Dict[str, Any]:
        """
        Monitor gaming-specific performance metrics.

        Returns:
            Dict containing game loading, real-time, and betting performance.
        """
        # Game loading performance
        game_loading = await self._monitor_game_loading()

        # Real-time game performance
        realtime_performance = await self._monitor_realtime_performance()

        # Betting system performance
        betting_performance = await self._monitor_betting_performance()

        # Live dealer performance
        live_dealer_performance = await self._monitor_live_dealer_performance()

        # Mobile gaming performance
        mobile_gaming = await self._monitor_mobile_gaming()

        # Cross-platform consistency
        cross_platform = await self._monitor_cross_platform_consistency()

        return {
            "game_loading": game_loading,
            "realtime_performance": realtime_performance,
            "betting_performance": betting_performance,
            "live_dealer_performance": live_dealer_performance,
            "mobile_gaming": mobile_gaming,
            "cross_platform_consistency": cross_platform,
            "gaming_performance_score": self._calculate_gaming_performance_score([
                game_loading, realtime_performance, betting_performance,
                live_dealer_performance, mobile_gaming, cross_platform
            ])
        }

    def _load_gaming_baselines(self) -> Dict[str, Any]:
        """Load gaming-specific performance baselines."""
        return {
            "game_loading": {
                "slot_games": {
                    "loading_time_target": 2000,  # milliseconds
                    "failure_rate_target": 0.01   # 0.01%
                },
                "table_games": {
                    "loading_time_target": 3000,
                    "failure_rate_target": 0.02
                },
                "live_dealer": {
                    "loading_time_target": 4000,
                    "failure_rate_target": 0.03
                }
            },
            "realtime_performance": {
                "bet_placement_latency": {
                    "target": 100,    # milliseconds
                    "maximum": 500
                },
                "game_state_updates": {
                    "target": 50,
                    "maximum": 200
                },
                "multiplayer_sync": {
                    "target": 30,
                    "maximum": 100
                }
            },
            "betting_system": {
                "bet_confirmation_time": {
                    "target": 200,
                    "maximum": 1000
                },
                "balance_update_time": {
                    "target": 100,
                    "maximum": 500
                },
                "bet_cancellation_time": {
                    "target": 150,
                    "maximum": 750
                }
            }
        }

    async def _monitor_game_loading(self) -> Dict[str, Any]:
        """Monitor game loading performance."""
        # Game loading times by category
        loading_times = {
            "slots": {
                "average_loading_time": 1840,
                "p95_loading_time": 3200,
                "failure_rate": 0.008,
                "abandonment_rate": 0.023
            },
            "blackjack": {
                "average_loading_time": 2650,
                "p95_loading_time": 4500,
                "failure_rate": 0.015,
                "abandonment_rate": 0.034
            },
            "roulette": {
                "average_loading_time": 2890,
                "p95_loading_time": 5200,
                "failure_rate": 0.012,
                "abandonment_rate": 0.028
            },
            "live_dealer": {
                "average_loading_time": 3450,
                "p95_loading_time": 6800,
                "failure_rate": 0.025,
                "abandonment_rate": 0.045
            }
        }

        # Loading performance by device
        device_performance = {
            "desktop": {
                "average_loading_time": 2100,
                "success_rate": 0.987
            },
            "mobile": {
                "average_loading_time": 3200,
                "success_rate": 0.965
            },
            "tablet": {
                "average_loading_time": 2800,
                "success_rate": 0.972
            }
        }

        # Geographic loading performance
        geographic_performance = {
            "north_america": {
                "average_loading_time": 2450,
                "success_rate": 0.978
            },
            "europe": {
                "average_loading_time": 2230,
                "success_rate": 0.982
            },
            "asia": {
                "average_loading_time": 3120,
                "success_rate": 0.956
            }
        }

        return {
            "loading_times_by_category": loading_times,
            "device_performance": device_performance,
            "geographic_performance": geographic_performance,
            "loading_performance_score": self._calculate_loading_performance_score(
                loading_times, device_performance, geographic_performance
            ),
            "optimization_opportunities": self._identify_loading_optimizations(
                loading_times, device_performance
            )
        }

    async def _monitor_realtime_performance(self) -> Dict[str, Any]:
        """Monitor real-time game performance."""
        return {
            "websocket_latency": {
                "p50": 12,
                "p95": 45,
                "p99": 89
            },
            "message_throughput": 15000,  # messages per second
            "connection_stability": 0.998,
            "reconnection_time_avg": 850,  # milliseconds
            "game_state_sync_lag": 23,     # milliseconds
            "frame_rate": {
                "target": 60,
                "actual_avg": 58,
                "drops_per_minute": 2
            }
        }

    async def _monitor_betting_performance(self) -> Dict[str, Any]:
        """Monitor betting system performance."""
        return {
            "bet_placement": {
                "avg_latency": 87,
                "p95_latency": 145,
                "success_rate": 0.9992,
                "throughput": 2500  # bets per second
            },
            "balance_updates": {
                "avg_latency": 45,
                "consistency_rate": 0.9999
            },
            "bet_cancellation": {
                "avg_latency": 123,
                "success_rate": 0.998
            },
            "payout_processing": {
                "avg_latency": 234,
                "accuracy_rate": 1.0
            }
        }

    async def _monitor_live_dealer_performance(self) -> Dict[str, Any]:
        """Monitor live dealer specific performance."""
        return {
            "video_stream": {
                "bitrate_avg": 3500,  # kbps
                "buffer_ratio": 0.02,
                "quality_score": 4.5,
                "latency": 450  # milliseconds
            },
            "dealer_interaction": {
                "command_latency": 89,
                "sync_accuracy": 0.997
            },
            "table_capacity": {
                "avg_players": 6.2,
                "peak_capacity": 7,
                "queue_time_avg": 45  # seconds
            },
            "uptime": 0.9995
        }

    async def _monitor_mobile_gaming(self) -> Dict[str, Any]:
        """Monitor mobile gaming specific performance."""
        return {
            "app_launch_time": 1200,  # milliseconds
            "game_switch_time": 890,  # milliseconds
            "touch_response": 45,     # milliseconds
            "battery_impact": "moderate",
            "data_usage_per_hour": 85,  # MB
            "offline_mode_coverage": 0.35,  # percentage of games
            "push_notification_delivery": 0.97
        }

    async def _monitor_cross_platform_consistency(self) -> Dict[str, Any]:
        """Monitor cross-platform consistency metrics."""
        return {
            "feature_parity": 0.95,
            "visual_consistency": 0.92,
            "performance_variance": 0.15,  # lower is better
            "session_continuity": 0.89,
            "platform_specific_issues": [
                {"platform": "iOS Safari", "issue": "Occasional audio delay", "severity": "low"},
                {"platform": "Android WebView", "issue": "Memory pressure on older devices", "severity": "medium"}
            ]
        }

    def _calculate_loading_performance_score(
        self,
        loading_times: Dict[str, Any],
        device_performance: Dict[str, Any],
        geographic_performance: Dict[str, Any]
    ) -> float:
        """Calculate loading performance score."""
        baselines = self.gaming_baselines["game_loading"]
        score = 0.0

        # Score based on loading times
        for game_type, metrics in loading_times.items():
            baseline = baselines.get(game_type.replace("s", "_games"), baselines.get("slot_games", {}))
            target = baseline.get("loading_time_target", 3000)

            if metrics.get("average_loading_time", 5000) <= target:
                score += 0.15
            elif metrics.get("average_loading_time", 5000) <= target * 1.5:
                score += 0.10

        # Bonus for low failure rates
        avg_failure = sum(
            m.get("failure_rate", 0.05) for m in loading_times.values()
        ) / len(loading_times)
        if avg_failure < 0.02:
            score += 0.15

        return min(score, 1.0)

    def _identify_loading_optimizations(
        self,
        loading_times: Dict[str, Any],
        device_performance: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """Identify game loading optimization opportunities."""
        optimizations = []

        for game_type, metrics in loading_times.items():
            if metrics.get("average_loading_time", 0) > 3000:
                optimizations.append({
                    "area": game_type,
                    "issue": "Slow loading time",
                    "recommendation": f"Implement progressive loading for {game_type}",
                    "estimated_improvement": "30-40%"
                })

            if metrics.get("abandonment_rate", 0) > 0.03:
                optimizations.append({
                    "area": game_type,
                    "issue": "High abandonment rate",
                    "recommendation": "Add loading progress indicator",
                    "estimated_improvement": "15-20%"
                })

        if device_performance.get("mobile", {}).get("average_loading_time", 0) > 3500:
            optimizations.append({
                "area": "mobile",
                "issue": "Mobile loading slower than desktop",
                "recommendation": "Implement mobile-optimized asset delivery",
                "estimated_improvement": "25-35%"
            })

        return optimizations

    def _calculate_gaming_performance_score(
        self,
        metrics_list: List[Dict[str, Any]]
    ) -> float:
        """Calculate overall gaming performance score."""
        scores = []

        for metrics in metrics_list:
            if isinstance(metrics, dict):
                if metrics.get("loading_performance_score"):
                    scores.append(metrics["loading_performance_score"])
                elif metrics.get("success_rate"):
                    scores.append(metrics["success_rate"])
                elif metrics.get("connection_stability"):
                    scores.append(metrics["connection_stability"])
                else:
                    scores.append(0.8)  # Default

        return sum(scores) / max(len(scores), 1)
