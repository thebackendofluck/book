# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Enterprise Load Testing System for iGaming Platforms

Provides comprehensive load testing capabilities for:
- Spike testing for peak event loads
- Stress testing for system limits
- Endurance testing for long-duration stability
- Volume testing for data throughput
- Scalability testing for auto-scaling validation

Features:
- Simulated gambler behavior profiles
- Geographic latency simulation
- Device mix distribution
- Real-time metrics collection
- Bottleneck identification
- Detailed reporting
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np


class LoadTestType(Enum):
    """Types of load tests supported."""

    SPIKE_TEST = "spike_test"
    STRESS_TEST = "stress_test"
    ENDURANCE_TEST = "endurance_test"
    VOLUME_TEST = "volume_test"
    SCALABILITY_TEST = "scalability_test"


class LoadTestPhase(Enum):
    """Phases of a load test execution."""

    WARM_UP = "warm_up"
    RAMP_UP = "ramp_up"
    STEADY_STATE = "steady_state"
    RAMP_DOWN = "ramp_down"
    COOL_DOWN = "cool_down"


@dataclass
class LoadTestConfig:
    """Configuration for a load test."""

    test_id: str
    test_type: LoadTestType
    target_concurrent_users: int
    ramp_up_duration: int  # seconds
    steady_state_duration: int  # seconds
    ramp_down_duration: int  # seconds
    target_endpoints: List[str]
    user_behavior_profile: str
    geographic_distribution: Dict[str, float]
    device_mix: Dict[str, float]
    bet_size_distribution: Dict[str, float]
    game_type_weights: Dict[str, float]


@dataclass
class LoadTestMetrics:
    """Metrics collected during load test."""

    timestamp: datetime
    concurrent_users: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    average_response_time: float
    p95_response_time: float
    p99_response_time: float
    requests_per_second: float
    errors_per_second: float
    cpu_utilization: float
    memory_utilization: float
    database_connections: int
    redis_connections: int
    network_io_mb_s: float


