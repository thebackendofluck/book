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
Automated Baseline Performance Measurement for Casino Platforms
================================================================
Captures baseline metrics across all critical casino subsystems:
  - API response times (login, bet placement, cashout, lobby)
  - Database query latencies (player lookup, transaction insert, leaderboard)
  - WebSocket round-trip times (game events, chat, live dealer)
  - Cache hit rates (Redis/Memcached)
  - Game round processing times (RNG call, settlement, payout)
  - CDN asset delivery times (game assets, thumbnails, JS bundles)

Usage:
    python3 performance_baseline.py --env staging --duration 300 --output baseline_report.json
    python3 performance_baseline.py --env production --compare baseline_report.json
"""

import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

try:
    import aiohttp  # ty:ignore[unresolved-import]
except ImportError:
    print("Install: pip install aiohttp")
    raise

try:
    import websockets  # ty:ignore[unresolved-import]
except ImportError:
    websockets = None  # ty:ignore[invalid-assignment]
    print("WARNING: pip install websockets — for WebSocket baseline tests")

try:
    import redis  # ty:ignore[unresolved-import]
except ImportError:
    redis = None  # ty:ignore[invalid-assignment]
    print("WARNING: pip install redis — for cache hit rate tests")

try:
    import psycopg2  # ty:ignore[unresolved-import]
except ImportError:
    psycopg2 = None  # ty:ignore[invalid-assignment]
    print("WARNING: pip install psycopg2-binary — for database baseline tests")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("perf-baseline")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENV_CONFIGS = {
    "staging": {
        "base_url": "https://staging-api.casino.example.com",
        "ws_url": "wss://staging-ws.casino.example.com",
        "db_host": "staging-db.casino.internal",
        "db_port": 5432,
        "db_name": "casino_staging",
        "redis_host": "staging-redis.casino.internal",
        "redis_port": 6379,
        "cdn_base": "https://staging-cdn.casino.example.com",
    },
    "production": {
        "base_url": "https://api.casino.example.com",
        "ws_url": "wss://ws.casino.example.com",
        "db_host": "prod-db.casino.internal",
        "db_port": 5432,
        "db_name": "casino_production",
        "redis_host": "prod-redis.casino.internal",
        "redis_port": 6379,
        "cdn_base": "https://cdn.casino.example.com",
    },
}

# Casino-specific API endpoints to benchmark
API_ENDPOINTS = [
    {"name": "lobby_games", "method": "GET", "path": "/api/v1/lobby/games", "slo_ms": 200},
    {"name": "player_profile", "method": "GET", "path": "/api/v1/player/profile", "slo_ms": 150},
    {"name": "player_balance", "method": "GET", "path": "/api/v1/wallet/balance", "slo_ms": 100},
    {"name": "place_bet", "method": "POST", "path": "/api/v1/games/slots/bet",
     "body": {"game_id": "starburst-xxxtreme", "stake_cents": 100, "currency": "EUR", "lines": 20},
     "slo_ms": 300},
    {"name": "cashout_request", "method": "POST", "path": "/api/v1/wallet/cashout",
     "body": {"amount_cents": 5000, "method": "bank_transfer"}, "slo_ms": 500},
    {"name": "game_history", "method": "GET", "path": "/api/v1/player/history?limit=50", "slo_ms": 300},
    {"name": "bonus_list", "method": "GET", "path": "/api/v1/promotions/active", "slo_ms": 250},
    {"name": "search_games", "method": "GET", "path": "/api/v1/lobby/search?q=roulette", "slo_ms": 200},
    {"name": "leaderboard", "method": "GET", "path": "/api/v1/tournaments/leaderboard/weekly", "slo_ms": 350},
    {"name": "kyc_status", "method": "GET", "path": "/api/v1/player/kyc/status", "slo_ms": 200},
]

# Database queries to benchmark
DB_QUERIES = [
    {
        "name": "player_lookup_by_id",
        "query": "SELECT id, username, status, created_at FROM players WHERE id = %s",
        "params": (1,),
        "slo_ms": 5,
    },
    {
        "name": "player_lookup_by_email",
        "query": "SELECT id, username, email FROM players WHERE email = %s",
        "params": ("test@example.com",),
        "slo_ms": 10,
    },
    {
        "name": "transaction_insert",
        "query": """INSERT INTO transactions (player_id, type, amount_cents, currency, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW()) RETURNING id""",
        "params": (1, "bet", 100, "EUR", "completed"),
        "slo_ms": 15,
        "rollback": True,
    },
    {
        "name": "game_round_history",
        "query": """SELECT gr.id, gr.game_id, gr.stake, gr.payout, gr.created_at
                    FROM game_rounds gr WHERE gr.player_id = %s
                    ORDER BY gr.created_at DESC LIMIT 50""",
        "params": (1,),
        "slo_ms": 20,
    },
    {
        "name": "leaderboard_aggregate",
        "query": """SELECT p.username, SUM(gr.payout - gr.stake) as net_win
                    FROM game_rounds gr JOIN players p ON gr.player_id = p.id
                    WHERE gr.created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY p.username ORDER BY net_win DESC LIMIT 100""",
        "params": (),
        "slo_ms": 100,
    },
    {
        "name": "active_session_count",
        "query": "SELECT COUNT(*) FROM player_sessions WHERE expires_at > NOW()",
        "params": (),
        "slo_ms": 30,
    },
]

# CDN assets to test
CDN_ASSETS = [
    {"name": "game_thumbnail", "path": "/assets/games/starburst/thumb_300x200.webp", "slo_ms": 50},
    {"name": "game_bundle_js", "path": "/assets/games/starburst/bundle.min.js", "slo_ms": 100},
    {"name": "lobby_sprite", "path": "/assets/ui/lobby-sprite.png", "slo_ms": 80},
    {"name": "font_woff2", "path": "/assets/fonts/casino-icons.woff2", "slo_ms": 40},
]


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class LatencySample:
    timestamp: float
    latency_ms: float
    status_code: Optional[int] = None
    error: Optional[str] = None


@dataclass
class MetricResult:
    name: str
    category: str
    slo_ms: float
    samples: list = field(default_factory=list)

    @property
    def latencies(self):
        return [s.latency_ms for s in self.samples if s.error is None]

    @property
    def error_count(self):
        return sum(1 for s in self.samples if s.error is not None)

    def summary(self) -> dict:
        lats = self.latencies
        if not lats:
            return {"name": self.name, "category": self.category, "error": "no successful samples"}
        return {
            "name": self.name,
            "category": self.category,
            "slo_ms": self.slo_ms,
            "sample_count": len(self.samples),
            "error_count": self.error_count,
            "error_rate_pct": round(self.error_count / len(self.samples) * 100, 2),
            "min_ms": round(min(lats), 2),
            "max_ms": round(max(lats), 2),
            "mean_ms": round(statistics.mean(lats), 2),
            "median_ms": round(statistics.median(lats), 2),
            "p95_ms": round(sorted(lats)[int(len(lats) * 0.95)] if len(lats) >= 20 else max(lats), 2),
            "p99_ms": round(sorted(lats)[int(len(lats) * 0.99)] if len(lats) >= 100 else max(lats), 2),
            "stddev_ms": round(statistics.stdev(lats), 2) if len(lats) > 1 else 0,
            "slo_met_pct": round(sum(1 for l in lats if l <= self.slo_ms) / len(lats) * 100, 2),
        }


# ---------------------------------------------------------------------------
# Benchmark Runners
# ---------------------------------------------------------------------------

async def benchmark_api_endpoints(config: dict, iterations: int = 50) -> list[MetricResult]:
    """Measure API endpoint response times."""
    results = []
    auth_token = "Bearer test-baseline-token"  # Replace with real auth in production
    headers = {"Authorization": auth_token, "Content-Type": "application/json"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for endpoint in API_ENDPOINTS:
            metric = MetricResult(
                name=endpoint["name"], category="api", slo_ms=endpoint["slo_ms"]  # ty:ignore[invalid-argument-type]
            )
            url = urljoin(config["base_url"], endpoint["path"])  # ty:ignore[invalid-argument-type]
            logger.info(f"Benchmarking API: {endpoint['name']} ({iterations} iterations)")

            for _ in range(iterations):
                start = time.perf_counter()
                try:
                    if endpoint["method"] == "GET":
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            await resp.read()
                            elapsed = (time.perf_counter() - start) * 1000
                            metric.samples.append(LatencySample(
                                timestamp=time.time(), latency_ms=elapsed, status_code=resp.status
                            ))
                    elif endpoint["method"] == "POST":
                        async with session.post(
                            url, json=endpoint.get("body", {}),
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            await resp.read()
                            elapsed = (time.perf_counter() - start) * 1000
                            metric.samples.append(LatencySample(
                                timestamp=time.time(), latency_ms=elapsed, status_code=resp.status
                            ))
                except Exception as e:
                    elapsed = (time.perf_counter() - start) * 1000
                    metric.samples.append(LatencySample(
                        timestamp=time.time(), latency_ms=elapsed, error=str(e)
                    ))
                await asyncio.sleep(0.05)  # 50ms between requests to avoid flooding

            results.append(metric)
    return results


async def benchmark_websocket(config: dict, iterations: int = 30) -> list[MetricResult]:
    """Measure WebSocket round-trip latency for game events."""
    if websockets is None:
        logger.warning("Skipping WebSocket benchmarks — websockets not installed")
        return []

    results = []
    ws_events = [
        {"name": "ws_game_spin", "event": "game:spin", "slo_ms": 50},
        {"name": "ws_chat_message", "event": "chat:message", "slo_ms": 100},
        {"name": "ws_live_dealer_action", "event": "live:action", "slo_ms": 80},
        {"name": "ws_balance_update", "event": "wallet:balance_update", "slo_ms": 30},
    ]

    for ws_event in ws_events:
        metric = MetricResult(name=ws_event["name"], category="websocket", slo_ms=ws_event["slo_ms"])  # ty:ignore[invalid-argument-type]
        logger.info(f"Benchmarking WebSocket: {ws_event['name']} ({iterations} iterations)")

        try:
            async with websockets.connect(
                config["ws_url"],
                extra_headers={"Authorization": "Bearer test-baseline-token"},
                ping_interval=20,
            ) as ws:
                for i in range(iterations):
                    payload = json.dumps({
                        "type": ws_event["event"],
                        "request_id": f"baseline-{i}",
                        "timestamp": time.time(),
                    })
                    start = time.perf_counter()
                    try:
                        await ws.send(payload)
                        response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        elapsed = (time.perf_counter() - start) * 1000
                        metric.samples.append(LatencySample(
                            timestamp=time.time(), latency_ms=elapsed
                        ))
                    except Exception as e:
                        elapsed = (time.perf_counter() - start) * 1000
                        metric.samples.append(LatencySample(
                            timestamp=time.time(), latency_ms=elapsed, error=str(e)
                        ))
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"WebSocket connection failed for {ws_event['name']}: {e}")
            metric.samples.append(LatencySample(timestamp=time.time(), latency_ms=0, error=str(e)))

        results.append(metric)
    return results


def benchmark_database(config: dict, iterations: int = 30) -> list[MetricResult]:
    """Measure database query latencies."""
    if psycopg2 is None:
        logger.warning("Skipping database benchmarks — psycopg2 not installed")
        return []

    results = []
    try:
        conn = psycopg2.connect(
            host=config["db_host"],
            port=config["db_port"],
            dbname=config["db_name"],
            user="benchmark_reader",
            password="benchmark_readonly",
            connect_timeout=5,
        )
        conn.autocommit = False
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return []

    for query_def in DB_QUERIES:
        metric = MetricResult(
            name=query_def["name"], category="database", slo_ms=query_def["slo_ms"]  # ty:ignore[invalid-argument-type]
        )
        logger.info(f"Benchmarking DB: {query_def['name']} ({iterations} iterations)")

        for _ in range(iterations):
            cursor = conn.cursor()
            start = time.perf_counter()
            try:
                cursor.execute(query_def["query"], query_def["params"])
                cursor.fetchall()
                elapsed = (time.perf_counter() - start) * 1000
                metric.samples.append(LatencySample(timestamp=time.time(), latency_ms=elapsed))
                if query_def.get("rollback"):
                    conn.rollback()
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                metric.samples.append(LatencySample(
                    timestamp=time.time(), latency_ms=elapsed, error=str(e)
                ))
                conn.rollback()
            finally:
                cursor.close()

        results.append(metric)

    conn.close()
    return results


def benchmark_cache(config: dict, iterations: int = 100) -> list[MetricResult]:
    """Measure Redis cache hit rates and latency."""
    if redis is None:
        logger.warning("Skipping cache benchmarks — redis not installed")
        return []

    results = []
    try:
        r = redis.Redis(
            host=config["redis_host"],
            port=config["redis_port"],
            decode_responses=True,
            socket_timeout=5,
        )
        r.ping()
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return []

    cache_tests = [
        {"name": "cache_player_session", "key_pattern": "session:player:{}", "slo_ms": 2},
        {"name": "cache_game_config", "key_pattern": "game:config:{}", "slo_ms": 2},
        {"name": "cache_lobby_games", "key_pattern": "lobby:games:all", "slo_ms": 5},
        {"name": "cache_bonus_rules", "key_pattern": "bonus:rules:active", "slo_ms": 3},
    ]

    for test in cache_tests:
        metric = MetricResult(name=test["name"], category="cache", slo_ms=test["slo_ms"])  # ty:ignore[invalid-argument-type]
        logger.info(f"Benchmarking cache: {test['name']} ({iterations} iterations)")

        # Pre-populate test key
        test_key = test["key_pattern"].format("baseline-test")  # ty:ignore[unresolved-attribute,possibly-missing-attribute]
        r.setex(test_key, 300, json.dumps({"baseline": True, "ts": time.time()}))

        for _ in range(iterations):
            start = time.perf_counter()
            try:
                result = r.get(test_key)
                elapsed = (time.perf_counter() - start) * 1000
                metric.samples.append(LatencySample(timestamp=time.time(), latency_ms=elapsed))
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                metric.samples.append(LatencySample(
                    timestamp=time.time(), latency_ms=elapsed, error=str(e)
                ))

        r.delete(test_key)
        results.append(metric)

    # Also measure overall cache hit rate from Redis INFO
    info_metric = MetricResult(name="cache_hit_rate", category="cache", slo_ms=0)
    try:
        info = r.info("stats")
        hits = info.get("keyspace_hits", 0)  # ty:ignore[unresolved-attribute]
        misses = info.get("keyspace_misses", 0)  # ty:ignore[unresolved-attribute]
        total = hits + misses
        hit_rate = (hits / total * 100) if total > 0 else 0
        info_metric.samples.append(LatencySample(
            timestamp=time.time(), latency_ms=hit_rate  # Reusing latency field for %
        ))
        logger.info(f"Overall cache hit rate: {hit_rate:.1f}% ({hits}/{total})")
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")

    results.append(info_metric)
    return results


async def benchmark_cdn(config: dict, iterations: int = 20) -> list[MetricResult]:
    """Measure CDN asset delivery times."""
    results = []
    async with aiohttp.ClientSession() as session:
        for asset in CDN_ASSETS:
            metric = MetricResult(
                name=asset["name"], category="cdn", slo_ms=asset["slo_ms"]  # ty:ignore[invalid-argument-type]
            )
            url = urljoin(config["cdn_base"], asset["path"])  # ty:ignore[invalid-argument-type]
            logger.info(f"Benchmarking CDN: {asset['name']} ({iterations} iterations)")

            for _ in range(iterations):
                start = time.perf_counter()
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
                        elapsed = (time.perf_counter() - start) * 1000
                        metric.samples.append(LatencySample(
                            timestamp=time.time(), latency_ms=elapsed, status_code=resp.status
                        ))
                except Exception as e:
                    elapsed = (time.perf_counter() - start) * 1000
                    metric.samples.append(LatencySample(
                        timestamp=time.time(), latency_ms=elapsed, error=str(e)
                    ))
                await asyncio.sleep(0.1)

            results.append(metric)
    return results


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

def generate_report(all_results: list[MetricResult], env: str, duration: float) -> dict:
    """Generate comprehensive baseline report."""
    report: dict[str, Any] = {
        "metadata": {
            "environment": env,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 2),
            "tool": "casino-performance-baseline",
            "version": "1.0.0",
        },
        "summary": {
            "total_metrics": len(all_results),
            "total_samples": sum(len(r.samples) for r in all_results),
            "categories": {},
        },
        "metrics": [],
        "slo_compliance": [],
    }

    # Per-category summary
    categories = set(r.category for r in all_results)
    for cat in sorted(categories):
        cat_results = [r for r in all_results if r.category == cat]
        slo_met = sum(1 for r in cat_results if r.latencies and
                      r.summary().get("slo_met_pct", 0) >= 99.0)
        report["summary"]["categories"][cat] = {
            "metric_count": len(cat_results),
            "slo_compliance_count": slo_met,
            "slo_compliance_pct": round(slo_met / len(cat_results) * 100, 2) if cat_results else 0,
        }

    # Detailed metrics
    for result in all_results:
        summary = result.summary()
        report["metrics"].append(summary)
        if "slo_met_pct" in summary:
            status = "PASS" if summary["slo_met_pct"] >= 99.0 else "WARN" if summary["slo_met_pct"] >= 95.0 else "FAIL"
            report["slo_compliance"].append({
                "metric": summary["name"],
                "category": summary["category"],
                "slo_ms": summary["slo_ms"],
                "p95_ms": summary.get("p95_ms"),
                "slo_met_pct": summary["slo_met_pct"],
                "status": status,
            })

    return report


def compare_reports(current: dict, baseline: dict) -> dict:
    """Compare current results against a saved baseline."""
    comparison = {
        "baseline_timestamp": baseline["metadata"]["timestamp"],
        "current_timestamp": current["metadata"]["timestamp"],
        "regressions": [],
        "improvements": [],
        "unchanged": [],
    }

    baseline_metrics = {m["name"]: m for m in baseline["metrics"]}

    for metric in current["metrics"]:
        name = metric["name"]
        if name not in baseline_metrics:
            continue

        base = baseline_metrics[name]
        if "p95_ms" not in metric or "p95_ms" not in base:
            continue

        pct_change = ((metric["p95_ms"] - base["p95_ms"]) / base["p95_ms"] * 100) if base["p95_ms"] > 0 else 0
        entry = {
            "metric": name,
            "category": metric["category"],
            "baseline_p95_ms": base["p95_ms"],
            "current_p95_ms": metric["p95_ms"],
            "change_pct": round(pct_change, 2),
        }

        if pct_change > 10:  # More than 10% slower
            entry["severity"] = "HIGH" if pct_change > 25 else "MEDIUM"
            comparison["regressions"].append(entry)
        elif pct_change < -10:  # More than 10% faster
            comparison["improvements"].append(entry)
        else:
            comparison["unchanged"].append(entry)

    comparison["regression_count"] = len(comparison["regressions"])
    comparison["improvement_count"] = len(comparison["improvements"])
    return comparison


def print_report(report: dict):
    """Print human-readable report to console."""
    print("\n" + "=" * 80)
    print("CASINO PERFORMANCE BASELINE REPORT")
    print(f"Environment: {report['metadata']['environment']}")
    print(f"Timestamp:   {report['metadata']['timestamp']}")
    print(f"Duration:    {report['metadata']['duration_seconds']}s")
    print("=" * 80)

    print(f"\n{'Metric':<30} {'Category':<12} {'SLO':<8} {'P95':<10} {'P99':<10} {'SLO%':<8} {'Status'}")
    print("-" * 90)

    for slo in report.get("slo_compliance", []):
        status_icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}.get(slo["status"], "???")
        print(f"{slo['metric']:<30} {slo['category']:<12} {slo['slo_ms']:<8.0f} "
              f"{slo.get('p95_ms', 'N/A'):<10} {'N/A':<10} {slo['slo_met_pct']:<8.1f} {status_icon}")

    print("\n" + "-" * 90)
    for cat, info in report["summary"]["categories"].items():
        print(f"  {cat}: {info['slo_compliance_count']}/{info['metric_count']} SLOs met "
              f"({info['slo_compliance_pct']:.1f}%)")
    print("=" * 80)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Casino Performance Baseline Tool")
    parser.add_argument("--env", choices=["staging", "production"], default="staging",
                        help="Target environment")
    parser.add_argument("--duration", type=int, default=300,
                        help="Maximum test duration in seconds (soft limit)")
    parser.add_argument("--iterations", type=int, default=50,
                        help="Number of iterations per metric")
    parser.add_argument("--output", type=str, default="baseline_report.json",
                        help="Output file for baseline report")
    parser.add_argument("--compare", type=str, default=None,
                        help="Compare against a saved baseline report")
    parser.add_argument("--skip-db", action="store_true", help="Skip database benchmarks")
    parser.add_argument("--skip-cache", action="store_true", help="Skip cache benchmarks")
    parser.add_argument("--skip-ws", action="store_true", help="Skip WebSocket benchmarks")
    parser.add_argument("--skip-cdn", action="store_true", help="Skip CDN benchmarks")
    args = parser.parse_args()

    config = ENV_CONFIGS[args.env]
    logger.info(f"Starting baseline measurement — env={args.env}, iterations={args.iterations}")
    start_time = time.time()

    all_results = []

    # Run API benchmarks (always)
    api_results = await benchmark_api_endpoints(config, args.iterations)
    all_results.extend(api_results)

    # Run WebSocket benchmarks
    if not args.skip_ws:
        ws_results = await benchmark_websocket(config, min(args.iterations, 30))
        all_results.extend(ws_results)

    # Run database benchmarks
    if not args.skip_db:
        db_results = benchmark_database(config, min(args.iterations, 30))
        all_results.extend(db_results)

    # Run cache benchmarks
    if not args.skip_cache:
        cache_results = benchmark_cache(config, args.iterations * 2)
        all_results.extend(cache_results)

    # Run CDN benchmarks
    if not args.skip_cdn:
        cdn_results = await benchmark_cdn(config, min(args.iterations, 20))
        all_results.extend(cdn_results)

    duration = time.time() - start_time
    report = generate_report(all_results, args.env, duration)

    # Save report
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Baseline report saved to {args.output}")

    # Print to console
    print_report(report)

    # Compare if baseline provided
    if args.compare:
        with open(args.compare) as f:
            baseline = json.load(f)
        comparison = compare_reports(report, baseline)
        print(f"\nComparison vs baseline ({baseline['metadata']['timestamp']}):")
        print(f"  Regressions:  {comparison['regression_count']}")
        print(f"  Improvements: {comparison['improvement_count']}")
        print(f"  Unchanged:    {len(comparison['unchanged'])}")
        if comparison["regressions"]:
            print("\n  REGRESSIONS DETECTED:")
            for reg in comparison["regressions"]:
                print(f"    [{reg['severity']}] {reg['metric']}: "
                      f"{reg['baseline_p95_ms']:.1f}ms -> {reg['current_p95_ms']:.1f}ms "
                      f"({reg['change_pct']:+.1f}%)")

        comp_file = args.output.replace(".json", "_comparison.json")
        with open(comp_file, "w") as f:
            json.dump(comparison, f, indent=2)
        logger.info(f"Comparison report saved to {comp_file}")

        # Exit non-zero if critical regressions found
        critical = [r for r in comparison["regressions"] if r["severity"] == "HIGH"]
        if critical:
            logger.error(f"{len(critical)} critical performance regressions detected!")
            return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
