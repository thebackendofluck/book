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
Real User Monitoring (RUM) + Synthetic Testing for Casino Platforms
====================================================================
Combines passive RUM data collection with active synthetic monitoring:

  RUM (passive):
    - Collects Core Web Vitals (LCP, FID, CLS, TTFB, INP)
    - Game load time tracking (lobby→first-spin)
    - Payment flow timing (initiate→confirm)
    - Player session quality scoring

  Synthetic (active):
    - Scheduled browser-based tests (Playwright)
    - Critical user journeys: login, deposit, play game, cashout
    - Multi-region testing (EU, US, APAC, LATAM)
    - WebSocket connection quality probes

Usage:
    # Run synthetic tests
    python3 synthetic_monitor.py --mode synthetic --regions eu-west,us-east

    # Process RUM beacon data
    python3 synthetic_monitor.py --mode rum --beacon-endpoint http://localhost:8080/rum

    # Full monitoring report
    python3 synthetic_monitor.py --mode report --output rum_report.json
"""

import argparse
import asyncio
import json
import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import aiohttp  # ty:ignore[unresolved-import]
except ImportError:
    print("Install: pip install aiohttp")
    raise

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rum-synthetic")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGIONS = {
    "eu-west": {
        "name": "Europe West (Ireland)",
        "base_url": "https://www.casino.example.com",
        "expected_ttfb_ms": 50,
    },
    "eu-central": {
        "name": "Europe Central (Frankfurt)",
        "base_url": "https://www.casino.example.com",
        "expected_ttfb_ms": 60,
    },
    "us-east": {
        "name": "US East (Virginia)",
        "base_url": "https://us.casino.example.com",
        "expected_ttfb_ms": 120,
    },
    "apac-southeast": {
        "name": "Asia Pacific (Singapore)",
        "base_url": "https://apac.casino.example.com",
        "expected_ttfb_ms": 200,
    },
    "latam-south": {
        "name": "Latin America (São Paulo)",
        "base_url": "https://latam.casino.example.com",
        "expected_ttfb_ms": 180,
    },
}

# Casino-specific Core Web Vitals thresholds
CWV_THRESHOLDS = {
    "lcp_ms": {"good": 2500, "poor": 4000},       # Largest Contentful Paint
    "fid_ms": {"good": 100, "poor": 300},          # First Input Delay
    "cls": {"good": 0.1, "poor": 0.25},            # Cumulative Layout Shift
    "ttfb_ms": {"good": 200, "poor": 600},         # Time to First Byte
    "inp_ms": {"good": 200, "poor": 500},          # Interaction to Next Paint
    "game_load_ms": {"good": 3000, "poor": 8000},  # Casino-specific: game load time
    "lobby_render_ms": {"good": 1500, "poor": 3000},
}

# Critical user journeys for synthetic testing
SYNTHETIC_JOURNEYS = [
    {
        "name": "login_flow",
        "steps": [
            {"action": "navigate", "url": "/login", "slo_ms": 2000},
            {"action": "fill", "selector": "#email", "value": "test@casino.example.com"},
            {"action": "fill", "selector": "#password", "value": "test-password"},
            {"action": "click", "selector": "#login-btn"},
            {"action": "wait_for", "selector": ".lobby-container", "slo_ms": 3000},
        ],
    },
    {
        "name": "game_launch",
        "steps": [
            {"action": "navigate", "url": "/lobby", "slo_ms": 2000},
            {"action": "click", "selector": "[data-game='starburst-xxxtreme']", "slo_ms": 500},
            {"action": "wait_for", "selector": ".game-canvas", "slo_ms": 5000},
            {"action": "click", "selector": "#spin-btn"},
            {"action": "wait_for", "selector": ".spin-result", "slo_ms": 3000},
        ],
    },
    {
        "name": "deposit_flow",
        "steps": [
            {"action": "navigate", "url": "/wallet/deposit", "slo_ms": 2000},
            {"action": "click", "selector": "[data-method='card']"},
            {"action": "fill", "selector": "#amount", "value": "50"},
            {"action": "click", "selector": "#deposit-btn"},
            {"action": "wait_for", "selector": ".deposit-success", "slo_ms": 10000},
        ],
    },
    {
        "name": "cashout_flow",
        "steps": [
            {"action": "navigate", "url": "/wallet/cashout", "slo_ms": 2000},
            {"action": "fill", "selector": "#cashout-amount", "value": "100"},
            {"action": "click", "selector": "[data-method='bank_transfer']"},
            {"action": "click", "selector": "#cashout-btn"},
            {"action": "wait_for", "selector": ".cashout-pending", "slo_ms": 5000},
        ],
    },
    {
        "name": "live_dealer_join",
        "steps": [
            {"action": "navigate", "url": "/live-casino", "slo_ms": 3000},
            {"action": "click", "selector": "[data-table='blackjack-vip-1']"},
            {"action": "wait_for", "selector": ".video-stream.playing", "slo_ms": 8000},
            {"action": "wait_for", "selector": ".bet-controls.active", "slo_ms": 2000},
        ],
    },
]


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class RUMBeacon:
    """A single RUM beacon from a player's browser."""
    session_id: str
    player_id: Optional[str]
    page_url: str
    timestamp: float
    region: str
    device_type: str  # desktop, mobile, tablet
    connection_type: str  # 4g, 3g, wifi, wired
    lcp_ms: Optional[float] = None
    fid_ms: Optional[float] = None
    cls: Optional[float] = None
    ttfb_ms: Optional[float] = None
    inp_ms: Optional[float] = None
    game_load_ms: Optional[float] = None
    lobby_render_ms: Optional[float] = None
    custom_metrics: dict = field(default_factory=dict)


