# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.observability — Metrics Collection
===============================================

Provides a ``MetricsCollector`` abstract base class plus two concrete
implementations:

* :class:`InMemoryMetrics` — stores counts and observations in plain Python
  dicts; designed for unit tests and local dev.
* :class:`PrometheusMetrics` — wraps ``prometheus_client`` with
  ``acmetocasino_game_*`` metric names for production use.

Design principles
-----------------
* **Name prefix** — All Prometheus metric names start with
  ``acmetocasino_game_`` to prevent collisions on shared Prometheus servers.
* **Consistent labels** — Every call site receives the same label set so
  PromQL aggregations work without surprises.
* **Monetary values never exposed as gauges** — Balance gauges are aggregated
  (sum across accounts), not per-player, to comply with data minimisation
  requirements.

Usage (production)
------------------
::

    from acmetocasino.gameservice.observability import PrometheusMetrics

    metrics = PrometheusMetrics()
    metrics.record_launch(supplier="netent", brand="brand-uk",
                          jurisdiction="UKGC", mode="real_money")
    metrics.session_opened(supplier="netent", brand="brand-uk")

Usage (tests)
-------------
::

    from acmetocasino.gameservice.observability import InMemoryMetrics

    metrics = InMemoryMetrics()
    metrics.record_launch(supplier="netent", brand="brand-uk",
                          jurisdiction="UKGC", mode="real_money")
    assert metrics.launches_total == 1
    assert metrics.counter("launches_total", supplier="netent") == 1
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class MetricsCollector(ABC):
    """Abstract interface for all game-service metrics.

    Concrete implementations must override every method.  The signatures are
    intentionally minimal — only the data needed to populate labels is passed
    in; derivations (e.g. RTP, hold percentage) are computed in dashboards.
    """

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    @abstractmethod
    def record_launch(
        self,
        supplier: str,
        brand: str,
        jurisdiction: str,
        mode: str,
    ) -> None:
        """Increment the ``launches_total`` counter.

        Parameters
        ----------
        supplier:
            Supplier identifier (e.g. ``"netent"``).
        brand:
            Operator brand identifier (e.g. ``"brand-uk"``).
        jurisdiction:
            Regulatory jurisdiction code (e.g. ``"UKGC"``).
        mode:
            Session mode — ``"real_money"``, ``"demo"``, or ``"free_round"``.
        """

    @abstractmethod
    def record_transaction(
        self,
        type: str,
        supplier: str,
        succeeded: bool,
        latency_ms: int,
    ) -> None:
        """Increment the ``transactions_total`` counter and record latency.

        Parameters
        ----------
        type:
            Transaction type — ``"debit"``, ``"credit"``, ``"rollback"``,
            or ``"adjust"``.
        supplier:
            Supplier identifier.
        succeeded:
            ``True`` if the wallet accepted the operation.
        latency_ms:
            Round-trip time to the wallet service in milliseconds.
        """

    @abstractmethod
    def record_round(
        self,
        supplier: str,
        brand: str,
        wager: Decimal,
        payout: Decimal,
        duration_ms: int,
    ) -> None:
        """Increment the ``rounds_total`` counter and record round duration.

        Parameters
        ----------
        supplier:
            Supplier identifier.
        brand:
            Operator brand identifier.
        wager:
            Total stake placed (used for aggregate wager tracking).
        payout:
            Total payout returned (used for aggregate RTP tracking).
        duration_ms:
            Time between round start and settlement in milliseconds.
        """

    @abstractmethod
    def record_compliance_violation(self, type: str, jurisdiction: str) -> None:
        """Increment the ``compliance_violations_total`` counter.

        Parameters
        ----------
        type:
            Violation type — e.g. ``"geo_blocked"``, ``"kyc_missing"``,
            ``"limit_exceeded"``, ``"self_excluded"``, ``"reality_check"``.
        jurisdiction:
            Regulatory jurisdiction code.
        """

    @abstractmethod
    def record_supplier_error(self, supplier: str, error_code: str) -> None:
        """Increment the ``supplier_errors_total`` counter.

        Parameters
        ----------
        supplier:
            Supplier identifier.
        error_code:
            Supplier-defined error code (e.g. ``"ROUND_NOT_FOUND"``).
        """

    # ------------------------------------------------------------------
    # Gauges
    # ------------------------------------------------------------------

    @abstractmethod
    def session_opened(self, supplier: str, brand: str) -> None:
        """Increment the ``active_sessions`` gauge by 1.

        Parameters
        ----------
        supplier:
            Supplier identifier.
        brand:
            Operator brand identifier.
        """

    @abstractmethod
    def session_closed(self, supplier: str, brand: str) -> None:
        """Decrement the ``active_sessions`` gauge by 1.

        Parameters
        ----------
        supplier:
            Supplier identifier.
        brand:
            Operator brand identifier.
        """


