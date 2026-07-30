#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
Real-Time Exchange Rate Aggregation

Multi-oracle exchange rate feed for crypto casino operations:
- Aggregates prices from multiple sources (Chainlink, CoinGecko, Binance, Kraken)
- Median/VWAP price calculation to resist manipulation
- Staleness detection and fallback oracle chain
- Rate caching with configurable TTL
- Spread calculation for deposit/withdrawal pricing
- Historical rate storage for tax/audit compliance
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class OracleSource(Enum):
    CHAINLINK = "chainlink"
    COINGECKO = "coingecko"
    BINANCE = "binance"
    KRAKEN = "kraken"
    COINBASE = "coinbase"
    INTERNAL = "internal_fallback"

@dataclass
class PricePoint:
    source: OracleSource
    pair: str
    price: float
    timestamp: float
    volume_24h: float = 0
    is_stale: bool = False

@dataclass
class AggregatedRate:
    pair: str
    median_price: float
    vwap_price: float
    spread_pct: float
    sources_used: int
    deposit_rate: float   # Rate offered for deposits (slightly above mid)
    withdrawal_rate: float  # Rate offered for withdrawals (slightly below mid)
    timestamp: str
    confidence: str  # high (3+ sources), medium (2), low (1)

class ExchangeRateFeed:
    """Multi-oracle exchange rate aggregator for crypto casino pricing."""

    CASINO_SPREAD_BPS = 50  # 0.5% spread on each side
    STALE_THRESHOLD_SECONDS = 300  # 5 minutes

    # Simulated price data for demo
    SIMULATED_PRICES = {
        "ETH/USD": {OracleSource.CHAINLINK: 2015.50, OracleSource.BINANCE: 2016.20,
                     OracleSource.KRAKEN: 2014.80, OracleSource.COINGECKO: 2015.90},
        "BTC/USD": {OracleSource.CHAINLINK: 42150.00, OracleSource.BINANCE: 42180.50,
                     OracleSource.KRAKEN: 42130.00, OracleSource.COINGECKO: 42160.25},
        "MATIC/USD": {OracleSource.COINGECKO: 0.92, OracleSource.BINANCE: 0.921},
        "USDT/USD": {OracleSource.CHAINLINK: 1.0001, OracleSource.BINANCE: 0.9999},
        "USDC/USD": {OracleSource.CHAINLINK: 1.0000, OracleSource.BINANCE: 1.0001},
    }

    def __init__(self, spread_bps: int = 50):
        self.spread_bps = spread_bps
        self.cache: dict[str, AggregatedRate] = {}
        self.history: list[AggregatedRate] = []

    def fetch_prices(self, pair: str) -> list[PricePoint]:
        """Fetch prices from all available oracles (simulated)."""
        prices = []
        now = time.time()
        simulated = self.SIMULATED_PRICES.get(pair, {})
        for source, price in simulated.items():
            # Add small random variance for realism
            import random
            variance = price * random.uniform(-0.001, 0.001)
            prices.append(PricePoint(
                source=source, pair=pair, price=price + variance,
                timestamp=now, volume_24h=random.uniform(1e6, 1e9),
            ))
        return prices

    def aggregate(self, pair: str) -> AggregatedRate:
        """Aggregate prices from multiple oracles into a single rate."""
        points = self.fetch_prices(pair)
        if not points:
            raise ValueError(f"No price data for {pair}")

        # Filter stale
        now = time.time()
        fresh = [p for p in points if now - p.timestamp < self.STALE_THRESHOLD_SECONDS]
        if not fresh:
            fresh = points  # Use stale if nothing else
            logger.warning(f"All sources stale for {pair}")

        prices = sorted([p.price for p in fresh])
        median = prices[len(prices) // 2]

        # VWAP
        total_vol = sum(p.volume_24h for p in fresh)
        vwap = sum(p.price * p.volume_24h for p in fresh) / total_vol if total_vol > 0 else median

        spread = self.spread_bps / 10_000
        rate = AggregatedRate(
            pair=pair, median_price=round(median, 6), vwap_price=round(vwap, 6),
            spread_pct=round(spread * 100, 3), sources_used=len(fresh),
            deposit_rate=round(median * (1 + spread), 6),
            withdrawal_rate=round(median * (1 - spread), 6),
            timestamp=datetime.now(timezone.utc).isoformat(),
            confidence="high" if len(fresh) >= 3 else "medium" if len(fresh) >= 2 else "low",
        )
        self.cache[pair] = rate
        self.history.append(rate)
        return rate

    def convert(self, amount: float, from_currency: str, to_currency: str = "USD",
                direction: str = "deposit") -> dict:
        pair = f"{from_currency}/{to_currency}"
        rate = self.cache.get(pair) or self.aggregate(pair)
        price = rate.deposit_rate if direction == "deposit" else rate.withdrawal_rate
        converted = amount * price
        return {
            "from": f"{amount} {from_currency}",
            "to": f"{round(converted, 2)} {to_currency}",
            "rate_used": price,
            "direction": direction,
            "confidence": rate.confidence,
        }

if __name__ == "__main__":
    feed = ExchangeRateFeed(spread_bps=50)
    print("=" * 60)
    print("EXCHANGE RATE FEED - Crypto Casino")
    print("=" * 60)

    for pair in ["ETH/USD", "BTC/USD", "MATIC/USD", "USDT/USD"]:
        rate = feed.aggregate(pair)
        print(f"\n  {pair}:")
        print(f"    Median: ${rate.median_price:,.4f} | VWAP: ${rate.vwap_price:,.4f}")
        print(f"    Deposit rate: ${rate.deposit_rate:,.4f} | Withdrawal: ${rate.withdrawal_rate:,.4f}")
        print(f"    Sources: {rate.sources_used} | Confidence: {rate.confidence}")

    print("\n  Conversions:")
    for amt, curr in [(1.0, "ETH"), (0.5, "BTC"), (10000, "MATIC")]:
        result = feed.convert(amt, curr, direction="deposit")
        print(f"    {result['from']:>15} -> {result['to']} ({result['direction']})")
