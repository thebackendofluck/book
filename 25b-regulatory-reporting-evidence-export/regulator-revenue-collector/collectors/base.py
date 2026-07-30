# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
StateCollector base class.

Each state-regulator integration subclasses StateCollector and implements
list_reports(). The base class handles streaming download with retries,
SHA-256 hashing, on-disk caching, and JSON snapshot output.

The streaming download follows the httpx documented pattern (response.stream
+ aiter_bytes) so we never load a multi-megabyte Excel/PDF into memory:
https://www.python-httpx.org/quickstart/#streaming-responses
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx
import structlog

from models import ReportFile, StateSnapshot

log = structlog.get_logger()


def _is_cert_error(exc: BaseException) -> bool:
    """True only for a TLS *certificate* failure (expired / invalid CA), not a
    generic connection error. Used to gate the insecure download fallback so we
    drop verification ONLY when a regulator's cert is broken — never on a normal
    network blip. Walks the exception cause chain (httpx wraps ssl errors)."""
    cur: BaseException | None = exc
    for _ in range(6):
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        if isinstance(cur, ssl.SSLError) and "CERTIFICATE" in str(cur).upper():
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        if cur is None:
            break
    msg = str(exc).upper()
    return "CERTIFICATE_VERIFY_FAILED" in msg or "CERTIFICATE HAS EXPIRED" in msg


# Most US gaming-regulator sites are CDN-fronted but rate-limit aggressively.
# A modest concurrency + per-request retry envelope balances throughput
# against being a polite citizen.
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
DEFAULT_CONCURRENCY = 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = (
    "regulator-revenue-collector/1.0 (+https://thebackendoftheluck.com/regulator-reports; "
    "weekly public-data archive)"
)


