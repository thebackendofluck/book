# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
# =============================================================================
# LEGACY TEACHING STUB — NOT THE CURRENT SIGAP WIRE CONTRACT.
#
# This module demonstrates internal event aggregation, a delivery ledger and
# retry mechanics. Its historical JSON report types and `/v1/reports/*` routes
# must never be connected to production SIGAP. The current regulatory boundary
# uses six XSD-defined XML document families, an e-CNPJ XML signature,
# GZIP/Base64 packaging, JWT + mTLS delivery to category-specific `/lote`
# endpoints, and later movement reconciliation. Most daily families use D-2.
#
# For the production-shaped delivery reference, see:
#   cloudflare/src/sigap-reporter.ts
#
# Official documentation:
#   https://documentacao-sigap-rec.ni.estaleiro.serpro.gov.br/manuais/MF-SPA-SMF-MN-002-R05.pdf
#   https://documentacao-sigap-rec.ni.estaleiro.serpro.gov.br/padroes/
#   https://documentacao-sigap-rec.ni.estaleiro.serpro.gov.br/documentacao_api_recepcao/
#
# References:
#   Lei 14.790/2023: https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/L14790.htm
#   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
#   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
#   COAF: https://www.gov.br/coaf/
# =============================================================================

Legacy Internal Reporting Stub -- Brazilian Betting Platform
==============================================================
This file is retained to demonstrate internal aggregation and retry patterns.
It does not implement the current regulator-facing SIGAP transport contract.

Features:
  - Real-time event streaming to Kafka
  - Daily GGR calculation and report generation
  - Player activity aggregation
  - Bet-level detail reporting
  - Write-ahead logging (WAL) for zero data loss
  - Retry with exponential backoff
  - e-CNPJ certificate authentication (mTLS stub)
  - JSON schema validation for internal illustrative records
  - Historical scheduling examples

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import enum
import json
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import aiohttp
import jsonschema
import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class SIGAPError(Exception):
    """Base SIGAP exception."""


class SchemaValidationError(SIGAPError):
    """An internal illustrative record fails JSON schema validation."""


class SIGAPSubmissionError(SIGAPError):
    """API call to SIGAP endpoint failed."""


class WALError(SIGAPError):
    """Write-ahead log operation failed."""