@dataclass
class SyntheticResult:
    """Result of a synthetic test run."""
    journey_name: str
    region: str
    timestamp: float
    total_duration_ms: float
    step_results: list
    success: bool
    error: Optional[str] = None
    screenshots: list = field(default_factory=list)


@dataclass
class WebVitalsAggregate:
    """Aggregated Web Vitals metrics."""
    metric_name: str
    p50: float
    p75: float
    p95: float
    p99: float
    good_pct: float
    needs_improvement_pct: float
    poor_pct: float
    sample_count: int


# ---------------------------------------------------------------------------
# RUM Beacon Server
# ---------------------------------------------------------------------------

class RUMCollector:
    """Collects and processes RUM beacons from player browsers."""

    # JavaScript snippet to inject into casino pages for RUM collection
    RUM_SNIPPET = """
    <!-- Casino RUM Collection Snippet -->
    <script>
    (function() {
      const BEACON_URL = '{{BEACON_ENDPOINT}}';
      const SESSION_ID = crypto.randomUUID();

      // Core Web Vitals via web-vitals library
      function sendBeacon(metrics) {
        const payload = {
          session_id: SESSION_ID,
          player_id: window.__PLAYER_ID__ || null,
          page_url: location.href,
          timestamp: Date.now(),
          region: '{{REGION}}',
          device_type: /Mobi/.test(navigator.userAgent) ? 'mobile' : 'desktop',
          connection_type: navigator.connection?.effectiveType || 'unknown',
          ...metrics
        };
        if (navigator.sendBeacon) {
          navigator.sendBeacon(BEACON_URL, JSON.stringify(payload));
        } else {
          fetch(BEACON_URL, { method: 'POST', body: JSON.stringify(payload), keepalive: true });
        }
      }

      // Observe LCP
      new PerformanceObserver((list) => {
        const entries = list.getEntries();
        const last = entries[entries.length - 1];
        sendBeacon({ lcp_ms: last.startTime });
      }).observe({ type: 'largest-contentful-paint', buffered: true });

      // Observe FID
      new PerformanceObserver((list) => {
        const entry = list.getEntries()[0];
        sendBeacon({ fid_ms: entry.processingStart - entry.startTime });
      }).observe({ type: 'first-input', buffered: true });

      // Observe CLS
      let clsValue = 0;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) clsValue += entry.value;
        }
        sendBeacon({ cls: clsValue });
      }).observe({ type: 'layout-shift', buffered: true });

      // Observe INP
      let inpValue = 0;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          inpValue = Math.max(inpValue, entry.duration);
        }
        sendBeacon({ inp_ms: inpValue });
      }).observe({ type: 'event', buffered: true });

      // TTFB
      window.addEventListener('load', () => {
        const nav = performance.getEntriesByType('navigation')[0];
        if (nav) sendBeacon({ ttfb_ms: nav.responseStart });
      });

      // Casino-specific: Game load time
      window.addEventListener('casino:game:loaded', (e) => {
        sendBeacon({ game_load_ms: e.detail.loadTime });
      });

      // Casino-specific: Lobby render time
      window.addEventListener('casino:lobby:rendered', (e) => {
        sendBeacon({ lobby_render_ms: e.detail.renderTime });
      });
    })();
    </script>
    """

    def __init__(self):
        self.beacons: list[RUMBeacon] = []

    def add_beacon(self, data: dict):
        """Process incoming RUM beacon."""
        beacon = RUMBeacon(
            session_id=data.get("session_id", "unknown"),
            player_id=data.get("player_id"),
            page_url=data.get("page_url", ""),
            timestamp=data.get("timestamp", time.time()),
            region=data.get("region", "unknown"),
            device_type=data.get("device_type", "unknown"),
            connection_type=data.get("connection_type", "unknown"),
            lcp_ms=data.get("lcp_ms"),
            fid_ms=data.get("fid_ms"),
            cls=data.get("cls"),
            ttfb_ms=data.get("ttfb_ms"),
            inp_ms=data.get("inp_ms"),
            game_load_ms=data.get("game_load_ms"),
            lobby_render_ms=data.get("lobby_render_ms"),
            custom_metrics=data.get("custom_metrics", {}),
        )
        self.beacons.append(beacon)
        return beacon

    def aggregate_vitals(self, metric_name: str, filter_region: Optional[str] = None,
                         filter_device: Optional[str] = None) -> Optional[WebVitalsAggregate]:
        """Compute aggregated stats for a given Web Vital metric."""
        values = []
        for b in self.beacons:
            if filter_region and b.region != filter_region:
                continue
            if filter_device and b.device_type != filter_device:
                continue
            val = getattr(b, metric_name, None)
            if val is not None:
                values.append(val)

        if not values:
            return None

        values.sort()
        thresholds = CWV_THRESHOLDS.get(metric_name, {})
        good_thresh = thresholds.get("good", float("inf"))
        poor_thresh = thresholds.get("poor", float("inf"))

        good_count = sum(1 for v in values if v <= good_thresh)
        poor_count = sum(1 for v in values if v > poor_thresh)
        ni_count = len(values) - good_count - poor_count

        return WebVitalsAggregate(
            metric_name=metric_name,
            p50=values[int(len(values) * 0.50)],
            p75=values[int(len(values) * 0.75)],
            p95=values[int(len(values) * 0.95)],
            p99=values[min(int(len(values) * 0.99), len(values) - 1)],
            good_pct=round(good_count / len(values) * 100, 1),
            needs_improvement_pct=round(ni_count / len(values) * 100, 1),
            poor_pct=round(poor_count / len(values) * 100, 1),
            sample_count=len(values),
        )

    def generate_report(self) -> dict:
        """Generate full RUM report with breakdowns."""
        report: dict[str, Any] = {
            "total_beacons": len(self.beacons),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "global_vitals": {},
            "by_region": {},
            "by_device": {},
            "casino_specific": {},
        }

        # Global vitals
        for metric in ["lcp_ms", "fid_ms", "cls", "ttfb_ms", "inp_ms", "game_load_ms", "lobby_render_ms"]:
            agg = self.aggregate_vitals(metric)
            if agg:
                report["global_vitals"][metric] = {
                    "p50": agg.p50, "p75": agg.p75, "p95": agg.p95,
                    "good_pct": agg.good_pct, "poor_pct": agg.poor_pct,
                    "samples": agg.sample_count,
                }

        # By region
        regions = set(b.region for b in self.beacons)
        for region in sorted(regions):
            report["by_region"][region] = {}
            for metric in ["lcp_ms", "ttfb_ms", "game_load_ms"]:
                agg = self.aggregate_vitals(metric, filter_region=region)
                if agg:
                    report["by_region"][region][metric] = {"p75": agg.p75, "p95": agg.p95}

        # By device
        for device in ["desktop", "mobile", "tablet"]:
            report["by_device"][device] = {}
            for metric in ["lcp_ms", "fid_ms", "cls"]:
                agg = self.aggregate_vitals(metric, filter_device=device)
                if agg:
                    report["by_device"][device][metric] = {
                        "p75": agg.p75, "good_pct": agg.good_pct,
                    }

        return report