class LoadTestingSystem:
    """
    Enterprise load testing system for gambling platforms.

    Supports:
    - Million+ concurrent user simulation
    - Realistic gambler behavior patterns
    - Multi-region geographic distribution
    - Comprehensive bottleneck analysis

    Example:
        >>> config = LoadTestConfig(
        ...     test_id="load_test_001",
        ...     test_type=LoadTestType.STRESS_TEST,
        ...     target_concurrent_users=100000,
        ...     ramp_up_duration=300,
        ...     steady_state_duration=1800,
        ...     ramp_down_duration=300,
        ...     target_endpoints=["/api/bets", "/api/balance"],
        ...     user_behavior_profile="aggressive",
        ...     geographic_distribution={"US": 0.6, "UK": 0.3, "EU": 0.1},
        ...     device_mix={"mobile": 0.7, "desktop": 0.25, "tablet": 0.05},
        ...     bet_size_distribution={"small": 0.6, "medium": 0.3, "large": 0.1},
        ...     game_type_weights={"slots": 0.5, "blackjack": 0.3, "roulette": 0.2}
        ... )
        >>> result = await system.execute_load_test(config)
    """

    def __init__(self, redis_client: Any, db_pool: Any) -> None:
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)
        self.active_tests: Dict[str, Any] = {}
        self.config: Dict[str, Any] = {"api_base_url": "http://localhost:8080"}

    async def execute_load_test(self, config: LoadTestConfig) -> Dict[str, Any]:
        """Execute comprehensive load test."""
        try:
            self.logger.info(
                f"Starting load test {config.test_id} with "
                f"{config.target_concurrent_users} users"
            )

            # Initialize test
            await self._initialize_load_test(config)

            # Execute test phases
            test_results: Dict[str, Any] = {
                "test_id": config.test_id,
                "config": config,
                "phases": {},
                "overall_metrics": {},
                "bottlenecks": [],
                "recommendations": [],
            }

            # Execute phases
            test_results["phases"]["warm_up"] = await self._execute_warm_up_phase(
                config
            )
            test_results["phases"]["ramp_up"] = await self._execute_ramp_up_phase(
                config
            )
            test_results["phases"]["steady_state"] = (
                await self._execute_steady_state_phase(config)
            )
            test_results["phases"]["ramp_down"] = await self._execute_ramp_down_phase(
                config
            )
            test_results["phases"]["cool_down"] = await self._execute_cool_down_phase(
                config
            )

            # Analyze results
            test_results["overall_metrics"] = await self._analyze_test_results(
                test_results
            )
            test_results["bottlenecks"] = await self._identify_bottlenecks(test_results)
            test_results["recommendations"] = await self._generate_recommendations(
                test_results
            )

            # Generate report
            await self._generate_load_test_report(test_results)

            return test_results

        except Exception as e:
            self.logger.error(f"Load test {config.test_id} failed: {e}")
            return {"test_id": config.test_id, "status": "failed", "error": str(e)}
        finally:
            await self._cleanup_load_test(config.test_id)

    async def _execute_steady_state_phase(
        self, config: LoadTestConfig
    ) -> Dict[str, Any]:
        """Execute steady state phase with constant load."""
        self.logger.info(f"Starting steady state phase for test {config.test_id}")

        phase_duration = config.steady_state_duration

        # Create user simulation tasks
        user_tasks: List[asyncio.Task[Dict[str, Any]]] = []
        for i in range(config.target_concurrent_users):
            user_task = asyncio.create_task(
                self._simulate_gambler_user(
                    user_id=f"user_{i}_{config.test_id}",
                    test_config=config,
                    duration=phase_duration,
                )
            )
            user_tasks.append(user_task)

        # Monitor system metrics during steady state
        monitoring_task = asyncio.create_task(
            self._monitor_system_metrics(config.test_id, phase_duration)
        )

        # Collect metrics
        metrics_task = asyncio.create_task(
            self._collect_metrics_continuously(config.test_id, phase_duration)
        )

        # Wait for all tasks
        user_results = await asyncio.gather(*user_tasks, return_exceptions=True)
        monitoring_results = await monitoring_task
        metrics_data = await metrics_task

        # Process results
        successful_users = sum(
            1 for result in user_results if not isinstance(result, Exception)
        )
        total_bets = 0
        for result in user_results:
            if not isinstance(result, Exception) and isinstance(result, dict):
                total_bets += result.get("bets_placed", 0)

        error_rate = (
            (config.target_concurrent_users - successful_users)
            / config.target_concurrent_users
            if config.target_concurrent_users > 0
            else 0
        )

        return {
            "phase": "steady_state",
            "duration": phase_duration,
            "target_users": config.target_concurrent_users,
            "successful_users": successful_users,
            "total_bets_placed": total_bets,
            "error_rate": error_rate,
            "metrics": metrics_data,
            "system_health": monitoring_results,
        }

    async def _simulate_gambler_user(
        self, user_id: str, test_config: LoadTestConfig, duration: int
    ) -> Dict[str, Any]:
        """Simulate realistic gambler behavior during load test."""
        user_metrics: Dict[str, Any] = {
            "user_id": user_id,
            "session_duration": duration,
            "bets_placed": 0,
            "total_wagered": 0.0,
            "total_won": 0.0,
            "api_calls": [],
            "errors": [],
        }

        try:
            session_start = time.time()
            session_end = session_start + duration

            # User behavior parameters
            bet_frequency = self._get_user_bet_frequency(
                test_config.user_behavior_profile
            )
            game_preference = self._select_user_game_preference(
                test_config.game_type_weights
            )
            bet_size = self._select_user_bet_size(test_config.bet_size_distribution)

            while time.time() < session_end:
                try:
                    bet_result = await self._place_load_test_bet(
                        user_id=user_id,
                        game_type=game_preference,
                        bet_amount=bet_size,
                        test_config=test_config,
                    )

                    user_metrics["bets_placed"] += 1
                    user_metrics["total_wagered"] += bet_size
                    user_metrics["total_won"] += bet_result.get("win_amount", 0)
                    user_metrics["api_calls"].append(bet_result)

                except Exception as e:
                    user_metrics["errors"].append(
                        {
                            "timestamp": time.time(),
                            "error": str(e),
                            "context": "bet_placement",
                        }
                    )

                await asyncio.sleep(bet_frequency)

            return user_metrics

        except Exception as e:
            self.logger.error(f"User simulation failed for {user_id}: {e}")
            return user_metrics

    async def _monitor_system_metrics(
        self, test_id: str, duration: int
    ) -> Dict[str, Any]:
        """Monitor system health during load test."""
        monitoring_data: Dict[str, List[Any]] = {
            "cpu_utilization": [],
            "memory_utilization": [],
            "database_metrics": [],
            "redis_metrics": [],
            "network_metrics": [],
            "alerts": [],
        }

        start_time = time.time()
        check_interval = 10

        while time.time() - start_time < duration:
            try:
                metrics = await self._collect_system_metrics()

                monitoring_data["cpu_utilization"].append(metrics["cpu_percent"])
                monitoring_data["memory_utilization"].append(metrics["memory_percent"])

                # Check for alerts
                if metrics["cpu_percent"] > 80:
                    monitoring_data["alerts"].append(
                        {
                            "timestamp": time.time(),
                            "type": "high_cpu",
                            "value": metrics["cpu_percent"],
                        }
                    )

                if metrics["memory_percent"] > 85:
                    monitoring_data["alerts"].append(
                        {
                            "timestamp": time.time(),
                            "type": "high_memory",
                            "value": metrics["memory_percent"],
                        }
                    )

            except Exception as e:
                self.logger.error(f"Error collecting system metrics: {e}")

            await asyncio.sleep(check_interval)

        # Calculate summary
        cpu_list = monitoring_data["cpu_utilization"]
        mem_list = monitoring_data["memory_utilization"]

        return {
            "avg_cpu": float(np.mean(cpu_list)) if cpu_list else 0,
            "max_cpu": float(max(cpu_list)) if cpu_list else 0,
            "avg_memory": float(np.mean(mem_list)) if mem_list else 0,
            "max_memory": float(max(mem_list)) if mem_list else 0,
            "alerts_triggered": len(monitoring_data["alerts"]),
            "system_health_score": self._calculate_system_health_score(monitoring_data),
        }

    def _calculate_system_health_score(
        self, monitoring_data: Dict[str, List[Any]]
    ) -> float:
        """Calculate overall system health score during load test."""
        health_score = 100.0

        cpu_list = monitoring_data.get("cpu_utilization", [])
        if cpu_list:
            max_cpu = max(cpu_list)
            if max_cpu > 90:
                health_score -= 30
            elif max_cpu > 80:
                health_score -= 20
            elif max_cpu > 70:
                health_score -= 10

        mem_list = monitoring_data.get("memory_utilization", [])
        if mem_list:
            max_memory = max(mem_list)
            if max_memory > 90:
                health_score -= 25
            elif max_memory > 80:
                health_score -= 15
            elif max_memory > 70:
                health_score -= 10

        alert_count = len(monitoring_data.get("alerts", []))
        if alert_count > 10:
            health_score -= 20
        elif alert_count > 5:
            health_score -= 10
        elif alert_count > 2:
            health_score -= 5

        return max(0, health_score)

    async def _identify_bottlenecks(
        self, test_results: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Identify system bottlenecks from load test results."""
        bottlenecks: List[Dict[str, Any]] = []

        steady_state = test_results.get("phases", {}).get("steady_state", {})
        if not steady_state:
            return bottlenecks

        metrics = steady_state.get("metrics", [])
        if not metrics:
            return bottlenecks

        # CPU bottleneck analysis
        cpu_utilizations = [m.get("cpu_utilization", 0) for m in metrics]
        if cpu_utilizations:
            max_cpu = max(cpu_utilizations)
            avg_cpu = float(np.mean(cpu_utilizations))

            if max_cpu > 90:
                bottlenecks.append(
                    {
                        "type": "cpu",
                        "severity": "high",
                        "description": f"CPU utilization reached {max_cpu:.1f}%",
                        "recommendation": "Scale up CPU resources or optimize code",
                    }
                )
            elif avg_cpu > 80:
                bottlenecks.append(
                    {
                        "type": "cpu",
                        "severity": "medium",
                        "description": f"Average CPU utilization {avg_cpu:.1f}% is high",
                        "recommendation": "Monitor CPU usage and consider optimization",
                    }
                )

        # Memory bottleneck analysis
        memory_utilizations = [m.get("memory_utilization", 0) for m in metrics]
        if memory_utilizations:
            max_memory = max(memory_utilizations)
            avg_memory = float(np.mean(memory_utilizations))

            if max_memory > 90:
                bottlenecks.append(
                    {
                        "type": "memory",
                        "severity": "high",
                        "description": f"Memory utilization reached {max_memory:.1f}%",
                        "recommendation": "Scale up memory or investigate leaks",
                    }
                )
            elif avg_memory > 80:
                bottlenecks.append(
                    {
                        "type": "memory",
                        "severity": "medium",
                        "description": f"Average memory {avg_memory:.1f}% is high",
                        "recommendation": "Monitor memory usage and optimize",
                    }
                )

        # Response time bottleneck
        response_times = [m.get("average_response_time", 0) for m in metrics]
        if response_times:
            max_rt = max(response_times)
            avg_rt = float(np.mean(response_times))

            if max_rt > 5000:
                bottlenecks.append(
                    {
                        "type": "response_time",
                        "severity": "high",
                        "description": f"Max response time {max_rt:.0f}ms unacceptable",
                        "recommendation": "Investigate and optimize slow endpoints",
                    }
                )
            elif avg_rt > 1000:
                bottlenecks.append(
                    {
                        "type": "response_time",
                        "severity": "medium",
                        "description": f"Average response time {avg_rt:.0f}ms is slow",
                        "recommendation": "Optimize application performance",
                    }
                )

        return bottlenecks

    async def generate_load_test_report(
        self, test_results: Dict[str, Any]
    ) -> str:
        """Generate comprehensive load test report."""
        config = test_results.get("config")
        overall_metrics = test_results.get("overall_metrics", {})
        bottlenecks = test_results.get("bottlenecks", [])
        recommendations = test_results.get("recommendations", [])

        report = f"""# Load Test Report

## Executive Summary
- **Test ID**: {config.test_id if config else 'N/A'}
- **Test Type**: {config.test_type.value if config else 'N/A'}
- **Target Users**: {config.target_concurrent_users if config else 'N/A':,}
- **Test Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}

