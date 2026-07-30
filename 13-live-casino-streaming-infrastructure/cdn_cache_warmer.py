#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
cdn_cache_warmer.py — CDN Edge Cache Pre-Warming Agent

Pre-populates CDN edge caches before live casino events to prevent
cold-start cache misses that cause visible stutters on stream start.

For each scheduled table start:
  1. Fetches the HLS master manifest from origin
  2. Parses variant stream URLs for each quality level
  3. Fetches the first N segments of each variant from each CDN PoP
  4. Reports cache warm status and timing

CDN edge PoP selection: driven by target jurisdiction and player geolocation
data from the analytics platform.

Chapter 13 — Live Casino Streaming Infrastructure
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
import os
import sys

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cdn_cache_warmer")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class CdnEndpoint:
    provider: str
    url: str
    regions: list[str]
    edge_pop_urls: list[str] = field(default_factory=list)


@dataclass
class WarmingTarget:
    table_id: str
    origin_url: str
    quality_levels: list[str]  # e.g. ["720p", "1080p", "4k"]
    segments_to_warm: int = 5
    jurisdictions: list[str] = field(default_factory=list)


@dataclass
class WarmingResult:
    table_id: str
    cdn_provider: str
    region: str
    quality: str
    segments_warmed: int
    total_bytes: int
    elapsed_ms: float
    success: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# CDN configuration
# ---------------------------------------------------------------------------

CDN_ENDPOINTS: list[CdnEndpoint] = [
    CdnEndpoint(
        provider="cloudflare",
        url=os.getenv("CDN_PRIMARY_URL", "https://stream.acmetocasino.com"),
        regions=["EU", "US", "APAC"],
        edge_pop_urls=[
            os.getenv("CDN_CF_EU", "https://eu.stream.acmetocasino.com"),
            os.getenv("CDN_CF_US", "https://us.stream.acmetocasino.com"),
            os.getenv("CDN_CF_APAC", "https://apac.stream.acmetocasino.com"),
        ],
    ),
    CdnEndpoint(
        provider="fastly",
        url=os.getenv("CDN_SECONDARY_URL", "https://stream2.acmetocasino.com"),
        regions=["EU", "US"],
        edge_pop_urls=[
            os.getenv("CDN_FASTLY_EU", "https://eu.stream2.acmetocasino.com"),
            os.getenv("CDN_FASTLY_US", "https://us.stream2.acmetocasino.com"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Cache warmer
# ---------------------------------------------------------------------------

class CdnCacheWarmer:
    def __init__(
        self,
        cdns: list[CdnEndpoint],
        concurrency: int = 10,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.cdns = cdns
        self.concurrency = concurrency
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def warm_table(self, target: WarmingTarget) -> list[WarmingResult]:
        """Warm all CDN edge PoPs for a table across all quality levels."""
        logger.info(
            "Starting cache warming for table %s (%d quality levels, %d CDNs)",
            target.table_id,
            len(target.quality_levels),
            len(self.cdns),
        )

        tasks = []
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            for cdn in self.cdns:
                for pop_url in cdn.edge_pop_urls or [cdn.url]:
                    for quality in target.quality_levels:
                        tasks.append(
                            self._warm_variant(session, cdn, pop_url, target, quality)
                        )

            results = await asyncio.gather(*tasks, return_exceptions=True)

        warming_results: list[WarmingResult] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Warming task failed with exception: %s", r)
            elif isinstance(r, WarmingResult):
                warming_results.append(r)
                if r.success:
                    logger.info(
                        "[OK] %s / %s / %s: %d segments, %d bytes in %.0f ms",
                        r.cdn_provider,
                        r.region,
                        r.quality,
                        r.segments_warmed,
                        r.total_bytes,
                        r.elapsed_ms,
                    )
                else:
                    logger.warning(
                        "[FAIL] %s / %s / %s: %s",
                        r.cdn_provider,
                        r.region,
                        r.quality,
                        r.error,
                    )

        return warming_results

    async def _warm_variant(
        self,
        session: aiohttp.ClientSession,
        cdn: CdnEndpoint,
        pop_url: str,
        target: WarmingTarget,
        quality: str,
    ) -> WarmingResult:
        async with self._semaphore:
            start = time.monotonic()
            region = self._infer_region(pop_url)
            result = WarmingResult(
                table_id=target.table_id,
                cdn_provider=cdn.provider,
                region=region,
                quality=quality,
                segments_warmed=0,
                total_bytes=0,
                elapsed_ms=0,
                success=False,
            )

            try:
                # Step 1: Fetch variant playlist
                playlist_url = (
                    f"{pop_url}/live/{target.table_id}/{quality}/index.m3u8"
                )
                segment_urls = await self._fetch_playlist(session, playlist_url)

                if not segment_urls:
                    result.error = "Empty or unparseable playlist"
                    return result

                # Step 2: Fetch first N segments
                to_warm = segment_urls[: target.segments_to_warm]
                for seg_url in to_warm:
                    full_url = (
                        seg_url if seg_url.startswith("http")
                        else f"{pop_url}{seg_url}"
                    )
                    bytes_fetched = await self._fetch_segment(session, full_url)
                    result.total_bytes += bytes_fetched
                    result.segments_warmed += 1

                result.success = True
            except aiohttp.ClientError as exc:
                result.error = f"HTTP error: {exc}"
            except asyncio.TimeoutError:
                result.error = "Timeout"
            except Exception as exc:  # noqa: BLE001
                result.error = str(exc)
            finally:
                result.elapsed_ms = (time.monotonic() - start) * 1000

            return result

    async def _fetch_playlist(
        self, session: aiohttp.ClientSession, url: str
    ) -> list[str]:
        """Fetch an HLS playlist and extract segment URLs."""
        async with session.get(url) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
            text = await resp.text()

        segment_urls = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        return segment_urls

    async def _fetch_segment(
        self, session: aiohttp.ClientSession, url: str
    ) -> int:
        """Fetch a segment and return its size in bytes."""
        async with session.get(url) as resp:
            if resp.status not in (200, 206):
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
            data = await resp.read()
            return len(data)

    @staticmethod
    def _infer_region(pop_url: str) -> str:
        if "eu." in pop_url or "-eu-" in pop_url:
            return "EU"
        if "us." in pop_url or "-us-" in pop_url:
            return "US"
        if "apac." in pop_url or "-apac-" in pop_url:
            return "APAC"
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Warm CDN caches for all scheduled tables."""
    targets = [
        WarmingTarget(
            table_id="blackjack-vip-01",
            origin_url="https://origin.acmetocasino.com",
            quality_levels=["720p", "1080p", "4k"],
            segments_to_warm=5,
            jurisdictions=["UK", "MT"],
        ),
        WarmingTarget(
            table_id="roulette-live-03",
            origin_url="https://origin.acmetocasino.com",
            quality_levels=["720p", "1080p"],
            segments_to_warm=5,
            jurisdictions=["UK", "SE", "DK"],
        ),
    ]

    warmer = CdnCacheWarmer(cdns=CDN_ENDPOINTS, concurrency=10)
    all_results: list[WarmingResult] = []

    for target in targets:
        results = await warmer.warm_table(target)
        all_results.extend(results)

    # Summary
    total = len(all_results)
    succeeded = sum(1 for r in all_results if r.success)
    failed = total - succeeded
    total_bytes = sum(r.total_bytes for r in all_results if r.success)

    logger.info(
        "Cache warming complete: %d/%d succeeded, %.1f MB warmed",
        succeeded,
        total,
        total_bytes / 1_048_576,
    )

    if failed > 0:
        logger.warning("%d warming tasks failed", failed)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
