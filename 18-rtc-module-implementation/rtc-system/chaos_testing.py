# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
RTC Chaos Engineering Test Suite

This module provides chaos engineering tests for validating
RTC system resilience under adverse conditions.

Tests Include:
- Clock drift injection and detection
- Byzantine node simulation
- Network partition scenarios
- High-load stress testing
- Failover validation

These tests follow the principles of chaos engineering:
1. Start with a steady state hypothesis
2. Introduce real-world events (drift, failures, partitions)
3. Measure deviation from steady state
4. Minimize blast radius
5. Run in production (or production-like) environments

Usage:
    ```python
    tests = RTCChaosTests("http://rtc-service:8080")

    # Test drift detection
    await tests.test_clock_drift_injection(100.0)

    # Test Byzantine fault tolerance
    await tests.test_byzantine_node()

    # Test network partitions
    await tests.test_network_partition()

    # Run full chaos suite
    await tests.run_full_chaos_suite()
    ```
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

# External dependency (install with: pip install aiohttp)
try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore


class RTCChaosTests:
    """
    Chaos engineering tests for RTC system resilience.

    This class implements chaos engineering principles for testing
    Real-Time Clock systems in iGaming environments where time
    accuracy is critical for regulatory compliance.
    """

    def __init__(self, rtc_service_url: str):
        """
        Initialize chaos tests with RTC service URL.

        Args:
            rtc_service_url: Base URL of the RTC service
        """
        if aiohttp is None:
            raise ImportError("aiohttp package required: pip install aiohttp")

        self.rtc_service_url = rtc_service_url
        self.logger = logging.getLogger(__name__)
        self.results: List[Dict[str, Any]] = []

    async def test_clock_drift_injection(self, drift_ms: float) -> Dict[str, Any]:
        """
        Inject artificial clock drift and verify detection.

        This test validates that the RTC monitoring system
        can detect and alert on clock drift anomalies.

        Args:
            drift_ms: Amount of drift to inject in milliseconds

        Returns:
            Test result dictionary with success status and details
        """
        self.logger.info(f"Injecting {drift_ms}ms drift")
        details: Dict[str, Any] = {}
        result: Dict[str, Any] = {
            "test": "clock_drift_injection",
            "drift_ms": drift_ms,
            "start_time": datetime.now().isoformat(),
            "success": False,
            "details": details,
        }

        try:
            # Inject drift via debug endpoint
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.rtc_service_url}/debug/inject-drift",
                    json={"drift_ms": drift_ms},
                )

            # Wait for detection
            await asyncio.sleep(5)

            # Verify drift was detected
            async with aiohttp.ClientSession() as session:
                resp = await session.get(f"{self.rtc_service_url}/metrics")
                metrics = await resp.text()

                if f"rtc_drift_ms {drift_ms}" in metrics:
                    result["success"] = True
                    details["detected"] = True
                else:
                    details["detected"] = False
                    details["metrics"] = metrics[:500]

        except Exception as e:
            details["error"] = str(e)
            self.logger.error(f"Drift injection test failed: {e}")

        result["end_time"] = datetime.now().isoformat()
        self.results.append(result)
        return result

    async def test_byzantine_node(self) -> Dict[str, Any]:
        """
        Test Byzantine fault tolerance with malicious node.

        This test simulates a Byzantine (malicious or faulty) node
        returning incorrect time values and verifies that the
        consensus mechanism continues to function correctly.

        A Byzantine-tolerant system should:
        - Continue operating with reduced confidence
        - Not accept the malicious node's time
        - Maintain accuracy within acceptable bounds

        Returns:
            Test result dictionary
        """
        self.logger.info("Simulating Byzantine node")
        details: Dict[str, Any] = {}
        result: Dict[str, Any] = {
            "test": "byzantine_node",
            "start_time": datetime.now().isoformat(),
            "success": False,
            "details": details,
        }

        try:
            # Make one node return incorrect time
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.rtc_service_url}/debug/byzantine-mode",
                    json={"node_id": "rtc-2", "enabled": True},
                )

            # Verify consensus still works
            timestamps = []
            for _ in range(10):
                async with aiohttp.ClientSession() as session:
                    resp = await session.get(f"{self.rtc_service_url}/api/v1/timestamp")
                    ts = await resp.json()
                    timestamps.append(ts["confidence"])

            # Confidence should be reduced but not zero
            avg_confidence = sum(timestamps) / len(timestamps)
            details["avg_confidence"] = avg_confidence
            details["confidence_values"] = timestamps

            # Byzantine tolerance: 0.6 < confidence < 0.9
            if 0.6 < avg_confidence < 0.9:
                result["success"] = True
            else:
                details["reason"] = (
                    f"Confidence {avg_confidence} outside expected range (0.6-0.9)"
                )

        except Exception as e:
            details["error"] = str(e)
            self.logger.error(f"Byzantine node test failed: {e}")

        finally:
            # Disable Byzantine mode
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        f"{self.rtc_service_url}/debug/byzantine-mode",
                        json={"node_id": "rtc-2", "enabled": False},
                    )
            except Exception:
                pass

        result["end_time"] = datetime.now().isoformat()
        self.results.append(result)
        return result

    async def test_network_partition(self) -> Dict[str, Any]:
        """
        Test behavior during network partition.

        This test simulates a network partition where some RTC
        nodes cannot communicate with each other. The system
        should:
        - Continue operating on both sides of the partition
        - Reduce confidence during partition
        - Recover gracefully when partition heals

        Returns:
            Test result dictionary
        """
        self.logger.info("Creating network partition")
        details: Dict[str, Any] = {}
        result: Dict[str, Any] = {
            "test": "network_partition",
            "start_time": datetime.now().isoformat(),
            "success": False,
            "details": details,
        }

        try:
            # Simulate partition (30 second duration)
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.rtc_service_url}/debug/network-partition",
                    json={"partition": ["rtc-1", "rtc-2"], "duration": 30},
                )

            # Monitor behavior during partition
            results_during: List[Dict[str, Any]] = []
            for i in range(40):
                try:
                    async with aiohttp.ClientSession() as session:
                        resp = await session.get(
                            f"{self.rtc_service_url}/api/v1/timestamp",
                            timeout=aiohttp.ClientTimeout(total=1),
                        )
                        ts = await resp.json()
                        results_during.append(
                            {"success": True, "confidence": ts["confidence"]}
                        )
                except asyncio.TimeoutError:
                    results_during.append({"success": False, "error": "timeout"})
                except Exception as e:
                    results_during.append({"success": False, "error": str(e)})

                await asyncio.sleep(1)

            # Analyze results
            successful = [r for r in results_during if r.get("success")]
            failed = [r for r in results_during if not r.get("success")]

            details["total_requests"] = len(results_during)
            details["successful"] = len(successful)
            details["failed"] = len(failed)

            if successful:
                avg_confidence = sum(r["confidence"] for r in successful) / len(
                    successful
                )
                details["avg_confidence"] = avg_confidence

            # Success criteria: >70% availability during partition
            availability = len(successful) / len(results_during)
            details["availability"] = availability

            if availability > 0.7:
                result["success"] = True
            else:
                details["reason"] = (
                    f"Availability {availability:.1%} below 70% threshold"
                )

        except Exception as e:
            details["error"] = str(e)
            self.logger.error(f"Network partition test failed: {e}")

        result["end_time"] = datetime.now().isoformat()
        self.results.append(result)
        return result

    async def test_high_load(
        self, requests_per_second: int = 1000, duration_seconds: int = 60
    ) -> Dict[str, Any]:
        """
        Test RTC system under high load.

        Args:
            requests_per_second: Target RPS
            duration_seconds: Test duration

        Returns:
            Test result dictionary
        """
        self.logger.info(f"Starting high load test: {requests_per_second} RPS")
        details: Dict[str, Any] = {}
        result: Dict[str, Any] = {
            "test": "high_load",
            "rps_target": requests_per_second,
            "duration": duration_seconds,
            "start_time": datetime.now().isoformat(),
            "success": False,
            "details": details,
        }

        latencies: List[float] = []
        errors = 0

        try:
            start = datetime.now()
            while (datetime.now() - start).total_seconds() < duration_seconds:
                batch_start = datetime.now()
                tasks = []

                for _ in range(requests_per_second):
                    tasks.append(self._make_timestamp_request())

                responses = await asyncio.gather(*tasks, return_exceptions=True)

                for resp in responses:
                    if isinstance(resp, Exception):
                        errors += 1
                    elif isinstance(resp, float):
                        latencies.append(resp)
                    else:
                        errors += 1

                # Wait for next second
                elapsed = (datetime.now() - batch_start).total_seconds()
                if elapsed < 1.0:
                    await asyncio.sleep(1.0 - elapsed)

            # Calculate statistics
            if latencies:
                latencies.sort()
                details["p50_ms"] = latencies[len(latencies) // 2] * 1000
                details["p99_ms"] = latencies[int(len(latencies) * 0.99)] * 1000
                details["avg_ms"] = (sum(latencies) / len(latencies)) * 1000

            total_requests = len(latencies) + errors
            details["total_requests"] = total_requests
            details["successful"] = len(latencies)
            details["errors"] = errors
            error_rate = errors / total_requests if total_requests > 0 else 0.0
            details["error_rate"] = error_rate

            # Success: <1% error rate and p99 < 10ms
            p99_ms = details.get("p99_ms", 999.0)
            if error_rate < 0.01 and p99_ms < 10:
                result["success"] = True

        except Exception as e:
            details["error"] = str(e)
            self.logger.error(f"High load test failed: {e}")

        result["end_time"] = datetime.now().isoformat()
        self.results.append(result)
        return result

    async def _make_timestamp_request(self) -> float:
        """Make a single timestamp request and return latency."""
        start = datetime.now()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.rtc_service_url}/api/v1/timestamp",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                await resp.json()
        return (datetime.now() - start).total_seconds()

    async def run_full_chaos_suite(self) -> Dict[str, Any]:
        """
        Run complete chaos engineering test suite.

        Returns:
            Summary of all test results
        """
        self.logger.info("Starting full chaos engineering suite")
        self.results = []

        # Run all tests
        await self.test_clock_drift_injection(50.0)
        await self.test_clock_drift_injection(100.0)
        await self.test_byzantine_node()
        await self.test_network_partition()
        await self.test_high_load(500, 30)

        # Summarize
        passed = sum(1 for r in self.results if r["success"])
        failed = len(self.results) - passed

        return {
            "total_tests": len(self.results),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / len(self.results) if self.results else 0,
            "results": self.results,
        }


# Simple integration test helper
def test_bulk_timestamps(client: Any, count: int = 100) -> bool:
    """
    Test bulk timestamp generation.

    Args:
        client: RTC client instance
        count: Number of timestamps to request

    Returns:
        True if all timestamps are valid
    """
    responses = client.get_bulk_timestamps(count)

    if len(responses) != count:
        return False

    for resp in responses:
        if resp.signature is None or resp.timestamp is None:
            return False

    return True