## Key Metrics
- **System Health Score**: {overall_metrics.get('system_health_score', 0):.1f}/100
- **Peak Concurrent Users**: {overall_metrics.get('peak_concurrent_users', 0):,}
- **Total Bets Processed**: {overall_metrics.get('total_bets', 0):,}
- **Average Response Time**: {overall_metrics.get('avg_response_time', 0):.0f}ms
- **Success Rate**: {overall_metrics.get('success_rate', 0):.1f}%

## Bottlenecks Identified
{self._format_bottlenecks(bottlenecks)}

## Recommendations
{self._format_recommendations(recommendations)}
"""
        return report

    def _format_bottlenecks(self, bottlenecks: List[Dict[str, Any]]) -> str:
        """Format bottlenecks for report."""
        if not bottlenecks:
            return "No significant bottlenecks identified."

        lines: List[str] = []
        for b in bottlenecks:
            lines.append(
                f"- **{b['type'].upper()}** ({b['severity']}): "
                f"{b['description']}"
            )
        return "\n".join(lines)

    def _format_recommendations(self, recommendations: List[str]) -> str:
        """Format recommendations for report."""
        if not recommendations:
            return "No specific recommendations at this time."
        return "\n".join(f"- {r}" for r in recommendations)

    # Helper methods
    def _get_user_bet_frequency(self, profile: str) -> float:
        """Get bet frequency based on user profile."""
        frequencies = {"aggressive": 2.0, "moderate": 5.0, "casual": 15.0}
        return frequencies.get(profile, 10.0)

    def _select_user_game_preference(self, weights: Dict[str, float]) -> str:
        """Select game type based on weights."""
        if not weights:
            return "slots"
        games = list(weights.keys())
        probs = list(weights.values())
        return str(np.random.choice(games, p=probs))

    def _select_user_bet_size(self, distribution: Dict[str, float]) -> float:
        """Select bet size based on distribution."""
        sizes = {"small": 10.0, "medium": 50.0, "large": 200.0}
        if not distribution:
            return 25.0
        categories = list(distribution.keys())
        probs = list(distribution.values())
        category = str(np.random.choice(categories, p=probs))
        return sizes.get(category, 25.0)

    # Placeholder implementations
    async def _initialize_load_test(self, config: LoadTestConfig) -> None:
        """Initialize load test resources."""
        self.active_tests[config.test_id] = {"status": "running", "config": config}

    async def _execute_warm_up_phase(
        self, config: LoadTestConfig
    ) -> Dict[str, Any]:
        """Execute warm-up phase."""
        return {"phase": "warm_up", "status": "completed"}

    async def _execute_ramp_up_phase(
        self, config: LoadTestConfig
    ) -> Dict[str, Any]:
        """Execute ramp-up phase."""
        return {"phase": "ramp_up", "status": "completed"}

    async def _execute_ramp_down_phase(
        self, config: LoadTestConfig
    ) -> Dict[str, Any]:
        """Execute ramp-down phase."""
        return {"phase": "ramp_down", "status": "completed"}

    async def _execute_cool_down_phase(
        self, config: LoadTestConfig
    ) -> Dict[str, Any]:
        """Execute cool-down phase."""
        return {"phase": "cool_down", "status": "completed"}

    async def _analyze_test_results(
        self, test_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze test results."""
        return {"system_health_score": 85.0, "success_rate": 99.5}

    async def _generate_recommendations(
        self, test_results: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations."""
        return ["Continue monitoring", "Consider scaling"]

    async def _generate_load_test_report(
        self, test_results: Dict[str, Any]
    ) -> None:
        """Generate and store load test report."""
        pass

    async def _cleanup_load_test(self, test_id: str) -> None:
        """Cleanup load test resources."""
        if test_id in self.active_tests:
            del self.active_tests[test_id]

    async def _place_load_test_bet(
        self,
        user_id: str,
        game_type: str,
        bet_amount: float,
        test_config: LoadTestConfig,
    ) -> Dict[str, Any]:
        """Place a bet during load test."""
        return {
            "bet_id": f"bet_{user_id}_{int(time.time())}",
            "status": "success",
            "win_amount": 0,
        }

    async def _collect_metrics_continuously(
        self, test_id: str, duration: int
    ) -> List[Dict[str, Any]]:
        """Collect metrics continuously."""
        return []

    async def _collect_system_metrics(self) -> Dict[str, Any]:
        """Collect current system metrics."""
        return {"cpu_percent": 50.0, "memory_percent": 60.0}