# ---------------------------------------------------------------------------
# Synthetic Monitor
# ---------------------------------------------------------------------------

class SyntheticMonitor:
    """Runs synthetic browser tests against casino critical paths."""

    def __init__(self, regions: list[str]):
        self.regions = [REGIONS[r] for r in regions if r in REGIONS]
        self.results: list[SyntheticResult] = []

    async def run_journey_http(self, journey: dict, region: dict) -> SyntheticResult:
        """
        Run a synthetic journey using HTTP requests (lightweight fallback).
        For full browser testing, use run_journey_playwright() instead.
        """
        step_results = []
        total_start = time.perf_counter()
        success = True
        error_msg = None

        async with aiohttp.ClientSession() as session:
            for step in journey["steps"]:
                step_start = time.perf_counter()
                step_result = {"action": step["action"], "success": True}

                try:
                    if step["action"] == "navigate":
                        url = f"{region['base_url']}{step['url']}"
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            await resp.read()
                            step_result["status_code"] = resp.status
                            step_result["success"] = resp.status < 400
                    elif step["action"] in ("fill", "click", "wait_for"):
                        # Simulated — in production use Playwright
                        await asyncio.sleep(random.uniform(0.05, 0.2))
                        step_result["selector"] = step.get("selector", "")
                except Exception as e:
                    step_result["success"] = False
                    step_result["error"] = str(e)
                    success = False
                    error_msg = str(e)

                step_result["duration_ms"] = round((time.perf_counter() - step_start) * 1000, 2)
                slo = step.get("slo_ms")
                if slo and step_result["duration_ms"] > slo:
                    step_result["slo_violated"] = True
                step_results.append(step_result)

                if not step_result["success"]:
                    break

        total_duration = (time.perf_counter() - total_start) * 1000
        result = SyntheticResult(
            journey_name=journey["name"],
            region=region["name"],
            timestamp=time.time(),
            total_duration_ms=round(total_duration, 2),
            step_results=step_results,
            success=success,
            error=error_msg,
        )
        self.results.append(result)
        return result

    async def run_journey_playwright(self, journey: dict, region: dict) -> SyntheticResult:
        """
        Run synthetic journey with real browser via Playwright.
        Requires: pip install playwright && playwright install chromium
        """
        try:
            from playwright.async_api import async_playwright  # ty:ignore[unresolved-import]
        except ImportError:
            logger.warning("Playwright not installed, falling back to HTTP tests")
            return await self.run_journey_http(journey, region)

        step_results = []
        total_start = time.perf_counter()
        success = True
        error_msg = None
        screenshots = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="CasinoSyntheticMonitor/1.0",
            )
            page = await context.new_page()

            # Enable performance tracing
            await page.evaluate("""() => {
                window.__perfMarks = [];
                const origMark = performance.mark.bind(performance);
                performance.mark = function(name) {
                    window.__perfMarks.push({name, time: performance.now()});
                    return origMark(name);
                };
            }""")

            for i, step in enumerate(journey["steps"]):
                step_start = time.perf_counter()
                step_result = {"action": step["action"], "success": True}

                try:
                    if step["action"] == "navigate":
                        url = f"{region['base_url']}{step['url']}"
                        response = await page.goto(url, wait_until="domcontentloaded",
                                                   timeout=step.get("slo_ms", 10000))
                        step_result["status_code"] = response.status if response else 0
                    elif step["action"] == "fill":
                        await page.fill(step["selector"], step["value"],
                                       timeout=step.get("slo_ms", 5000))
                    elif step["action"] == "click":
                        await page.click(step["selector"],
                                        timeout=step.get("slo_ms", 5000))
                    elif step["action"] == "wait_for":
                        await page.wait_for_selector(step["selector"],
                                                     timeout=step.get("slo_ms", 10000))
                except Exception as e:
                    step_result["success"] = False
                    step_result["error"] = str(e)
                    success = False
                    error_msg = str(e)

                    # Screenshot on failure
                    try:
                        ss_path = f"/tmp/synthetic_{journey['name']}_step{i}_{int(time.time())}.png"
                        await page.screenshot(path=ss_path)
                        screenshots.append(ss_path)
                    except Exception:
                        pass

                step_result["duration_ms"] = round((time.perf_counter() - step_start) * 1000, 2)
                step_results.append(step_result)

                if not step_result["success"]:
                    break

            # Collect Core Web Vitals from the page
            try:
                vitals = await page.evaluate("""() => {
                    const nav = performance.getEntriesByType('navigation')[0];
                    return {
                        ttfb_ms: nav ? nav.responseStart : null,
                        dom_interactive_ms: nav ? nav.domInteractive : null,
                        load_ms: nav ? nav.loadEventEnd : null,
                    };
                }""")
                step_results.append({"action": "collect_vitals", "vitals": vitals, "success": True})
            except Exception:
                pass

            await browser.close()

        total_duration = (time.perf_counter() - total_start) * 1000
        result = SyntheticResult(
            journey_name=journey["name"],
            region=region["name"],
            timestamp=time.time(),
            total_duration_ms=round(total_duration, 2),
            step_results=step_results,
            success=success,
            error=error_msg,
            screenshots=screenshots,
        )
        self.results.append(result)
        return result

    async def run_all_journeys(self, use_browser: bool = False):
        """Run all synthetic journeys across all regions."""
        for region in self.regions:
            logger.info(f"Running synthetic tests for region: {region['name']}")
            for journey in SYNTHETIC_JOURNEYS:
                logger.info(f"  Journey: {journey['name']}")
                if use_browser:
                    await self.run_journey_playwright(journey, region)
                else:
                    await self.run_journey_http(journey, region)
                await asyncio.sleep(1)

    def generate_report(self) -> dict:
        """Generate synthetic test report."""
        report: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_journeys": len(self.results),
            "pass_count": sum(1 for r in self.results if r.success),
            "fail_count": sum(1 for r in self.results if not r.success),
            "by_journey": {},
            "by_region": {},
            "results": [],
        }

        # Group by journey
        for journey in SYNTHETIC_JOURNEYS:
            name = journey["name"]
            journey_results = [r for r in self.results if r.journey_name == name]
            if journey_results:
                durations = [r.total_duration_ms for r in journey_results if r.success]
                report["by_journey"][name] = {
                    "total_runs": len(journey_results),
                    "success_rate": round(sum(1 for r in journey_results if r.success)
                                          / len(journey_results) * 100, 1),
                    "avg_duration_ms": round(statistics.mean(durations), 2) if durations else None,
                    "p95_duration_ms": round(sorted(durations)[int(len(durations) * 0.95)], 2)
                    if len(durations) >= 5 else None,
                }

        # Group by region
        for region_key, region_info in REGIONS.items():
            region_results = [r for r in self.results if r.region == region_info["name"]]
            if region_results:
                report["by_region"][region_key] = {
                    "total_runs": len(region_results),
                    "success_rate": round(sum(1 for r in region_results if r.success)
                                          / len(region_results) * 100, 1),
                }

        # Detailed results
        for r in self.results:
            report["results"].append({
                "journey": r.journey_name,
                "region": r.region,
                "success": r.success,
                "duration_ms": r.total_duration_ms,
                "error": r.error,
                "steps": r.step_results,
            })

        return report


