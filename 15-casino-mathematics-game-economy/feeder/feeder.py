# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
feeder.py — Progressive jackpot and data feed aggregation service.

Mirrors Feeder.scala + ProgressiveJackpots.scala.

Usage:
    python feeder.py [-env prod|stage] [-upload true|false] [-force true|false] <feed-name>

Architecture:
    1. Load feed configuration (jackpots.conf or equivalent YAML)
    2. Resolve environment (prod vs stage)
    3. Fetch jackpot values from multiple suppliers concurrently
    4. Aggregate per-currency JSON files
    5. Upload to S3/CDN (if upload=True)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass
class JackpotRef:
    game_name: str
    game_title: str
    jackpot_ref: str
    mobile: bool
    restricted: bool


@dataclass
class Jackpot:
    jackpot_ref: JackpotRef
    amount: Decimal
    supplier: int
    jurisdiction: str | None = None


@dataclass
class JackpotFeedGame:
    name: str
    value: Decimal
    is_mobile: bool
    is_restricted: bool


@dataclass
class JackpotFeedPerCurrency:
    fetch_time: str
    total: Decimal
    jackpots: dict[str, dict]   # game_name -> JackpotFeedGame dict


@dataclass
class FeedContext:
    config: dict
    production: bool
    upload: bool
    force: bool
    feed_dir: Path
    http_client: httpx.AsyncClient
    cache: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ProgressiveFeed base class (mirrors the Scala trait)
# ---------------------------------------------------------------------------

class ProgressiveFeed:
    """Base class for per-supplier jackpot fetchers."""

    async def fetch_jackpots(self, feed_ctx: FeedContext, currency: str, rate: float, jackpot_refs: list[JackpotRef]) -> list[Jackpot]:
        raise NotImplementedError

    def is_currency_supported(self, config: dict, currency: str) -> bool:
        supported = config.get("supported-currencies", [])
        return currency in supported


# ---------------------------------------------------------------------------
# Multi-supplier progressive jackpot aggregation
# ---------------------------------------------------------------------------

