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
ctr_generator.py — Currency Transaction Report (CTR) generator with
                   structuring / smurfing detection.

Jurisdiction:       United States (primary); multi-jurisdiction notes included
Regulator:          FinCEN (Financial Crimes Enforcement Network)
Regulation refs:
  - Bank Secrecy Act (BSA) 31 U.S.C. § 5313 — CTR filing obligation
    https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1021/
    subpart-D/section-1021.311
  - FinCEN CTR form 112 (FinCEN XML schema)
    https://www.fincen.gov/resources/filing-information/
    currency-transaction-report
  - 31 U.S.C. § 5324 — Structuring prohibition (anti-structuring)
    https://www.law.cornell.edu/uscode/text/31/5324
  - FinCEN Advisory FIN-2014-A005 — Structuring patterns
    https://www.fincen.gov/resources/advisories/fincen-advisory-fin-2014-a005
Penalties:
  - Failure to file: civil penalty up to $25,000/violation; criminal up to
    5 years imprisonment (31 U.S.C. § 5322)
  - Structuring: up to 5 years imprisonment + asset forfeiture (31 U.S.C. § 5324)
  - Wilful violation: up to 10 years imprisonment

Filing deadline:  15 calendar days after the transaction date.
Threshold:        Transactions aggregating >$10,000 in currency in a single day
                  from or to the same person require a CTR.

Structuring detection:
  Multiple transactions just under $10,000 by the same person designed to
  evade CTR filing are a federal crime regardless of the amounts.

Book chapter:  Chapter 19 — Anti-Fraud & Compliance Systems
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CTR_THRESHOLD_USD: Decimal = Decimal("10000.00")
CTR_FILING_DEADLINE_DAYS: int = 15

# Structuring detection — flag patterns approaching threshold
STRUCTURING_WINDOW_DAYS: int = 3
STRUCTURING_TRANSACTION_FLOOR_USD: Decimal = Decimal("3000.00")
STRUCTURING_PROXIMITY_BAND_USD: Decimal = Decimal("1000.00")  # within $1k of threshold
STRUCTURING_MIN_TX_COUNT: int = 3  # at least 3 transactions in window


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BUY_CHIPS = "buy_chips"
    CASH_OUT = "cash_out"


class CtrStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    FILED = "filed"
    AMENDED = "amended"


class StructuringAlert(str, Enum):
    BELOW_THRESHOLD = "below_threshold"           # single tx just under $10k
    SPLIT_TRANSACTIONS = "split_transactions"     # multiple tx in short window
    CROSS_DAY_AGGREGATE = "cross_day_aggregate"   # spread across multiple days
    THIRD_PARTY_STRUCTURING = "third_party"       # multiple persons same account


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CurrencyTransaction:
    """A single reportable currency transaction."""
    tx_id: str
    player_id: str
    amount_usd: Decimal
    tx_type: TransactionType
    occurred_at: datetime
    payment_method: str
    ip_address: Optional[str] = None


@dataclass
class DailyAggregate:
    """Aggregate of all currency transactions for a player on a single day."""
    player_id: str
    transaction_date: date
    total_in_usd: Decimal
    total_out_usd: Decimal
    transaction_ids: list[str]
    ctr_required: bool

    @property
    def net_usd(self) -> Decimal:
        return self.total_in_usd - self.total_out_usd


@dataclass
class CtrReport:
    """FinCEN CTR form 112 record."""
    ctr_id: str
    player_id: str
    transaction_date: date
    total_amount_usd: Decimal
    transactions: list[CurrencyTransaction]
    filing_deadline: date
    status: CtrStatus = CtrStatus.DRAFT
    filed_at: Optional[datetime] = None
    fincen_reference: Optional[str] = None
    amended_from_ctr_id: Optional[str] = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        self.audit_trail.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail or {},
        })


@dataclass
class StructuringDetectionResult:
    """Result of structuring / smurfing analysis."""
    player_id: str
    alert_type: Optional[StructuringAlert]
    flagged: bool
    evidence_tx_ids: list[str]
    aggregate_amount_usd: Decimal
    window_days: int
    analysis_timestamp: datetime
    notes: str = ""


# ---------------------------------------------------------------------------
# Daily aggregator
# ---------------------------------------------------------------------------