# ---------------------------------------------------------------------------
# In-memory implementation (tests / local dev)
# ---------------------------------------------------------------------------


def _label_key(**labels: str) -> tuple[tuple[str, str], ...]:
    """Return a hashable, sorted label key for use in dicts."""
    return tuple(sorted(labels.items()))


class InMemoryMetrics(MetricsCollector):
    """In-memory metrics store for unit tests and local development.

    All data is stored in plain Python ``defaultdict`` instances.  No
    external dependencies are required.

    Attributes
    ----------
    launches_total:
        Total number of :meth:`record_launch` calls (label-agnostic sum).
    transactions_total:
        Total number of :meth:`record_transaction` calls.
    rounds_total:
        Total number of :meth:`record_round` calls.
    compliance_violations_total:
        Total number of :meth:`record_compliance_violation` calls.
    supplier_errors_total:
        Total number of :meth:`record_supplier_error` calls.
    """

    def __init__(self) -> None:
        # Raw totals (sum across all labels)
        self.launches_total: int = 0
        self.transactions_total: int = 0
        self.rounds_total: int = 0
        self.compliance_violations_total: int = 0
        self.supplier_errors_total: int = 0

        # Labelled counters: tuple[label_tuple] → int
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(
            lambda: defaultdict(int)
        )

        # Labelled histograms (stored as raw observation lists)
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], list[float]]] = (
            defaultdict(lambda: defaultdict(list))
        )

        # Labelled gauges: tuple[label_tuple] → float
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )

    # ------------------------------------------------------------------
    # Generic helpers for test assertions
    # ------------------------------------------------------------------

    def counter(self, name: str, **labels: str) -> int:
        """Return the count for a named counter with specific labels.

        Parameters
        ----------
        name:
            Counter name (e.g. ``"launches_total"``).
        **labels:
            Label key/value pairs to match exactly.

        Returns
        -------
        int
            Accumulated count for the given label set.
        """
        key = _label_key(**labels)
        return self._counters[name][key]

    def histogram_observations(self, name: str, **labels: str) -> list[float]:
        """Return raw observations for a named histogram with specific labels.

        Parameters
        ----------
        name:
            Histogram name (e.g. ``"transaction_latency_seconds"``).
        **labels:
            Label key/value pairs to match exactly.

        Returns
        -------
        list[float]
            All observed values in insertion order.
        """
        key = _label_key(**labels)
        return list(self._histograms[name][key])

    def gauge(self, name: str, **labels: str) -> float:
        """Return the current value of a named gauge with specific labels.

        Parameters
        ----------
        name:
            Gauge name (e.g. ``"active_sessions"``).
        **labels:
            Label key/value pairs to match exactly.

        Returns
        -------
        float
            Current gauge value.
        """
        key = _label_key(**labels)
        return self._gauges[name][key]

    def reset(self) -> None:
        """Reset all metrics to zero.  Useful between test cases."""
        self.launches_total = 0
        self.transactions_total = 0
        self.rounds_total = 0
        self.compliance_violations_total = 0
        self.supplier_errors_total = 0
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()

    # ------------------------------------------------------------------
    # MetricsCollector implementation
    # ------------------------------------------------------------------

    def record_launch(
        self,
        supplier: str,
        brand: str,
        jurisdiction: str,
        mode: str,
    ) -> None:
        self.launches_total += 1
        key = _label_key(supplier=supplier, brand=brand, jurisdiction=jurisdiction, mode=mode)
        self._counters["launches_total"][key] += 1

    def record_transaction(
        self,
        type: str,
        supplier: str,
        succeeded: bool,
        latency_ms: int,
    ) -> None:
        self.transactions_total += 1
        status = "ok" if succeeded else "error"
        key = _label_key(type=type, supplier=supplier, status=status)
        self._counters["transactions_total"][key] += 1

        hist_key = _label_key(type=type, supplier=supplier)
        self._histograms["transaction_latency_seconds"][hist_key].append(
            latency_ms / 1000.0
        )

    def record_round(
        self,
        supplier: str,
        brand: str,
        wager: Decimal,
        payout: Decimal,
        duration_ms: int,
    ) -> None:
        self.rounds_total += 1
        key = _label_key(supplier=supplier, brand=brand)
        self._counters["rounds_total"][key] += 1

        hist_key = _label_key(supplier=supplier)
        self._histograms["round_duration_seconds"][hist_key].append(
            duration_ms / 1000.0
        )

    def record_compliance_violation(self, type: str, jurisdiction: str) -> None:
        self.compliance_violations_total += 1
        key = _label_key(type=type, jurisdiction=jurisdiction)
        self._counters["compliance_violations_total"][key] += 1

    def record_supplier_error(self, supplier: str, error_code: str) -> None:
        self.supplier_errors_total += 1
        key = _label_key(supplier=supplier, error_code=error_code)
        self._counters["supplier_errors_total"][key] += 1

    def session_opened(self, supplier: str, brand: str) -> None:
        key = _label_key(supplier=supplier, brand=brand)
        self._gauges["active_sessions"][key] += 1

    def session_closed(self, supplier: str, brand: str) -> None:
        key = _label_key(supplier=supplier, brand=brand)
        self._gauges["active_sessions"][key] = max(
            0.0, self._gauges["active_sessions"][key] - 1
        )