class ProgressiveJackpots:
    """
    Fetches jackpots from all configured suppliers in parallel for each currency,
    then writes per-currency JSON files.

    Mirrors ProgressiveJackpots.scala.
    """

    MAX_WAIT_SECONDS = 588  # 9.8 minutes — same upper bound as the Scala version

    async def fetch(self, context: FeedContext) -> None:
        log.info("ProgressiveJackpots::fetch starting")
        start = datetime.now(timezone.utc)

        # Load exchange rates from config (in production these come from DB)
        rates: dict[str, float] = context.config.get("exchange-rates", {"GBP": 1.0, "EUR": 1.17, "USD": 1.27})
        suppliers_config: list[dict] = context.config.get("suppliers", [])

        # Process all currencies concurrently (mirrors ioBoundCurrenciesPool)
        async with asyncio.TaskGroup() as tg:
            currency_tasks = {
                currency: tg.create_task(
                    self._process_currency(context, currency, rate, suppliers_config)
                )
                for currency, rate in rates.items()
            }

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        log.info("ProgressiveJackpots::fetch complete", elapsed_seconds=round(elapsed, 2))

    async def _process_currency(
        self,
        context: FeedContext,
        currency: str,
        rate: float,
        suppliers_config: list[dict],
    ) -> None:
        jackpots = await self._fetch_all_suppliers(context, currency, rate, suppliers_config)
        self._write_json_files(context, currency, jackpots)

    async def _fetch_all_suppliers(
        self,
        context: FeedContext,
        currency: str,
        rate: float,
        suppliers_config: list[dict],
    ) -> list[Jackpot]:
        """Fetch jackpots from every supplier concurrently for a given currency."""
        all_jackpots: list[Jackpot] = []
        async with asyncio.TaskGroup() as tg:
            supplier_tasks = [
                tg.create_task(
                    self._fetch_single_supplier(context, sup_cfg, currency, rate)
                )
                for sup_cfg in suppliers_config
            ]
        for t in supplier_tasks:
            try:
                all_jackpots.extend(t.result())
            except Exception as exc:
                if not context.production:
                    log.error("supplier fetch error", exc=str(exc))
                else:
                    raise
        return all_jackpots

    async def _fetch_single_supplier(
        self,
        context: FeedContext,
        sup_cfg: dict,
        currency: str,
        rate: float,
    ) -> list[Jackpot]:
        supplier_name = sup_cfg.get("name", "unknown")
        feed_class_path = sup_cfg.get("feed-class", "")
        supplier_id = sup_cfg.get("id", 0)

        log.info("fetching from supplier", supplier=supplier_name, currency=currency)

        # Dynamic supplier loading (mirrors Class.forName in Scala)
        feed: ProgressiveFeed = self._load_feed_class(feed_class_path)
        if not feed.is_currency_supported(sup_cfg, currency):
            return []

        # Stub jackpot refs — in production loaded from game_jackpot table
        jackpot_refs: list[JackpotRef] = []
        jackpots = await feed.fetch_jackpots(context, currency, rate, jackpot_refs)
        log.info("supplier fetch complete", supplier=supplier_name, count=len(jackpots))
        return jackpots

    def _write_json_files(self, context: FeedContext, currency: str, jackpots: list[Jackpot]) -> None:
        """Write per-currency (and per-jurisdiction) JSON files."""
        with_jur = [j for j in jackpots if j.jurisdiction is not None]
        without_jur = [j for j in jackpots if j.jurisdiction is None]
        suppliers_with_jur = {j.supplier for j in with_jur}
        base_jackpots = [j for j in without_jur if j.supplier not in suppliers_with_jur]

        self._generate_json_file(f"{currency}.json", without_jur, context)

        from itertools import groupby
        for jur, group in groupby(sorted(with_jur, key=lambda j: j.jurisdiction), key=lambda j: j.jurisdiction):
            combined = list(group) + base_jackpots
            self._generate_json_file(f"{currency}-{jur}.json", combined, context)

    def _generate_json_file(self, filename: str, jackpots: list[Jackpot], context: FeedContext) -> None:
        # Deduplicate by jackpot_ref, then sum totals
        seen: dict[str, Jackpot] = {}
        for j in jackpots:
            if j.jackpot_ref.jackpot_ref not in seen:
                seen[j.jackpot_ref.jackpot_ref] = j
        total = sum(j.amount for j in seen.values())

        games = {
            j.jackpot_ref.game_name: {
                "name": j.jackpot_ref.game_title,
                "value": float(j.amount),
                "isMobile": j.jackpot_ref.mobile,
                "isRestricted": j.jackpot_ref.restricted,
            }
            for j in seen.values()
        }

        payload = {
            "_fetchTime": datetime.now(timezone.utc).isoformat(),
            "total": float(total),
            "jackpots": games,
        }

        out_path = context.feed_dir / filename
        out_path.write_text(json.dumps(payload, indent=2))
        log.info("wrote jackpot file", file=str(out_path), games=len(games))

    @staticmethod
    def _load_feed_class(class_path: str) -> ProgressiveFeed:
        """Stub dynamic class loader — extend with importlib for real suppliers."""
        return ProgressiveFeed()


# ---------------------------------------------------------------------------
# CLI entry point (mirrors Feeder.scala object)
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feeder — data feed aggregation service")
    parser.add_argument("-env", choices=["prod", "stage"], default="stage")
    parser.add_argument("-upload", type=lambda v: v.lower() in ("1", "true", "yes", "y"), default=True)
    parser.add_argument("-force", type=lambda v: v.lower() in ("1", "true", "yes", "y"), default=False)
    parser.add_argument("feed_name")
    return parser.parse_args()


async def main() -> None:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    args = parse_args()

    feed_dir = Path(args.feed_name)
    feed_dir.mkdir(exist_ok=True)

    config_file = Path(f"{args.feed_name}.yaml")
    config: dict = {}
    if config_file.exists():
        with config_file.open() as f:
            config = yaml.safe_load(f) or {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        ctx = FeedContext(
            config=config,
            production=(args.env == "prod"),
            upload=args.upload,
            force=args.force,
            feed_dir=feed_dir,
            http_client=client,
        )

        feed_class_name = config.get("feed-class", "ProgressiveJackpots")
        if feed_class_name == "ProgressiveJackpots":
            feed_impl = ProgressiveJackpots()
            await feed_impl.fetch(ctx)
        else:
            log.error("unknown feed class", feed_class=feed_class_name)
            sys.exit(1)

    log.info("feeder complete")


if __name__ == "__main__":
    asyncio.run(main())