class DailyAggregator:
    """
    Aggregates same-day transactions per player to determine CTR obligation.

    FinCEN requires a CTR when a player conducts currency transactions
    exceeding $10,000 in a single business day, whether in a single
    transaction or multiple transactions.
    """

    def aggregate(
        self,
        player_id: str,
        transactions: list[CurrencyTransaction],
        for_date: date,
    ) -> DailyAggregate:
        """Aggregate all transactions for a player on a specific date."""
        day_txs = [
            t for t in transactions
            if t.player_id == player_id
            and t.occurred_at.date() == for_date
        ]

        total_in = sum(
            (t.amount_usd for t in day_txs
             if t.tx_type in (TransactionType.DEPOSIT, TransactionType.BUY_CHIPS)),
            Decimal("0"),
        )
        total_out = sum(
            (t.amount_usd for t in day_txs
             if t.tx_type in (TransactionType.WITHDRAWAL, TransactionType.CASH_OUT)),
            Decimal("0"),
        )

        ctr_required = total_in > CTR_THRESHOLD_USD or total_out > CTR_THRESHOLD_USD

        return DailyAggregate(
            player_id=player_id,
            transaction_date=for_date,
            total_in_usd=total_in,
            total_out_usd=total_out,
            transaction_ids=[t.tx_id for t in day_txs],
            ctr_required=ctr_required,
        )


# ---------------------------------------------------------------------------
# Structuring detector
# ---------------------------------------------------------------------------