# ---------------------------------------------------------------------------
# Prometheus implementation (production)
# ---------------------------------------------------------------------------


class PrometheusMetrics(MetricsCollector):
    """Production metrics backed by ``prometheus_client``.

    All metric names use the ``acmetocasino_game_`` prefix.

    Parameters
    ----------
    registry:
        Optional custom ``CollectorRegistry``.  When ``None`` (default) the
        global ``prometheus_client.REGISTRY`` is used.

    Notes
    -----
    ``prometheus_client`` is listed as an optional dependency.  An
    ``ImportError`` is raised at construction time if it is not installed,
    rather than at import time.

    Metric catalogue
    ----------------
    +--------------------------------------------------+----------+-----------------------------------------------+
    | Name                                             | Type     | Labels                                        |
    +==================================================+==========+===============================================+
    | acmetocasino_game_launches_total                 | Counter  | supplier, brand, jurisdiction, mode           |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_transactions_total             | Counter  | type, supplier, status                        |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_rounds_total                   | Counter  | supplier, brand                               |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_compliance_violations_total    | Counter  | type, jurisdiction                            |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_supplier_errors_total          | Counter  | supplier, error_code                          |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_transaction_latency_seconds    | Histogram| type, supplier                                |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_round_duration_seconds         | Histogram| supplier                                      |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_active_sessions                | Gauge    | supplier, brand                               |
    +--------------------------------------------------+----------+-----------------------------------------------+
    | acmetocasino_game_player_balance                 | Gauge    | currency                                      |
    +--------------------------------------------------+----------+-----------------------------------------------+
    """

    #: Histogram buckets for transaction latency (milliseconds → seconds).
    LATENCY_BUCKETS: tuple[float, ...] = (
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
    )

    #: Histogram buckets for round duration.
    ROUND_DURATION_BUCKETS: tuple[float, ...] = (
        0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0
    )

    def __init__(self, registry: Any | None = None) -> None:
        try:
            import prometheus_client as prom  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "prometheus_client is required to use PrometheusMetrics. "
                "Install it with: pip install prometheus-client"
            ) from exc

        reg_kwargs: dict[str, Any] = {}
        if registry is not None:
            reg_kwargs["registry"] = registry

        self._launches_total = prom.Counter(
            "acmetocasino_game_launches_total",
            "Number of game sessions launched.",
            ["supplier", "brand", "jurisdiction", "mode"],
            **reg_kwargs,
        )
        self._transactions_total = prom.Counter(
            "acmetocasino_game_transactions_total",
            "Number of wallet transactions processed.",
            ["type", "supplier", "status"],
            **reg_kwargs,
        )
        self._rounds_total = prom.Counter(
            "acmetocasino_game_rounds_total",
            "Number of game rounds completed.",
            ["supplier", "brand"],
            **reg_kwargs,
        )
        self._compliance_violations_total = prom.Counter(
            "acmetocasino_game_compliance_violations_total",
            "Number of compliance rule violations triggered.",
            ["type", "jurisdiction"],
            **reg_kwargs,
        )
        self._supplier_errors_total = prom.Counter(
            "acmetocasino_game_supplier_errors_total",
            "Number of supplier integration errors.",
            ["supplier", "error_code"],
            **reg_kwargs,
        )
        self._transaction_latency = prom.Histogram(
            "acmetocasino_game_transaction_latency_seconds",
            "Wallet transaction round-trip latency in seconds.",
            ["type", "supplier"],
            buckets=self.LATENCY_BUCKETS,
            **reg_kwargs,
        )
        self._round_duration = prom.Histogram(
            "acmetocasino_game_round_duration_seconds",
            "Time between round start and settlement in seconds.",
            ["supplier"],
            buckets=self.ROUND_DURATION_BUCKETS,
            **reg_kwargs,
        )
        self._active_sessions = prom.Gauge(
            "acmetocasino_game_active_sessions",
            "Number of active game sessions.",
            ["supplier", "brand"],
            **reg_kwargs,
        )
        self._player_balance = prom.Gauge(
            "acmetocasino_game_player_balance",
            "Aggregate player balance across all accounts (monitoring only).",
            ["currency"],
            **reg_kwargs,
        )

    # ------------------------------------------------------------------
    # MetricsCollector implementation
    # ------------------------------------------------------------------

    def record_launch(
        self,
        supplier: str,
        brand: str,
        jurisdiction: str,
        mode: str,
    ) -> None:
        self._launches_total.labels(
            supplier=supplier, brand=brand, jurisdiction=jurisdiction, mode=mode
        ).inc()

    def record_transaction(
        self,
        type: str,
        supplier: str,
        succeeded: bool,
        latency_ms: int,
    ) -> None:
        status = "ok" if succeeded else "error"
        self._transactions_total.labels(
            type=type, supplier=supplier, status=status
        ).inc()
        self._transaction_latency.labels(
            type=type, supplier=supplier
        ).observe(latency_ms / 1000.0)

    def record_round(
        self,
        supplier: str,
        brand: str,
        wager: Decimal,
        payout: Decimal,
        duration_ms: int,
    ) -> None:
        self._rounds_total.labels(supplier=supplier, brand=brand).inc()
        self._round_duration.labels(supplier=supplier).observe(duration_ms / 1000.0)

    def record_compliance_violation(self, type: str, jurisdiction: str) -> None:
        self._compliance_violations_total.labels(
            type=type, jurisdiction=jurisdiction
        ).inc()

    def record_supplier_error(self, supplier: str, error_code: str) -> None:
        self._supplier_errors_total.labels(
            supplier=supplier, error_code=error_code
        ).inc()

    def session_opened(self, supplier: str, brand: str) -> None:
        self._active_sessions.labels(supplier=supplier, brand=brand).inc()

    def session_closed(self, supplier: str, brand: str) -> None:
        self._active_sessions.labels(supplier=supplier, brand=brand).dec()

    def update_aggregate_balance(self, currency: str, total_balance: Decimal) -> None:
        """Update the aggregate player balance gauge for *currency*.

        This method is intentionally not part of the base ``MetricsCollector``
        interface because it is only meaningful in a Prometheus context where
        periodic scraping provides a snapshot of aggregate balances.  The
        value should be computed by a background task that sums all player
        accounts in the given currency.

        Parameters
        ----------
        currency:
            ISO-4217 currency code.
        total_balance:
            Sum of all player balances in *currency*.
        """
        self._player_balance.labels(currency=currency).set(float(total_balance))


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "MetricsCollector",
    "InMemoryMetrics",
    "PrometheusMetrics",
]
