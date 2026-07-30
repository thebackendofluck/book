# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Treasury service examples for Chapter 12.

This module collects the progressive jackpot aggregation and FX-rate rollover
patterns referenced from the chapter text. It keeps the external dependencies
abstract so the examples can be wired to a real HTTP client or database adapter
without changing the orchestration logic.
"""

from __future__ import annotations

from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import os
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class Jackpot:
    jackpot_ref: str
    amount: Decimal
    currency: str


class JackpotFeed(Protocol):
    def fetch_jackpots(self, currency: str, rate: float) -> list[Jackpot]:
        ...


@dataclass(frozen=True)
class SupplierConfig:
    name: str
    feed_class: Callable[[], JackpotFeed] | JackpotFeed


class ProgressiveJackpotFeed:
    """Fan out jackpot aggregation by currency and supplier."""

    def __init__(self) -> None:
        workers = (os.cpu_count() or 4) * 3
        self._currency_pool = ThreadPoolExecutor(max_workers=workers)
        self._supplier_pool = ThreadPoolExecutor(max_workers=workers)
        self._max_wait = 9.8 * 60
        self.latest_feed: dict[str, Any] = {}

    def fetch(self, rates: dict[str, float], suppliers: list[SupplierConfig]) -> None:
        currency_futures = [
            self._currency_pool.submit(
                self._fetch_for_currency, currency, rate, suppliers
            )
            for currency, rate in rates.items()
        ]

        _, not_done = wait(
            currency_futures,
            timeout=self._max_wait,
            return_when=FIRST_EXCEPTION,
        )
        for future in not_done:
            future.cancel()

    def _fetch_for_currency(
        self, currency: str, rate: float, suppliers: list[SupplierConfig]
    ) -> None:
        supplier_futures = [
            self._supplier_pool.submit(
                self._fetch_from_supplier, supplier, currency, rate
            )
            for supplier in suppliers
        ]

        jackpots: list[Jackpot] = []
        for future in supplier_futures:
            try:
                jackpots.extend(future.result(timeout=self._max_wait))
            except Exception:
                continue

        by_ref: dict[str, Jackpot] = {}
        for jackpot in jackpots:
            by_ref.setdefault(jackpot.jackpot_ref, jackpot)
        total = sum((jp.amount for jp in by_ref.values()), Decimal("0"))

        self._write_json_feed(currency, total, list(by_ref.values()))

    def _fetch_from_supplier(
        self, supplier: SupplierConfig, currency: str, rate: float
    ) -> list[Jackpot]:
        feed = self._instantiate_feed(supplier.feed_class)
        return feed.fetch_jackpots(currency, rate)

    def _instantiate_feed(
        self, feed_class: Callable[[], JackpotFeed] | JackpotFeed
    ) -> JackpotFeed:
        if callable(feed_class):
            return feed_class()
        return feed_class

    def _write_json_feed(
        self, currency: str, total: Decimal, jackpots: list[Jackpot]
    ) -> None:
        self.latest_feed[currency] = {
            "total": str(total),
            "jackpots": [
                {
                    "jackpot_ref": jackpot.jackpot_ref,
                    "amount": str(jackpot.amount),
                    "currency": jackpot.currency,
                }
                for jackpot in jackpots
            ],
        }


@dataclass(frozen=True)
class FxRate:
    currency: str
    value: Decimal


class FxRateService:
    """Daily FX upsert pattern with month-rollover prepopulation."""

    def __init__(
        self,
        db: Any,
        provider: Callable[[], list[FxRate]] | None = None,
    ) -> None:
        self._db = db
        self._provider = provider

    def fetch_and_store(self) -> None:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        next_month = self._month_for(today + timedelta(days=7))
        this_month = self._month_for(today)

        if next_month != this_month and not self._has_month_data(next_month):
            self._copy_month_data(from_month=this_month, to_month=next_month)

        rates = self._fetch_rates_from_provider()
        self._insert_daily_rates(tomorrow, rates)

        if self._month_for(tomorrow) != this_month:
            self._insert_monthly_rates(self._month_for(tomorrow), rates)

    def _insert_daily_rates(self, rate_date: date, rates: list[FxRate]) -> None:
        with self._db.transaction() as cur:
            for rate in rates:
                cur.execute(
                    "DELETE FROM daily_currencies WHERE date = %s AND currency = %s",
                    (rate_date, rate.currency),
                )
                cur.execute(
                    "INSERT INTO daily_currencies (date, currency, rate) "
                    "VALUES (%s, %s, %s)",
                    (rate_date, rate.currency, rate.value),
                )
                cur.execute(
                    "INSERT INTO currencies (currency, rate, is_manual) "
                    "VALUES (%s, %s, false) "
                    "ON CONFLICT (currency) DO UPDATE SET rate = EXCLUDED.rate",
                    (rate.currency, rate.value),
                )

    def _insert_monthly_rates(self, month: date, rates: list[FxRate]) -> None:
        with self._db.transaction() as cur:
            for rate in rates:
                cur.execute(
                    "DELETE FROM monthly_currencies WHERE month = %s AND currency = %s",
                    (month, rate.currency),
                )
                cur.execute(
                    "INSERT INTO monthly_currencies (month, currency, rate) "
                    "VALUES (%s, %s, %s)",
                    (month, rate.currency, rate.value),
                )

    @staticmethod
    def _month_for(day: date) -> date:
        return day.replace(day=1)

    def _has_month_data(self, month: date) -> bool:
        if hasattr(self._db, "has_month_data"):
            return bool(self._db.has_month_data(month))
        return False

    def _copy_month_data(self, from_month: date, to_month: date) -> None:
        with self._db.transaction() as cur:
            cur.execute(
                "INSERT INTO monthly_currencies (month, currency, rate) "
                "SELECT %s, currency, rate FROM monthly_currencies WHERE month = %s "
                "ON CONFLICT (month, currency) DO NOTHING",
                (to_month, from_month),
            )

    def _fetch_rates_from_provider(self) -> list[FxRate]:
        if self._provider is None:
            return []
        return self._provider()


__all__ = [
    "FxRate",
    "FxRateService",
    "Jackpot",
    "ProgressiveJackpotFeed",
    "SupplierConfig",
]
