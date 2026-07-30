# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Performance and Load Testing

This module contains performance and load tests to ensure the system can handle
production-level traffic and maintain required performance characteristics.
"""

import asyncio
import aiohttp
import time
import statistics
import psutil
import os
from typing import List, Dict, Any, Tuple
import json
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger(__name__)


class PerformanceLoadTester:
    """Performance and load testing suite"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.services = {
            "data_ingestion": "http://localhost:8080",
            "feature_engineering": "http://localhost:8081",
            "model_serving": "http://localhost:8082",
            "alerting": "http://localhost:8083"
        }

        # Performance thresholds
        self.thresholds = {
            "response_time_p95": 1000,  # ms
            "response_time_p99": 2000,  # ms
            "throughput_min": 50,       # requests per second
            "error_rate_max": 0.05,     # 5%
            "cpu_usage_max": 85,        # %
            "memory_usage_max": 90      # %
        }

    async def run_performance_test(self, duration_seconds: int = 60,
                                 concurrent_users: int = 10) -> Dict[str, Any]:
        """Run comprehensive performance test"""

        logger.info(f"Starting performance test: {concurrent_users} users for {duration_seconds}s")

        # Start monitoring
        monitoring_task = asyncio.create_task(self._monitor_system_resources(duration_seconds))

        # Run load test
        load_results = await self._run_load_test(duration_seconds, concurrent_users)

        # Wait for monitoring to complete
        monitoring_results = await monitoring_task

        # Analyze results
        analysis = self._analyze_performance_results(load_results, monitoring_results)

        return {
            "test_config": {
                "duration_seconds": duration_seconds,
                "concurrent_users": concurrent_users
            },
            "load_results": load_results,
            "monitoring_results": monitoring_results,
            "analysis": analysis,
            "passed": analysis["overall_passed"]
        }

    async def _run_load_test(self, duration: int, concurrent_users: int) -> Dict[str, Any]:
        """Run load test with multiple concurrent users"""

        start_time = time.time()
        end_time = start_time + duration

        response_times: List[float] = []
        errors: List[str] = []
        request_count = 0

        # Create user tasks
        tasks = []
        for user_id in range(concurrent_users):
            task = asyncio.create_task(
                self._simulate_user_journey(user_id, end_time, response_times, errors)
            )
            tasks.append(task)

        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)

        total_time = time.time() - start_time
        throughput = len(response_times) / total_time if total_time > 0 else 0

        return {
            "total_requests": len(response_times),
            "total_errors": len(errors),
            "duration": total_time,
            "throughput_rps": throughput,
            "response_times": response_times,
            "error_rate": len(errors) / len(response_times) if response_times else 0,
            "response_time_stats": self._calculate_response_time_stats(response_times)
        }

    async def _simulate_user_journey(self, user_id: int, end_time: float,
                                   response_times: List[float], errors: List[str]):
        """Simulate a user journey through the system"""

        async with aiohttp.ClientSession() as session:
            while time.time() < end_time:
                try:
                    # Simulate transaction ingestion
                    transaction = self._generate_test_transaction(user_id)

                    start_time = time.time()
                    async with session.post(
                        f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                        json=transaction
                    ) as response:
                        response_time = (time.time() - start_time) * 1000  # Convert to ms
                        response_times.append(response_time)

                        if response.status != 200:
                            errors.append(f"HTTP {response.status}: {await response.text()}")

                    # Small delay between requests to simulate realistic user behavior
                    await asyncio.sleep(0.1)

                except Exception as e:
                    errors.append(str(e))
                    await asyncio.sleep(0.1)

    def _generate_test_transaction(self, user_id: int) -> Dict[str, Any]:
        """Generate a test transaction"""

        import random
        import uuid

        return {
            "transaction_id": str(uuid.uuid4()),
            "player_id": f"perf_test_player_{user_id}",
            "amount": round(random.uniform(10, 1000), 2),
            "currency": "USD",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payment_method": random.choice(["credit_card", "debit_card", "paypal"]),
            "game_type": random.choice(["slots", "blackjack", "roulette", "baccarat"]),
            "location": {
                "ip_address": f"192.168.1.{random.randint(1, 255)}",
                "country": random.choice(["US", "UK", "DE", "FR", "CA"]),
                "city": random.choice(["New York", "London", "Berlin", "Paris", "Toronto"])
            }
        }

    async def _monitor_system_resources(self, duration: int) -> Dict[str, Any]:
        """Monitor system resources during test"""

        cpu_usage = []
        memory_usage = []
        disk_io = []
        network_io = []

        start_time = time.time()

        while time.time() - start_time < duration:
            # CPU and memory
            cpu_usage.append(psutil.cpu_percent(interval=1))
            memory = psutil.virtual_memory()
            memory_usage.append(memory.percent)

            # Disk I/O
            disk = psutil.disk_io_counters()
            if disk:
                disk_io.append({
                    "read_mb": disk.read_bytes / 1024 / 1024,
                    "write_mb": disk.write_bytes / 1024 / 1024
                })

            # Network I/O
            net = psutil.net_io_counters()
            if net:
                network_io.append({
                    "bytes_sent": net.bytes_sent,
                    "bytes_recv": net.bytes_recv
                })

        return {
            "cpu_usage_percent": cpu_usage,
            "memory_usage_percent": memory_usage,
            "disk_io": disk_io,
            "network_io": network_io,
            "cpu_stats": self._calculate_stats(cpu_usage),
            "memory_stats": self._calculate_stats(memory_usage)
        }

    def _calculate_response_time_stats(self, response_times: List[float]) -> Dict[str, float]:
        """Calculate response time statistics"""

        if not response_times:
            return {}

        sorted_times = sorted(response_times)

        return {
            "min": min(response_times),
            "max": max(response_times),
            "mean": statistics.mean(response_times),
            "median": statistics.median(response_times),
            "p50": sorted_times[int(len(sorted_times) * 0.5)],
            "p95": sorted_times[int(len(sorted_times) * 0.95)],
            "p99": sorted_times[int(len(sorted_times) * 0.99)],
            "std_dev": statistics.stdev(response_times) if len(response_times) > 1 else 0
        }

    def _calculate_stats(self, values: List[float]) -> Dict[str, float]:
        """Calculate basic statistics"""

        if not values:
            return {}

        return {
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "std_dev": statistics.stdev(values) if len(values) > 1 else 0
        }

    def _analyze_performance_results(self, load_results: Dict[str, Any],
                                   monitoring_results: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze performance test results against thresholds"""

        analysis: Dict[str, Any] = {
            "overall_passed": True,
            "checks": {},
            "recommendations": []
        }

        # Check response time thresholds
        rt_stats = load_results.get("response_time_stats", {})
        p95_time = rt_stats.get("p95", 0)
        p99_time = rt_stats.get("p99", 0)

        analysis["checks"]["response_time_p95"] = {
            "value": p95_time,
            "threshold": self.thresholds["response_time_p95"],
            "passed": p95_time <= self.thresholds["response_time_p95"]
        }

        analysis["checks"]["response_time_p99"] = {
            "value": p99_time,
            "threshold": self.thresholds["response_time_p99"],
            "passed": p99_time <= self.thresholds["response_time_p99"]
        }

        # Check throughput
        throughput = load_results.get("throughput_rps", 0)
        analysis["checks"]["throughput"] = {
            "value": throughput,
            "threshold": self.thresholds["throughput_min"],
            "passed": throughput >= self.thresholds["throughput_min"]
        }

        # Check error rate
        error_rate = load_results.get("error_rate", 0)
        analysis["checks"]["error_rate"] = {
            "value": error_rate,
            "threshold": self.thresholds["error_rate_max"],
            "passed": error_rate <= self.thresholds["error_rate_max"]
        }

        # Check resource usage
        cpu_stats = monitoring_results.get("cpu_stats", {})
        memory_stats = monitoring_results.get("memory_stats", {})

        avg_cpu = cpu_stats.get("mean", 0)
        avg_memory = memory_stats.get("mean", 0)

        analysis["checks"]["cpu_usage"] = {
            "value": avg_cpu,
            "threshold": self.thresholds["cpu_usage_max"],
            "passed": avg_cpu <= self.thresholds["cpu_usage_max"]
        }

        analysis["checks"]["memory_usage"] = {
            "value": avg_memory,
            "threshold": self.thresholds["memory_usage_max"],
            "passed": avg_memory <= self.thresholds["memory_usage_max"]
        }

        # Determine overall result
        analysis["overall_passed"] = all(check["passed"] for check in analysis["checks"].values())

        # Generate recommendations
        if not analysis["checks"]["response_time_p95"]["passed"]:
            analysis["recommendations"].append(
                f"P95 response time ({p95_time:.1f}ms) exceeds threshold. Consider optimizing database queries or adding caching."
            )

        if not analysis["checks"]["throughput"]["passed"]:
            analysis["recommendations"].append(
                f"Throughput ({throughput:.1f} RPS) below minimum. Consider horizontal scaling or performance optimization."
            )

        if not analysis["checks"]["cpu_usage"]["passed"]:
            analysis["recommendations"].append(
                f"High CPU usage ({avg_cpu:.1f}%). Consider increasing instance size or optimizing CPU-intensive operations."
            )

        if not analysis["checks"]["memory_usage"]["passed"]:
            analysis["recommendations"].append(
                f"High memory usage ({avg_memory:.1f}%). Consider memory optimization or increasing instance memory."
            )

        return analysis

    async def run_scalability_test(self, user_levels: List[int] = [10, 50, 100, 200]) -> Dict[str, Any]:
        """Run scalability test with increasing user loads"""

        results = {}

        for user_count in user_levels:
            logger.info(f"Testing with {user_count} concurrent users")

            # Run test for 30 seconds
            test_result = await self.run_performance_test(
                duration_seconds=30,
                concurrent_users=user_count
            )

            results[f"{user_count}_users"] = {
                "throughput_rps": test_result["load_results"]["throughput_rps"],
                "response_time_p95": test_result["load_results"]["response_time_stats"].get("p95", 0),
                "error_rate": test_result["load_results"]["error_rate"],
                "passed": test_result["passed"]
            }

            # Small delay between tests
            await asyncio.sleep(5)

        # Analyze scalability
        scalability_analysis = self._analyze_scalability(results)

        return {
            "user_levels": user_levels,
            "results": results,
            "scalability_analysis": scalability_analysis
        }

    def _analyze_scalability(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze scalability characteristics"""

        user_counts = []
        throughputs = []
        response_times = []

        for key, result in results.items():
            user_count = int(key.split('_')[0])
            user_counts.append(user_count)
            throughputs.append(result["throughput_rps"])
            response_times.append(result["response_time_p95"])

        # Calculate scaling efficiency
        if len(user_counts) >= 2:
            throughput_scaling = throughputs[-1] / throughputs[0] if throughputs[0] > 0 else 0
            user_scaling = user_counts[-1] / user_counts[0]
            scaling_efficiency = throughput_scaling / user_scaling

            response_time_degradation = response_times[-1] / response_times[0] if response_times[0] > 0 else 0
        else:
            scaling_efficiency = 0
            response_time_degradation = 0

        return {
            "scaling_efficiency": scaling_efficiency,  # Should be close to 1.0 for linear scaling
            "response_time_degradation": response_time_degradation,
            "max_sustainable_users": self._estimate_max_users(results),
            "bottleneck_identified": self._identify_bottleneck(results)
        }

    def _estimate_max_users(self, results: Dict[str, Any]) -> int:
        """Estimate maximum sustainable user load"""

        # Find the highest user count that still passes performance thresholds
        for key in sorted(results.keys(), key=lambda x: int(x.split('_')[0]), reverse=True):
            if results[key]["passed"]:
                return int(key.split('_')[0])

        return 0  # No level passed

    def _identify_bottleneck(self, results: Dict[str, Any]) -> str:
        """Identify potential system bottleneck"""

        # Analyze degradation patterns
        user_counts = sorted([int(k.split('_')[0]) for k in results.keys()])
        response_times = [results[f"{uc}_users"]["response_time_p95"] for uc in user_counts]

        # Check for exponential response time growth (indicates bottleneck)
        if len(response_times) >= 3:
            growth_rate = response_times[-1] / response_times[-2] if response_times[-2] > 0 else 1
            if growth_rate > 2:
                return "Response time degradation suggests CPU or memory bottleneck"

        # Check throughput plateau
        throughputs = [results[f"{uc}_users"]["throughput_rps"] for uc in user_counts]
        if len(throughputs) >= 3:
            # Check if throughput is flattening
            recent_avg = sum(throughputs[-3:]) / 3
            earlier_avg = sum(throughputs[:3]) / 3 if len(throughputs) >= 3 else recent_avg

            if recent_avg < earlier_avg * 0.8:
                return "Throughput plateau suggests I/O or network bottleneck"

        return "No significant bottleneck identified"

    async def run_stress_test(self, peak_users: int = 200, duration: int = 120) -> Dict[str, Any]:
        """Run stress test to find system breaking points"""

        logger.info(f"Starting stress test with {peak_users} peak users for {duration}s")

        # Ramp up users gradually
        ramp_up_periods = 4
        users_per_period = peak_users // ramp_up_periods

        all_results = []

        for period in range(ramp_up_periods):
            current_users = (period + 1) * users_per_period

            logger.info(f"Stress test period {period + 1}: {current_users} users")

            # Run test for this period
            period_result = await self.run_performance_test(
                duration_seconds=duration // ramp_up_periods,
                concurrent_users=current_users
            )

            all_results.append({
                "period": period + 1,
                "users": current_users,
                "result": period_result
            })

            # Check if system is failing
            if not period_result["passed"]:
                logger.warning(f"System failing at {current_users} users")
                break

        # Analyze stress test results
        stress_analysis = self._analyze_stress_test(all_results)

        return {
            "peak_users_tested": peak_users,
            "period_results": all_results,
            "stress_analysis": stress_analysis
        }

    def _analyze_stress_test(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze stress test results"""

        breaking_point = None
        degradation_start = None

        for result in results:
            if not result["result"]["passed"]:
                breaking_point = result["users"]
                break

            # Check for significant degradation
            analysis = result["result"]["analysis"]
            if (analysis["checks"]["response_time_p95"]["value"] > self.thresholds["response_time_p95"] * 2 or
                analysis["checks"]["error_rate"]["value"] > self.thresholds["error_rate_max"] * 2):
                degradation_start = result["users"]

        return {
            "breaking_point_users": breaking_point,
            "degradation_start_users": degradation_start,
            "max_recommended_users": breaking_point or degradation_start or results[-1]["users"],
            "system_stability": "stable" if not breaking_point else "unstable"
        }


async def run_performance_tests():
    """Run comprehensive performance test suite"""

    tester = PerformanceLoadTester()

    logger.info("Starting comprehensive performance test suite")

    # Basic performance test
    basic_test = await tester.run_performance_test(duration_seconds=60, concurrent_users=20)
    logger.info(f"Basic performance test: {'PASSED' if basic_test['passed'] else 'FAILED'}")

    # Scalability test
    scalability_test = await tester.run_scalability_test([10, 25, 50, 100])
    logger.info(f"Scalability test completed for {len(scalability_test['user_levels'])} user levels")

    # Stress test
    stress_test = await tester.run_stress_test(peak_users=150, duration=180)
    logger.info(f"Stress test completed. Breaking point: {stress_test['stress_analysis']['breaking_point_users']}")

    # Generate comprehensive report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "basic_performance": basic_test,
        "scalability": scalability_test,
        "stress_test": stress_test,
        "summary": {
            "overall_status": "PASS" if (basic_test["passed"] and
                                       not stress_test["stress_analysis"]["breaking_point_users"]) else "FAIL",
            "recommendations": []
        }
    }

    # Add recommendations based on results
    if not basic_test["passed"]:
        report["summary"]["recommendations"].append("Basic performance test failed. Address performance issues before production deployment.")

    if stress_test["stress_analysis"]["breaking_point_users"]:
        report["summary"]["recommendations"].append(
            f"System breaking point identified at {stress_test['stress_analysis']['breaking_point_users']} users. Implement auto-scaling or capacity planning."
        )

    scaling_efficiency = scalability_test["scalability_analysis"]["scaling_efficiency"]
    if scaling_efficiency < 0.7:
        report["summary"]["recommendations"].append(
            f"Poor scaling efficiency ({scaling_efficiency:.2f}). Optimize for horizontal scaling."
        )

    # Save report
    with open("performance_test_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("Performance test suite completed. Report saved to performance_test_report.json")

    return report


if __name__ == "__main__":
    asyncio.run(run_performance_tests())