class StructuringDetector:
    """
    Detects structuring patterns (smurfing) as defined in 31 U.S.C. § 5324
    and FinCEN Advisory FIN-2014-A005.

    Patterns detected:
      1. Single transaction just below $10,000
      2. Multiple transactions in a short window that aggregate near threshold
      3. Cross-day aggregates designed to stay under daily limit
    """

    def analyse(
        self,
        player_id: str,
        recent_transactions: list[CurrencyTransaction],
    ) -> StructuringDetectionResult:
        """
        Analyse recent transactions for structuring indicators.

        Pass the last STRUCTURING_WINDOW_DAYS days of transactions
        for a single player.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=STRUCTURING_WINDOW_DAYS)
        window_txs = [
            t for t in recent_transactions
            if t.player_id == player_id
            and t.occurred_at >= window_start
            and t.amount_usd >= STRUCTURING_TRANSACTION_FLOOR_USD
        ]

        if not window_txs:
            return self._no_alert(player_id, now)

        # Pattern 1: Single large tx just below $10k threshold
        for tx in window_txs:
            if (
                CTR_THRESHOLD_USD - STRUCTURING_PROXIMITY_BAND_USD
                <= tx.amount_usd
                < CTR_THRESHOLD_USD
            ):
                log.warning(
                    "structuring: single tx near threshold",
                    player_id=player_id,
                    tx_id=tx.tx_id,
                    amount=str(tx.amount_usd),
                )
                return StructuringDetectionResult(
                    player_id=player_id,
                    alert_type=StructuringAlert.BELOW_THRESHOLD,
                    flagged=True,
                    evidence_tx_ids=[tx.tx_id],
                    aggregate_amount_usd=tx.amount_usd,
                    window_days=STRUCTURING_WINDOW_DAYS,
                    analysis_timestamp=now,
                    notes=f"Single transaction ${tx.amount_usd} is within "
                          f"${STRUCTURING_PROXIMITY_BAND_USD} of CTR threshold",
                )

        # Pattern 2: Multiple transactions aggregating near threshold
        if len(window_txs) >= STRUCTURING_MIN_TX_COUNT:
            aggregate = sum((t.amount_usd for t in window_txs), Decimal("0"))
            if (
                CTR_THRESHOLD_USD - STRUCTURING_PROXIMITY_BAND_USD
                <= aggregate
                < CTR_THRESHOLD_USD * Decimal("1.5")
            ):
                log.warning(
                    "structuring: split transactions near threshold",
                    player_id=player_id,
                    tx_count=len(window_txs),
                    aggregate=str(aggregate),
                )
                return StructuringDetectionResult(
                    player_id=player_id,
                    alert_type=StructuringAlert.SPLIT_TRANSACTIONS,
                    flagged=True,
                    evidence_tx_ids=[t.tx_id for t in window_txs],
                    aggregate_amount_usd=aggregate,
                    window_days=STRUCTURING_WINDOW_DAYS,
                    analysis_timestamp=now,
                    notes=f"{len(window_txs)} transactions totalling ${aggregate} "
                          f"in {STRUCTURING_WINDOW_DAYS} days",
                )

        return self._no_alert(player_id, now)

    @staticmethod
    def _no_alert(player_id: str, ts: datetime) -> StructuringDetectionResult:
        return StructuringDetectionResult(
            player_id=player_id,
            alert_type=None,
            flagged=False,
            evidence_tx_ids=[],
            aggregate_amount_usd=Decimal("0"),
            window_days=STRUCTURING_WINDOW_DAYS,
            analysis_timestamp=ts,
        )


# ---------------------------------------------------------------------------
# CTR generator
# ---------------------------------------------------------------------------

class CtrGenerator:
    """
    Generates FinCEN CTR form 112 records from daily transaction aggregates.

    Production systems submit via FinCEN BSA E-Filing System:
    https://bsaefiling.fincen.treas.gov/
    """

    def __init__(self) -> None:
        self._aggregator = DailyAggregator()
        self._structuring = StructuringDetector()

    def evaluate_day(
        self,
        player_id: str,
        transactions: list[CurrencyTransaction],
        for_date: date,
    ) -> Optional[CtrReport]:
        """
        Evaluate a player's transactions for a given day.
        Returns a CTR report if the threshold is exceeded, otherwise None.
        Also triggers structuring analysis.
        """
        aggregate = self._aggregator.aggregate(player_id, transactions, for_date)

        # Always run structuring analysis regardless of CTR requirement
        structuring = self._structuring.analyse(player_id, transactions)
        if structuring.flagged:
            log.warning("ctr: structuring detected — escalate to SAR",
                        player_id=player_id,
                        alert_type=structuring.alert_type.value if structuring.alert_type else "unknown",
                        aggregate=str(structuring.aggregate_amount_usd))
            # TODO: auto-create SAR via SarGenerator when structuring detected

        if not aggregate.ctr_required:
            return None

        ctr_id = f"CTR-{uuid.uuid4().hex[:12].upper()}"
        day_txs = [
            t for t in transactions
            if t.player_id == player_id
            and t.occurred_at.date() == for_date
        ]

        report = CtrReport(
            ctr_id=ctr_id,
            player_id=player_id,
            transaction_date=for_date,
            total_amount_usd=max(aggregate.total_in_usd, aggregate.total_out_usd),
            transactions=day_txs,
            filing_deadline=for_date + timedelta(days=CTR_FILING_DEADLINE_DAYS),
        )
        report.record_event("ctr_created", {
            "total_in": str(aggregate.total_in_usd),
            "total_out": str(aggregate.total_out_usd),
            "tx_count": len(day_txs),
        })
        log.info("ctr: report created",
                 ctr_id=ctr_id,
                 player_id=player_id,
                 date=str(for_date),
                 total=str(report.total_amount_usd))
        return report

    def serialise_fincen(self, report: CtrReport) -> dict[str, Any]:
        """Produce FinCEN CTR form 112 XML-compatible payload."""
        return {
            "form_type": "FINCEN_CTR_112",
            "ctr_id": report.ctr_id,
            "transaction_date": str(report.transaction_date),
            "total_currency_usd": str(report.total_amount_usd),
            "person": {
                "account_id": report.player_id,
                # In production: include full identity pulled from KYC record
            },
            "transactions": [
                {
                    "tx_id": t.tx_id,
                    "amount_usd": str(t.amount_usd),
                    "type": t.tx_type.value,
                    "timestamp": t.occurred_at.isoformat(),
                }
                for t in report.transactions
            ],
            "filing_institution": "<<OPERATOR_NAME>>",
            "filing_deadline": str(report.filing_deadline),
        }

    def mark_filed(
        self, report: CtrReport, fincen_reference: str
    ) -> CtrReport:
        report.status = CtrStatus.FILED
        report.filed_at = datetime.now(timezone.utc)
        report.fincen_reference = fincen_reference
        report.record_event("filed", {"fincen_reference": fincen_reference})
        log.info("ctr: filed with FinCEN",
                 ctr_id=report.ctr_id,
                 reference=fincen_reference)
        return report


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    generator = CtrGenerator()
    today = date(2026, 4, 3)

    txs = [
        CurrencyTransaction(
            tx_id=f"tx-{i:04d}",
            player_id="player-us-0042",
            amount_usd=Decimal("3500.00"),
            tx_type=TransactionType.DEPOSIT,
            occurred_at=datetime(2026, 4, 3, 10 + i, 0, tzinfo=timezone.utc),
            payment_method="wire_transfer",
        )
        for i in range(3)
    ]

    ctr = generator.evaluate_day("player-us-0042", txs, today)
    if ctr:
        payload = generator.serialise_fincen(ctr)
        print(f"CTR {ctr.ctr_id} generated: ${ctr.total_amount_usd}")
        print(f"Filing deadline: {ctr.filing_deadline}")
        print(f"Payload keys: {list(payload.keys())}")
    else:
        print("No CTR required for this day.")


if __name__ == "__main__":
    _demo()