class StateCollector:
    state: str          # e.g. "NY"
    regulator: str      # e.g. "NY State Gaming Commission"
    source_url: str     # landing page

    def __init__(self, output_root: Path, *, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.output_root = output_root
        self.semaphore = asyncio.Semaphore(concurrency)
        self._insecure_client: httpx.AsyncClient | None = None

    def _get_insecure_client(self) -> httpx.AsyncClient:
        """Lazily-built client with TLS verification DISABLED. Only ever used as
        a fallback after the verifying client hit a certificate error — see
        fetch() / _download_one. Created on demand so a collector that never
        encounters a broken cert never opens an insecure connection."""
        if self._insecure_client is None:
            self._insecure_client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
                verify=False,  # noqa: S501 — gated by _is_cert_error; see docstring
            )
        return self._insecure_client

    async def fetch(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """Request `url` with the verifying client first; if (and only if) it
        fails with an expired/invalid TLS certificate, retry once over an
        unverified connection and log a warning. Use this in list_reports() for
        regulators whose certs sometimes lapse (e.g. iGaming Ontario)."""
        try:
            return await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            if not _is_cert_error(exc):
                raise
            log.warning(
                "tls cert invalid — retrying without verification",
                state=self.state, url=url, err=str(exc),
            )
            return await self._get_insecure_client().request(method, url, **kwargs)

    # ------------------------------------------------------------------
    # Internet Archive (Wayback Machine) fallback. Some regulators front
    # their site with Cloudflare/WAF that blocks any non-browser client
    # (gaming.az.gov, tn.gov). The Wayback Machine archives the same files
    # and serves them WITHOUT that protection, so we discover via the CDX
    # API and download the raw bytes via the `id_` snapshot URL.
    # ------------------------------------------------------------------
    async def wayback_snapshots(
        self,
        client: httpx.AsyncClient,
        url_pattern: str,
        *,
        filter_regex: str | None = None,
        limit: int = 400,
    ) -> list[tuple[str, str]]:
        """List archived (original_url, timestamp) pairs, newest first, from the
        IA CDX API. `url_pattern` may end with `*` for a prefix match;
        `filter_regex` is applied to the original URL (CDX `filter=original:`)."""
        params = {
            "url": url_pattern,
            "output": "json",
            "fl": "original,timestamp",
            "collapse": "urlkey",
            "limit": str(limit),
        }
        if filter_regex:
            params["filter"] = "original:" + filter_regex
        try:
            r = await client.get(
                "http://web.archive.org/cdx/search/cdx", params=params, timeout=45
            )
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        # rows[0] is the header ["original","timestamp"]
        pairs = [(row[0], row[1]) for row in rows[1:] if len(row) >= 2]
        pairs.sort(key=lambda t: t[1], reverse=True)
        return pairs

    @staticmethod
    def wayback_raw_url(original_url: str, timestamp: str) -> str:
        """IA raw-bytes URL: the `id_` suffix returns the ORIGINAL file (not the
        Wayback HTML wrapper). Use as ReportFile.source_url for blocked sources."""
        return f"https://web.archive.org/web/{timestamp}id_/{original_url}"

    # Subclasses MUST override.
    async def list_reports(self, client: httpx.AsyncClient) -> Iterable[ReportFile]:
        raise NotImplementedError

    # Optional override: parse the downloaded file into summary rows.
    async def parse(self, report: ReportFile, file_path: Path) -> list:
        return []

    async def collect(self) -> StateSnapshot:
        cache_dir = self.output_root / self.state.lower()
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                reports = list(await self.list_reports(client))
                tasks = [self._download_one(client, r, cache_dir) for r in reports]
                await asyncio.gather(*tasks, return_exceptions=False)
        finally:
            if self._insecure_client is not None:
                await self._insecure_client.aclose()
                self._insecure_client = None

        return StateSnapshot(
            state=self.state,
            regulator=self.regulator,
            source_url=self.source_url,
            collected_at=datetime.now(timezone.utc),
            reports=reports,
        )

    async def _download_one(
        self, client: httpx.AsyncClient, report: ReportFile, cache_dir: Path
    ) -> None:
        async with self.semaphore:
            dl_client = client
            for attempt in range(3):
                try:
                    sha = hashlib.sha256()
                    size = 0
                    target = cache_dir / self._safe_name(report)
                    async with dl_client.stream("GET", report.source_url) as resp:
                        if resp.status_code in RETRYABLE_STATUS:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        resp.raise_for_status()
                        with target.open("wb") as fp:
                            async for chunk in resp.aiter_bytes(64 * 1024):
                                sha.update(chunk)
                                size += len(chunk)
                                fp.write(chunk)
                    report.local_path = str(target.relative_to(self.output_root))
                    report.sha256 = sha.hexdigest()
                    report.size_bytes = size
                    report.retrieved_at = datetime.now(timezone.utc)
                    # Best-effort parse; never let parse failures fail the whole collection.
                    try:
                        report.summary = await self.parse(report, target)
                    except Exception as exc:  # noqa: BLE001 — parser is opt-in
                        log.warning(
                            "parse failed",
                            state=self.state, op=report.operator, err=str(exc),
                        )
                    return
                except (httpx.HTTPError, OSError) as exc:
                    # On a broken regulator TLS cert, switch this download to the
                    # unverified fallback client for the remaining attempts.
                    if _is_cert_error(exc) and dl_client is client:
                        log.warning(
                            "tls cert invalid — retrying download without verification",
                            state=self.state, op=report.operator, url=report.source_url,
                        )
                        dl_client = self._get_insecure_client()
                    log.warning(
                        "download failed",
                        state=self.state, op=report.operator, attempt=attempt, err=str(exc),
                    )
                    await asyncio.sleep(2 ** attempt)
            report.fetch_error = "download failed after 3 attempts"

    def _safe_name(self, report: ReportFile) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in report.operator).strip("-").lower()
        # Discriminate by a short hash of the source URL so multiple monthly
        # reports sharing the same operator/cadence/format (e.g. statewide
        # AZ/TN reports, one PDF per month) don't all collide onto a single
        # cached filename and overwrite each other — which silently left only
        # the last month on disk for the parser.
        disc = hashlib.sha1(report.source_url.encode("utf-8")).hexdigest()[:10]
        return f"{slug}-{report.cadence}-{disc}.{report.format}"
