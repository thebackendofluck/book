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
Performance Monitoring and Profiling

This module provides comprehensive performance monitoring, profiling,
and optimization recommendations for the fraud detection system.
"""

import asyncio
import time
import psutil
import threading
from typing import Dict, Any, List, Optional
from collections import deque
import statistics
import json
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger(__name__)


class PerformanceMonitor:
    """Real-time performance monitoring and profiling"""

    def __init__(self, window_size: int = 100, monitoring_interval: int = 5):
        self.window_size = window_size
        self.monitoring_interval = monitoring_interval

        # Metrics storage
        self.metrics = {
            "cpu_percent": deque(maxlen=window_size),
            "memory_percent": deque(maxlen=window_size),
            "memory_mb": deque(maxlen=window_size),
            "disk_io_read_mb": deque(maxlen=window_size),
            "disk_io_write_mb": deque(maxlen=window_size),
            "network_bytes_sent": deque(maxlen=window_size),
            "network_bytes_recv": deque(maxlen=window_size),
            "response_times_ms": deque(maxlen=window_size),
            "throughput_rps": deque(maxlen=window_size),
            "active_connections": deque(maxlen=window_size),
            "error_rate_percent": deque(maxlen=window_size)
        }

        # Monitoring state
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.process = psutil.Process()

        # Baseline metrics for anomaly detection
        self.baseline_metrics = {}
        self.anomaly_thresholds = {
            "cpu_percent": 85.0,
            "memory_percent": 90.0,
            "response_times_ms": 1000.0,  # P95 threshold
            "error_rate_percent": 5.0
        }

    def start_monitoring(self):
        """Start performance monitoring"""
        if self.monitoring:
            return

        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Performance monitoring started")

    def stop_monitoring(self):
        """Stop performance monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        logger.info("Performance monitoring stopped")

    def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.monitoring:
            try:
                self._collect_system_metrics()
                time.sleep(self.monitoring_interval)
            except Exception as e:
                logger.error("Error in monitoring loop", error=str(e))
                time.sleep(self.monitoring_interval * 2)

    def _collect_system_metrics(self):
        """Collect system performance metrics"""
        try:
            # CPU metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            self.metrics["cpu_percent"].append(cpu_percent)

            # Memory metrics
            memory = psutil.virtual_memory()
            self.metrics["memory_percent"].append(memory.percent)
            self.metrics["memory_mb"].append(memory.used / 1024 / 1024)

            # Disk I/O metrics
            disk_io = psutil.disk_io_counters()
            if disk_io:
                self.metrics["disk_io_read_mb"].append(disk_io.read_bytes / 1024 / 1024)
                self.metrics["disk_io_write_mb"].append(disk_io.write_bytes / 1024 / 1024)

            # Network I/O metrics
            net_io = psutil.net_io_counters()
            if net_io:
                self.metrics["network_bytes_sent"].append(net_io.bytes_sent)
                self.metrics["network_bytes_recv"].append(net_io.bytes_recv)

        except Exception as e:
            logger.error("Error collecting system metrics", error=str(e))

    def record_api_metrics(self, response_time_ms: float, success: bool = True):
        """Record API performance metrics"""
        self.metrics["response_times_ms"].append(response_time_ms)

        # Calculate error rate (rolling window)
        if not success:
            # Simple error rate calculation - in practice you'd want more sophisticated tracking
            recent_responses = list(self.metrics["response_times_ms"])[-10:]  # Last 10 responses
            error_count = sum(1 for _ in recent_responses if not success)  # This is simplified
            error_rate = (error_count / len(recent_responses)) * 100 if recent_responses else 0
            self.metrics["error_rate_percent"].append(error_rate)

    def record_throughput(self, requests_per_second: float):
        """Record system throughput"""
        self.metrics["throughput_rps"].append(requests_per_second)

    def record_connections(self, active_connections: int):
        """Record active connections"""
        self.metrics["active_connections"].append(active_connections)

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics"""

        stats = {}

        for metric_name, values in self.metrics.items():
            if not values:
                stats[metric_name] = {
                    "current": 0,
                    "average": 0,
                    "min": 0,
                    "max": 0,
                    "std_dev": 0,
                    "count": 0
                }
                continue

            values_list = list(values)
            stats[metric_name] = {
                "current": values_list[-1],
                "average": statistics.mean(values_list),
                "min": min(values_list),
                "max": max(values_list),
                "std_dev": statistics.stdev(values_list) if len(values_list) > 1 else 0,
                "count": len(values_list)
            }

        # Calculate percentiles for response times
        if self.metrics["response_times_ms"]:
            response_times = sorted(list(self.metrics["response_times_ms"]))
            n = len(response_times)

            stats["response_times_ms"].update({
                "p50": response_times[int(n * 0.5)],
                "p95": response_times[int(n * 0.95)],
                "p99": response_times[int(n * 0.99)]
            })

        return stats

    def detect_anomalies(self) -> List[Dict[str, Any]]:
        """Detect performance anomalies"""

        anomalies = []
        current_stats = self.get_performance_stats()

        # CPU anomaly
        if current_stats["cpu_percent"]["current"] > self.anomaly_thresholds["cpu_percent"]:
            anomalies.append({
                "type": "cpu_usage",
                "severity": "high" if current_stats["cpu_percent"]["current"] > 95 else "medium",
                "message": f"High CPU usage: {current_stats['cpu_percent']['current']:.1f}%",
                "current_value": current_stats["cpu_percent"]["current"],
                "threshold": self.anomaly_thresholds["cpu_percent"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Memory anomaly
        if current_stats["memory_percent"]["current"] > self.anomaly_thresholds["memory_percent"]:
            anomalies.append({
                "type": "memory_usage",
                "severity": "high" if current_stats["memory_percent"]["current"] > 95 else "medium",
                "message": f"High memory usage: {current_stats['memory_percent']['current']:.1f}%",
                "current_value": current_stats["memory_percent"]["current"],
                "threshold": self.anomaly_thresholds["memory_percent"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Response time anomaly
        if "p95" in current_stats["response_times_ms"]:
            p95_response_time = current_stats["response_times_ms"]["p95"]
            if p95_response_time > self.anomaly_thresholds["response_times_ms"]:
                anomalies.append({
                    "type": "response_time",
                    "severity": "high" if p95_response_time > 2000 else "medium",
                    "message": f"High response time P95: {p95_response_time:.1f}ms",
                    "current_value": p95_response_time,
                    "threshold": self.anomaly_thresholds["response_times_ms"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

        # Error rate anomaly
        if current_stats["error_rate_percent"]["current"] > self.anomaly_thresholds["error_rate_percent"]:
            anomalies.append({
                "type": "error_rate",
                "severity": "high" if current_stats["error_rate_percent"]["current"] > 10 else "medium",
                "message": f"High error rate: {current_stats['error_rate_percent']['current']:.1f}%",
                "current_value": current_stats["error_rate_percent"]["current"],
                "threshold": self.anomaly_thresholds["error_rate_percent"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        return anomalies

    def get_optimization_recommendations(self) -> List[Dict[str, Any]]:
        """Generate optimization recommendations based on performance data"""

        recommendations = []
        stats = self.get_performance_stats()

        # CPU optimization recommendations
        cpu_usage = stats["cpu_percent"]["average"]
        if cpu_usage > 80:
            recommendations.append({
                "category": "cpu",
                "priority": "high",
                "title": "High CPU Usage Detected",
                "description": f"Average CPU usage is {cpu_usage:.1f}%. Consider scaling or optimization.",
                "actions": [
                    "Increase number of worker processes",
                    "Implement connection pooling",
                    "Optimize database queries",
                    "Consider horizontal scaling"
                ]
            })

        # Memory optimization recommendations
        memory_usage = stats["memory_percent"]["average"]
        if memory_usage > 85:
            recommendations.append({
                "category": "memory",
                "priority": "high",
                "title": "High Memory Usage Detected",
                "description": f"Average memory usage is {memory_usage:.1f}%. Memory optimization needed.",
                "actions": [
                    "Implement memory-efficient data structures",
                    "Use streaming for large datasets",
                    "Optimize garbage collection",
                    "Consider increasing instance size"
                ]
            })

        # Response time optimization
        if "p95" in stats["response_times_ms"]:
            p95_time = stats["response_times_ms"]["p95"]
            if p95_time > 500:
                recommendations.append({
                    "category": "latency",
                    "priority": "medium",
                    "title": "High Response Times Detected",
                    "description": f"P95 response time is {p95_time:.1f}ms. Performance optimization needed.",
                    "actions": [
                        "Implement caching layers",
                        "Optimize database queries",
                        "Use async processing",
                        "Implement request batching"
                    ]
                })

        # Throughput optimization
        throughput = stats["throughput_rps"]["average"]
        if throughput < 100:  # Adjust threshold based on requirements
            recommendations.append({
                "category": "throughput",
                "priority": "medium",
                "title": "Low Throughput Detected",
                "description": f"Average throughput is {throughput:.1f} RPS. Consider optimization.",
                "actions": [
                    "Implement horizontal scaling",
                    "Optimize I/O operations",
                    "Use connection pooling",
                    "Implement load balancing"
                ]
            })

        return recommendations

    def export_metrics(self, format: str = "json") -> str:
        """Export performance metrics"""

        if format == "json":
            return json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stats": self.get_performance_stats(),
                "anomalies": self.detect_anomalies(),
                "recommendations": self.get_optimization_recommendations()
            }, indent=2)

        return ""

    def reset_metrics(self):
        """Reset all performance metrics"""
        for metric_queue in self.metrics.values():
            metric_queue.clear()
        logger.info("Performance metrics reset")


class PerformanceProfiler:
    """Code profiling utilities"""

    def __init__(self):
        self.profiles = {}

    def profile_function(self, func_name: str):
        """Decorator to profile function performance"""

        def decorator(func):
            def wrapper(*args, **kwargs):
                start_time = time.time()
                start_memory = psutil.Process().memory_info().rss

                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    end_time = time.time()
                    end_memory = psutil.Process().memory_info().rss

                    execution_time = end_time - start_time
                    memory_delta = end_memory - start_memory

                    if func_name not in self.profiles:
                        self.profiles[func_name] = {
                            "calls": 0,
                            "total_time": 0,
                            "total_memory": 0,
                            "min_time": float('inf'),
                            "max_time": 0,
                            "avg_time": 0,
                            "avg_memory": 0
                        }

                    profile = self.profiles[func_name]
                    profile["calls"] += 1
                    profile["total_time"] += execution_time
                    profile["total_memory"] += memory_delta
                    profile["min_time"] = min(profile["min_time"], execution_time)
                    profile["max_time"] = max(profile["max_time"], execution_time)
                    profile["avg_time"] = profile["total_time"] / profile["calls"]
                    profile["avg_memory"] = profile["total_memory"] / profile["calls"]

            return wrapper
        return decorator

    def get_profiles(self) -> Dict[str, Any]:
        """Get profiling results"""
        return self.profiles.copy()

    def reset_profiles(self):
        """Reset profiling data"""
        self.profiles.clear()


# Global instances
performance_monitor = PerformanceMonitor()
performance_profiler = PerformanceProfiler()


async def initialize_performance_monitoring():
    """Initialize performance monitoring"""
    performance_monitor.start_monitoring()
    logger.info("Performance monitoring initialized")


def get_performance_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance"""
    return performance_monitor


def get_performance_profiler() -> PerformanceProfiler:
    """Get global performance profiler instance"""
    return performance_profiler