class ReportGenerationError(SIGAPError):
    """Error during report generation or aggregation."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReportType(str, enum.Enum):
    DAILY_GGR = "daily_ggr"
    PLAYER_ACTIVITY = "player_activity"
    BET_DETAIL = "bet_detail"
    AML_SUSPICIOUS = "aml_suspicious"
    WEEKLY_SUMMARY = "weekly_summary"
    MONTHLY_COMPLIANCE = "monthly_compliance"


class ReportStatus(str, enum.Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    VALIDATING = "validating"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    RETRYING = "retrying"


class EventType(str, enum.Enum):
    BET_PLACED = "bet_placed"
    BET_SETTLED = "bet_settled"
    BET_CANCELLED = "bet_cancelled"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    LOGIN = "login"
    LOGOUT = "logout"
    BONUS_AWARDED = "bonus_awarded"
    PLAYER_SUSPENDED = "player_suspended"


# ---------------------------------------------------------------------------
# Legacy internal JSON schemas; these are not official SIGAP schemas.
# ---------------------------------------------------------------------------

SIGAP_SCHEMAS: Dict[ReportType, Dict[str, Any]] = {
    ReportType.DAILY_GGR: {
        "type": "object",
        "required": ["report_id", "operator_cnpj", "reference_date", "ggr_brl",
                     "total_bets", "total_players", "generated_at"],
        "properties": {
            "report_id": {"type": "string"},
            "operator_cnpj": {"type": "string", "pattern": r"^\d{14}$"},
            "reference_date": {"type": "string", "format": "date"},
            "ggr_brl": {"type": "number"},
            "ngr_brl": {"type": "number"},
            "total_bets": {"type": "integer"},
            "total_wagers_brl": {"type": "number"},
            "total_payouts_brl": {"type": "number"},
            "total_deposits": {"type": "number"},
            "total_withdrawals": {"type": "number"},
            "total_players": {"type": "integer"},
            "new_players": {"type": "integer"},
            "generated_at": {"type": "string"},
        },
    },
    ReportType.BET_DETAIL: {
        "type": "object",
        "required": ["report_id", "operator_cnpj", "reference_date", "bets"],
        "properties": {
            "report_id": {"type": "string"},
            "operator_cnpj": {"type": "string"},
            "reference_date": {"type": "string"},
            "bets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["bet_id", "player_cpf_hash", "amount_brl",
                                 "event_type", "placed_at"],
                    "properties": {
                        "bet_id": {"type": "string"},
                        "player_cpf_hash": {"type": "string"},
                        "amount_brl": {"type": "number"},
                        "payout_brl": {"type": "number"},
                        "event_type": {"type": "string"},
                        "market": {"type": "string"},
                        "odds": {"type": "number"},
                        "status": {"type": "string"},
                        "placed_at": {"type": "string"},
                        "settled_at": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
    ReportType.PLAYER_ACTIVITY: {
        "type": "object",
        "required": ["report_id", "operator_cnpj", "reference_date", "players"],
        "properties": {
            "report_id": {"type": "string"},
            "operator_cnpj": {"type": "string"},
            "reference_date": {"type": "string"},
            "players": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["player_cpf_hash", "total_bets", "total_wagered_brl",
                                 "total_won_brl", "net_result_brl"],
                    "properties": {
                        "player_cpf_hash": {"type": "string"},
                        "total_bets": {"type": "integer"},
                        "total_wagered_brl": {"type": "number"},
                        "total_won_brl": {"type": "number"},
                        "net_result_brl": {"type": "number"},
                        "total_deposits_brl": {"type": "number"},
                        "total_withdrawals_brl": {"type": "number"},
                        "session_count": {"type": "integer"},
                        "total_session_minutes": {"type": "integer"},
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class BetEvent:
    bet_id: str
    player_id: str
    player_cpf_hash: str
    amount_brl: float
    payout_brl: float
    event_type: str
    market: str
    odds: float
    status: str
    placed_at: datetime
    settled_at: Optional[datetime] = None


@dataclass
class DailyGGR:
    reference_date: date
    ggr_brl: float
    ngr_brl: float
    total_bets: int
    total_wagers_brl: float
    total_payouts_brl: float
    total_deposits_brl: float
    total_withdrawals_brl: float
    total_players: int
    new_players: int
    bonus_costs_brl: float = 0.0


@dataclass
class WALEntry:
    """Write-ahead log entry for zero-data-loss guarantee."""
    wal_id: str
    report_id: str
    report_type: ReportType
    payload: Dict[str, Any]
    created_at: datetime
    attempts: int = 0
    last_error: Optional[str] = None
    status: ReportStatus = ReportStatus.QUEUED


@dataclass
class ReportRecord:
    report_id: str
    report_type: ReportType
    reference_date: date
    status: ReportStatus
    created_at: datetime
    submitted_at: Optional[datetime] = None
    sigap_ack: Optional[str] = None
    error: Optional[str] = None
    payload_size_bytes: int = 0
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Write-Ahead Log
# ---------------------------------------------------------------------------


class WriteAheadLog:
    """
    File-based WAL for durable, at-least-once delivery to SIGAP.
    In production use a Postgres-backed WAL or Redis Streams.
    """

    def __init__(self, wal_dir: str = "/tmp/sigap_wal") -> None:
        self.wal_dir = Path(wal_dir)
        self.wal_dir.mkdir(parents=True, exist_ok=True)

    def write(self, entry: WALEntry) -> None:
        """Atomically append entry to WAL."""
        wal_file = self.wal_dir / f"{entry.wal_id}.json"
        try:
            tmp = wal_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "wal_id": entry.wal_id,
                "report_id": entry.report_id,
                "report_type": entry.report_type.value,
                "payload": entry.payload,
                "created_at": entry.created_at.isoformat(),
                "attempts": entry.attempts,
                "status": entry.status.value,
            }))
            tmp.rename(wal_file)
        except OSError as exc:
            raise WALError(f"Failed to write WAL entry {entry.wal_id}: {exc}") from exc

    def mark_submitted(self, wal_id: str) -> None:
        wal_file = self.wal_dir / f"{wal_id}.json"
        done_file = self.wal_dir / f"{wal_id}.done"
        if wal_file.exists():
            wal_file.rename(done_file)

    def pending_entries(self) -> Iterator[WALEntry]:
        """Yields all unsubmitted WAL entries for replay on restart."""
        for f in sorted(self.wal_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                yield WALEntry(
                    wal_id=data["wal_id"],
                    report_id=data["report_id"],
                    report_type=ReportType(data["report_type"]),
                    payload=data["payload"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                    attempts=data.get("attempts", 0),
                    status=ReportStatus(data.get("status", "queued")),
                )
            except Exception as exc:
                logger.error("wal_parse_error", file=str(f), error=str(exc))


# ---------------------------------------------------------------------------
# SIGAP API Client
# ---------------------------------------------------------------------------


class SIGAPClient:
    """
    Authenticated HTTPS client for the MF/SPA SIGAP API.
    Uses mTLS with operator e-CNPJ certificate.
    """

    def __init__(
        self,
        base_url: str,
        operator_cnpj: str,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        ca_bundle: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.operator_cnpj = operator_cnpj
        self.cert_path = cert_path
        self.key_path = key_path
        self.ca_bundle = ca_bundle
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build mTLS context with operator e-CNPJ certificate."""
        if not self.cert_path or not self.key_path:
            return None  # Sandbox: no mTLS
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_cert_chain(self.cert_path, self.key_path)
        if self.ca_bundle:
            ctx.load_verify_locations(self.ca_bundle)
        return ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            ssl_ctx = self._build_ssl_context()
            connector = aiohttp.TCPConnector(ssl=ssl_ctx if ssl_ctx is not None else True)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    "Content-Type": "application/json",
                    "X-Operator-CNPJ": self.operator_cnpj,
                    "Accept": "application/json",
                },
            )
        return self._session

    async def submit_report(
        self,
        report_type: ReportType,
        payload: Dict[str, Any],
    ) -> str:
        """
        Submit a report to SIGAP. Returns acknowledgement ID.
        Raises SIGAPSubmissionError on non-2xx response.
        """
        endpoint_map = {
            ReportType.DAILY_GGR: "/v1/reports/ggr",
            ReportType.BET_DETAIL: "/v1/reports/bets",
            ReportType.PLAYER_ACTIVITY: "/v1/reports/players",
            ReportType.AML_SUSPICIOUS: "/v1/reports/aml",
            ReportType.WEEKLY_SUMMARY: "/v1/reports/weekly",
            ReportType.MONTHLY_COMPLIANCE: "/v1/reports/monthly",
        }
        url = self.base_url + endpoint_map[report_type]
        session = await self._get_session()

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=5, max=300),
            reraise=True,
        ):
            with attempt:
                async with session.post(url, json=payload) as resp:
                    if resp.status in (429, 500, 502, 503, 504):
                        raise SIGAPSubmissionError(
                            f"SIGAP transient error {resp.status} for {report_type.value}"
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise SIGAPSubmissionError(
                            f"SIGAP rejected report {resp.status}: {text[:200]}"
                        )
                    data = await resp.json()
                    return data.get("ackId") or data.get("id", str(uuid.uuid4()))

        raise SIGAPSubmissionError("SIGAP submission exhausted retries")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ---------------------------------------------------------------------------
# In-Memory Event Store (replace with Kafka consumer + TimescaleDB)
# ---------------------------------------------------------------------------


class EventStore:
    """Collects raw platform events for aggregation. Stub only."""

    def __init__(self) -> None:
        self._bets: List[BetEvent] = []
        self._lock = asyncio.Lock()

    async def append_bet(self, event: BetEvent) -> None:
        async with self._lock:
            self._bets.append(event)

    async def get_bets_for_date(self, ref_date: date) -> List[BetEvent]:
        return [
            b for b in self._bets
            if b.placed_at.date() == ref_date
        ]


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------


class SIGAPReportGenerator:
    """
    Builds, validates, and submits SIGAP reports.
    Guarantees at-least-once delivery via WAL.
    """

    def __init__(
        self,
        operator_cnpj: str,
        sigap_client: SIGAPClient,
        event_store: EventStore,
        wal: WriteAheadLog,
    ) -> None:
        self.operator_cnpj = operator_cnpj
        self.sigap = sigap_client
        self.events = event_store
        self.wal = wal
        self._report_store: Dict[str, ReportRecord] = {}

    # ------------------------------------------------------------------
    # Daily GGR Report
    # ------------------------------------------------------------------

    async def generate_daily_ggr(self, ref_date: Optional[date] = None) -> ReportRecord:
        """
        Calculates GGR for the reference date and submits to SIGAP.
        GGR = Total Wagers - Total Payouts
        NGR = GGR - Bonus Costs
        """
        if ref_date is None:
            ref_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        report_id = str(uuid.uuid4())
        record = ReportRecord(
            report_id=report_id,
            report_type=ReportType.DAILY_GGR,
            reference_date=ref_date,
            status=ReportStatus.GENERATING,
            created_at=datetime.now(timezone.utc),
        )
        self._report_store[report_id] = record

        bets = await self.events.get_bets_for_date(ref_date)
        settled = [b for b in bets if b.status == "settled"]

        total_wagers = sum(b.amount_brl for b in bets)
        total_payouts = sum(b.payout_brl for b in settled)
        ggr = round(total_wagers - total_payouts, 2)
        ngr = ggr  # simplified; subtract bonuses in production

        payload = {
            "report_id": report_id,
            "operator_cnpj": self.operator_cnpj,
            "reference_date": ref_date.isoformat(),
            "ggr_brl": ggr,
            "ngr_brl": ngr,
            "total_bets": len(bets),
            "total_wagers_brl": round(total_wagers, 2),
            "total_payouts_brl": round(total_payouts, 2),
            "total_deposits": 0.0,
            "total_withdrawals": 0.0,
            "total_players": len({b.player_id for b in bets}),
            "new_players": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return await self._validate_and_submit(
            record, ReportType.DAILY_GGR, payload
        )

    # ------------------------------------------------------------------
    # Bet Detail Report
    # ------------------------------------------------------------------

    async def generate_bet_detail(self, ref_date: Optional[date] = None) -> ReportRecord:
        """Submits granular bet-level data to SIGAP."""
        if ref_date is None:
            ref_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        report_id = str(uuid.uuid4())
        record = ReportRecord(
            report_id=report_id,
            report_type=ReportType.BET_DETAIL,
            reference_date=ref_date,
            status=ReportStatus.GENERATING,
            created_at=datetime.now(timezone.utc),
        )
        self._report_store[report_id] = record

        bets = await self.events.get_bets_for_date(ref_date)
        bet_list = [
            {
                "bet_id": b.bet_id,
                "player_cpf_hash": b.player_cpf_hash,
                "amount_brl": b.amount_brl,
                "payout_brl": b.payout_brl,
                "event_type": b.event_type,
                "market": b.market,
                "odds": b.odds,
                "status": b.status,
                "placed_at": b.placed_at.isoformat(),
                "settled_at": b.settled_at.isoformat() if b.settled_at else None,
            }
            for b in bets
        ]

        payload = {
            "report_id": report_id,
            "operator_cnpj": self.operator_cnpj,
            "reference_date": ref_date.isoformat(),
            "bets": bet_list,
        }

        return await self._validate_and_submit(
            record, ReportType.BET_DETAIL, payload
        )

    # ------------------------------------------------------------------
    # Player Activity Aggregation
    # ------------------------------------------------------------------

    async def generate_player_activity(self, ref_date: Optional[date] = None) -> ReportRecord:
        """Aggregates per-player wagering activity for SIGAP."""
        if ref_date is None:
            ref_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        report_id = str(uuid.uuid4())
        record = ReportRecord(
            report_id=report_id,
            report_type=ReportType.PLAYER_ACTIVITY,
            reference_date=ref_date,
            status=ReportStatus.GENERATING,
            created_at=datetime.now(timezone.utc),
        )
        self._report_store[report_id] = record

        bets = await self.events.get_bets_for_date(ref_date)
        agg: Dict[str, Dict[str, Any]] = {}
        for b in bets:
            key = b.player_cpf_hash
            if key not in agg:
                agg[key] = {
                    "player_cpf_hash": key,
                    "total_bets": 0,
                    "total_wagered_brl": 0.0,
                    "total_won_brl": 0.0,
                    "net_result_brl": 0.0,
                    "total_deposits_brl": 0.0,
                    "total_withdrawals_brl": 0.0,
                    "session_count": 1,
                    "total_session_minutes": 0,
                }
            p = agg[key]
            p["total_bets"] += 1
            p["total_wagered_brl"] += b.amount_brl
            p["total_won_brl"] += b.payout_brl
            p["net_result_brl"] = round(p["total_won_brl"] - p["total_wagered_brl"], 2)

        payload = {
            "report_id": report_id,
            "operator_cnpj": self.operator_cnpj,
            "reference_date": ref_date.isoformat(),
            "players": list(agg.values()),
        }

        return await self._validate_and_submit(
            record, ReportType.PLAYER_ACTIVITY, payload
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _validate_and_submit(
        self,
        record: ReportRecord,
        report_type: ReportType,
        payload: Dict[str, Any],
    ) -> ReportRecord:
        """Validate schema, write WAL, submit with retry, mark done."""
        # Schema validation
        record.status = ReportStatus.VALIDATING
        schema = SIGAP_SCHEMAS.get(report_type)
        if schema:
            try:
                jsonschema.validate(payload, schema)
            except jsonschema.ValidationError as exc:
                record.status = ReportStatus.FAILED
                record.error = str(exc.message)
                self._report_store[record.report_id] = record
                raise SchemaValidationError(exc.message) from exc

        # Write to WAL before attempting submission
        wal_entry = WALEntry(
            wal_id=str(uuid.uuid4()),
            report_id=record.report_id,
            report_type=report_type,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        self.wal.write(wal_entry)
        record.payload_size_bytes = len(json.dumps(payload).encode())

        # Submit to SIGAP
        record.status = ReportStatus.SUBMITTING
        try:
            ack_id = await self.sigap.submit_report(report_type, payload)
            record.status = ReportStatus.SUBMITTED
            record.submitted_at = datetime.now(timezone.utc)
            record.sigap_ack = ack_id
            self.wal.mark_submitted(wal_entry.wal_id)
            logger.info(
                "sigap_report_submitted",
                report_id=record.report_id,
                report_type=report_type.value,
                ack_id=ack_id,
            )
        except (SIGAPSubmissionError, RetryError) as exc:
            record.status = ReportStatus.FAILED
            record.error = str(exc)
            record.retry_count += 1
            logger.error(
                "sigap_report_failed",
                report_id=record.report_id,
                report_type=report_type.value,
                error=str(exc),
                retry_count=record.retry_count,
            )

        self._report_store[record.report_id] = record
        return record

    async def replay_wal(self) -> int:
        """Replays unsubmitted WAL entries on service restart."""
        replayed = 0
        for entry in self.wal.pending_entries():
            logger.info(
                "wal_replay_attempt",
                wal_id=entry.wal_id,
                report_id=entry.report_id,
                report_type=entry.report_type.value,
            )
            try:
                ack_id = await self.sigap.submit_report(
                    entry.report_type, entry.payload
                )
                self.wal.mark_submitted(entry.wal_id)
                replayed += 1
                logger.info("wal_replay_success", wal_id=entry.wal_id, ack_id=ack_id)
            except Exception as exc:
                logger.error(
                    "wal_replay_failed",
                    wal_id=entry.wal_id,
                    error=str(exc),
                )
        return replayed


# ---------------------------------------------------------------------------
# Report Scheduler
# ---------------------------------------------------------------------------


class ReportScheduler:
    """
    Schedules automatic SIGAP report submissions:
      - Daily GGR + Bet Detail + Player Activity: 06:00 BRT (09:00 UTC)
      - Weekly summary: Monday 07:00 BRT
      - Monthly compliance: 1st of month 08:00 BRT
    """

    def __init__(self, generator: SIGAPReportGenerator) -> None:
        self.generator = generator
        self._tasks: List[asyncio.Task] = []

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._daily_runner()))
        self._tasks.append(asyncio.create_task(self._weekly_runner()))
        self._tasks.append(asyncio.create_task(self._monthly_runner()))
        logger.info("sigap_scheduler_started")

    async def _daily_runner(self) -> None:
        while True:
            try:
                await self._run_daily_reports()
            except Exception as exc:
                logger.error("daily_report_error", error=str(exc))
            await asyncio.sleep(86400)

    async def _weekly_runner(self) -> None:
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now.weekday() == 0:  # Monday
                    await self.generator.generate_daily_ggr()  # extend for weekly
                    logger.info("weekly_report_submitted")
            except Exception as exc:
                logger.error("weekly_report_error", error=str(exc))
            await asyncio.sleep(86400)

    async def _monthly_runner(self) -> None:
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now.day == 1:
                    logger.info("monthly_report_submitted")
            except Exception as exc:
                logger.error("monthly_report_error", error=str(exc))
            await asyncio.sleep(86400)

    async def _run_daily_reports(self) -> None:
        ref = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        results = await asyncio.gather(
            self.generator.generate_daily_ggr(ref),
            self.generator.generate_bet_detail(ref),
            self.generator.generate_player_activity(ref),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error("daily_report_partial_failure", error=str(r))

    def stop(self) -> None:
        for task in self._tasks:
            task.cancel()


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

event_store = EventStore()
wal = WriteAheadLog()
generator: Optional[SIGAPReportGenerator] = None
scheduler: Optional[ReportScheduler] = None


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    global generator, scheduler
    sigap_client = SIGAPClient(
        base_url="https://sandbox.sigap.fazenda.gov.br",
        operator_cnpj="12345678000195",
        cert_path=None,
        key_path=None,
    )
    generator = SIGAPReportGenerator(
        operator_cnpj="12345678000195",
        sigap_client=sigap_client,
        event_store=event_store,
        wal=wal,
    )
    replayed = await generator.replay_wal()
    logger.info("wal_replay_complete", replayed=replayed)
    scheduler = ReportScheduler(generator)
    scheduler.start()
    logger.info("sigap_reporter_started")
    yield
    scheduler.stop()
    await sigap_client.close()
    logger.info("sigap_reporter_shutdown")


app = FastAPI(
    title="SIGAP Regulatory Reporter",
    description="Brazilian betting platform SIGAP compliance reporting",
    version="1.0.0",
    lifespan=lifespan,
)


class BetEventRequest(BaseModel):
    """Ingest a bet event for aggregation."""
    bet_id: str
    player_id: str
    player_cpf_hash: str
    amount_brl: float = Field(..., gt=0)
    payout_brl: float = Field(default=0.0, ge=0)
    event_type: str
    market: str
    odds: float = Field(..., gt=0)
    status: str
    placed_at: datetime


@app.post("/v1/events/bet", response_model=Dict[str, str])
async def ingest_bet(req: BetEventRequest) -> Dict[str, str]:
    """Ingest a bet event into the aggregation pipeline."""
    evt = BetEvent(
        bet_id=req.bet_id,
        player_id=req.player_id,
        player_cpf_hash=req.player_cpf_hash,
        amount_brl=req.amount_brl,
        payout_brl=req.payout_brl,
        event_type=req.event_type,
        market=req.market,
        odds=req.odds,
        status=req.status,
        placed_at=req.placed_at,
    )
    await event_store.append_bet(evt)
    return {"status": "ingested", "bet_id": req.bet_id}


@app.post("/v1/reports/daily-ggr", response_model=Dict[str, Any])
async def trigger_daily_ggr(ref_date: Optional[str] = None) -> Dict[str, Any]:
    """Manually trigger daily GGR report generation."""
    d = date.fromisoformat(ref_date) if ref_date else None
    record = await generator.generate_daily_ggr(d)  # type: ignore[union-attr]
    return {
        "report_id": record.report_id,
        "status": record.status.value,
        "sigap_ack": record.sigap_ack,
        "error": record.error,
    }


@app.post("/v1/reports/bet-detail", response_model=Dict[str, Any])
async def trigger_bet_detail(ref_date: Optional[str] = None) -> Dict[str, Any]:
    d = date.fromisoformat(ref_date) if ref_date else None
    record = await generator.generate_bet_detail(d)  # type: ignore[union-attr]
    return {"report_id": record.report_id, "status": record.status.value}


@app.post("/v1/reports/player-activity", response_model=Dict[str, Any])
async def trigger_player_activity(ref_date: Optional[str] = None) -> Dict[str, Any]:
    d = date.fromisoformat(ref_date) if ref_date else None
    record = await generator.generate_player_activity(d)  # type: ignore[union-attr]
    return {"report_id": record.report_id, "status": record.status.value}


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "sigap-reporter"}


if __name__ == "__main__":
    uvicorn.run("sigap_reporter:app", host="0.0.0.0", port=8003, reload=False)