# ---------------------------------------------------------------------------
# Simulated RUM Data Generator (for testing)
# ---------------------------------------------------------------------------

def generate_sample_rum_data(collector: RUMCollector, count: int = 1000):
    """Generate realistic sample RUM data for testing the pipeline."""
    devices = ["desktop"] * 60 + ["mobile"] * 35 + ["tablet"] * 5
    connections = ["wifi"] * 40 + ["4g"] * 30 + ["wired"] * 20 + ["3g"] * 10
    regions = list(REGIONS.keys())
    pages = ["/lobby", "/games/starburst", "/wallet/deposit", "/live-casino",
             "/promotions", "/account/profile", "/games/roulette"]

    for _ in range(count):
        device = random.choice(devices)
        region = random.choice(regions)
        connection = random.choice(connections)

        # Base latency varies by region and connection
        base_factor = {"eu-west": 1.0, "eu-central": 1.1, "us-east": 1.5,
                       "apac-southeast": 2.0, "latam-south": 1.8}.get(region, 1.0)
        conn_factor = {"wired": 0.8, "wifi": 1.0, "4g": 1.5, "3g": 3.0}.get(connection, 1.0)
        device_factor = {"desktop": 1.0, "mobile": 1.3, "tablet": 1.2}.get(device, 1.0)
        total_factor = base_factor * conn_factor * device_factor

        beacon_data: dict[str, Any] = {
            "session_id": f"sim-{random.randint(10000, 99999)}",
            "player_id": f"player-{random.randint(1, 5000)}" if random.random() > 0.3 else None,
            "page_url": random.choice(pages),
            "timestamp": time.time() - random.uniform(0, 3600),
            "region": region,
            "device_type": device,
            "connection_type": connection,
            "lcp_ms": random.gauss(1800 * total_factor, 500 * total_factor),
            "fid_ms": max(0, random.gauss(50 * total_factor, 30 * total_factor)),
            "cls": max(0, random.gauss(0.05 * device_factor, 0.03)),
            "ttfb_ms": random.gauss(80 * base_factor, 30 * base_factor),
            "inp_ms": max(0, random.gauss(120 * total_factor, 50 * total_factor)),
        }

        # Game load time only for game pages
        if "/games/" in beacon_data["page_url"]:
            beacon_data["game_load_ms"] = random.gauss(2500 * total_factor, 800 * total_factor)

        # Lobby render time
        if beacon_data["page_url"] == "/lobby":
            beacon_data["lobby_render_ms"] = random.gauss(1200 * total_factor, 400 * total_factor)

        collector.add_beacon(beacon_data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Casino RUM + Synthetic Monitoring")
    parser.add_argument("--mode", choices=["synthetic", "rum", "report", "demo"],
                        default="demo", help="Operating mode")
    parser.add_argument("--regions", type=str, default="eu-west,us-east",
                        help="Comma-separated list of regions for synthetic tests")
    parser.add_argument("--browser", action="store_true",
                        help="Use real browser (Playwright) for synthetic tests")
    parser.add_argument("--iterations", type=int, default=3,
                        help="Number of iterations per journey")
    parser.add_argument("--output", type=str, default="monitoring_report.json",
                        help="Output file")
    parser.add_argument("--beacon-endpoint", type=str, default=None,
                        help="RUM beacon collection endpoint")
    parser.add_argument("--sample-count", type=int, default=1000,
                        help="Number of sample RUM beacons for demo mode")
    args = parser.parse_args()

    report = {}

    if args.mode in ("synthetic", "demo"):
        regions = [r.strip() for r in args.regions.split(",")]
        monitor = SyntheticMonitor(regions)

        for _ in range(args.iterations):
            await monitor.run_all_journeys(use_browser=args.browser)

        report["synthetic"] = monitor.generate_report()
        logger.info(f"Synthetic tests complete: {report['synthetic']['pass_count']}/"
                     f"{report['synthetic']['total_journeys']} passed")

    if args.mode in ("rum", "demo"):
        collector = RUMCollector()

        if args.mode == "demo":
            logger.info(f"Generating {args.sample_count} sample RUM beacons...")
            generate_sample_rum_data(collector, args.sample_count)
        else:
            logger.info("RUM collector ready — beacons would be received at beacon endpoint")

        report["rum"] = collector.generate_report()
        logger.info(f"RUM report: {report['rum']['total_beacons']} beacons processed")

        # Print summary
        print("\nRUM Web Vitals Summary:")
        print(f"{'Metric':<20} {'P75':<10} {'P95':<10} {'Good%':<10} {'Poor%':<10}")
        print("-" * 60)
        for metric, data in report["rum"]["global_vitals"].items():
            print(f"{metric:<20} {data['p75']:<10.1f} {data['p95']:<10.1f} "
                  f"{data['good_pct']:<10.1f} {data['poor_pct']:<10.1f}")

    # Save report
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved to {args.output}")

    # Print RUM snippet for reference
    if args.mode == "demo":
        print("\n" + "=" * 60)
        print("RUM Collection Snippet (inject into casino HTML):")
        print("=" * 60)
        print(RUMCollector.RUM_SNIPPET[:500] + "\n... (see source for full snippet)")


if __name__ == "__main__":
    asyncio.run(main())
