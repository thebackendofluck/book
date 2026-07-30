# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Withdrawal processor services.

Provides:
- WithdrawDAO: queries eligible self-excluded players from the database
- PlatformServiceClient: wraps the JSON-RPC style platform usergateway API
- WithdrawProcessor: orchestrates the batch (main business logic)

Design principles from the Scala original:
- Sequential processing is intentional for a batch job (no async needed)
- The single-option guard is a safety measure: multiple payment methods on
  file means manual processing, not automated guessing
- All failures are caught and logged individually so one failure doesn't
  block the rest of the batch
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

if __package__:
    from .models import (  # ty: ignore[unresolved-import]
        UserWithdraw,
        WithdrawOption,
        WithdrawOptionsResponse,
        WithdrawResponse,
    )
else:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from models import UserWithdraw, WithdrawOption, WithdrawOptionsResponse, WithdrawResponse

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/platform")
PLATFORM_SERVICE_URL = os.getenv(
    "PLATFORM_SERVICE_URL",
    "http://platform.internal.acmetocasino.com/platform/usergateway",
)


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------

class WithdrawDAO:
    """
    Data access for the batch withdrawal processor.

    The main query identifies players who:
      - Are KYC-approved (regulatory requirement)
      - Have a positive cash balance
      - Have at least one successful deposit via the configured PSP
      - Have NO pending withdrawal requests (prevents duplicates)
      - Are self-excluded (the whole reason this batch job exists)

    The self-exclusion start-time filter prevents the job from interfering
    with players who just self-excluded and may be in a cooling-off period.
    """

    ELIGIBLE_PLAYERS_SQL = """
        SELECT
            u.id            AS user_id,
            u.name          AS name,
            ua.balance      AS balance,
            ua.currency     AS currency,
            ui.email        AS email,
            b.id            AS brand_id,
            b.name          AS brand_name
        FROM platform.users u
            JOIN platform.user_info ui
                ON u.id = ui.userid AND ui.country = 'GB'
            JOIN platform.user_accounts ua
                ON u.id = ua.userid AND ua.typeid = 1
            JOIN platform.brands b
                ON b.id = u.affiliateid
        WHERE u.kyc_approved = 1
          AND ua.balance > 0
          AND EXISTS (
              SELECT 1 FROM platform.user_payments p
              WHERE p.user_id = u.id
                AND p.status = 'SUCCEEDED'
                AND p.provider_id = 'payment_gateway'
          )
          AND NOT EXISTS (
              SELECT 1 FROM platform.user_withdraws w
              WHERE w.userid = u.id AND w.status = 0
          )
          AND EXISTS (
              SELECT 1 FROM platform.user_lock ul
              WHERE ul.user_id = u.id
                AND ul.lock_type_id IN ('SELF_EXCLUDE', 'MATCHED_SELF_EXCLUDE')
                AND ul.start_time < :cutoff_date
                AND ul.status NOT IN ('CANCELLED', 'COMPLETED')
          )
        FETCH FIRST :limit ROWS ONLY
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_players_to_withdraw(
        self, cutoff_date: Any, limit: int = 1_000_000
    ) -> list[UserWithdraw]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(self.ELIGIBLE_PLAYERS_SQL),
                {"cutoff_date": cutoff_date, "limit": limit},
            ).mappings()
            return [
                UserWithdraw(
                    user_id=r["user_id"],
                    name=r["name"],
                    email=r["email"],
                    balance=r["balance"],
                    currency=r["currency"],
                    brand_id=r["brand_id"],
                    brand_name=r["brand_name"],
                )
                for r in rows
            ]


# ---------------------------------------------------------------------------
# Platform service client
# ---------------------------------------------------------------------------

class PlatformServiceClient:
    """
    Client for the platform's user gateway API.

    The platform exposes a JSON-RPC style API where the 'type' field in the
    request body determines the operation. Authentication is handled at the
    network level (mTLS) rather than per-request credentials.
    """

    def __init__(self, service_url: str = PLATFORM_SERVICE_URL) -> None:
        self._url = service_url
        self._session = requests.Session()

    def get_withdraw_options(self, user_id: int) -> WithdrawOptionsResponse:
        """Return available withdrawal methods for a player."""
        payload: dict[str, Any] = {
            "type": "getwithdrawoptions",
            "userId": user_id,
            "showblocked": False,
        }
        data = self._post(payload)
        return WithdrawOptionsResponse(
            min_amount=str(data.get("minAmount", "0")),
            options=[
                WithdrawOption(name=o["name"], id=o["id"])
                for o in data.get("options", [])
            ],
        )

    def request_withdraw(self, user_id: int, details: str) -> WithdrawResponse:
        """Initiate a full-balance withdrawal to the specified payment method."""
        payload: dict[str, Any] = {
            "type": "withdraw",
            "userId": user_id,
            "fullAmount": True,
            "details": details,
        }
        data = self._post(payload)
        return WithdrawResponse(
            txn_id=str(data["txnId"]),
            needs_kyc=bool(data.get("needsKyc", False)),
            total=str(data["total"]),
            request_id=str(data["requestId"]),
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            self._url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Batch orchestrator
# ---------------------------------------------------------------------------

class WithdrawProcessor:
    """
    Orchestrates the automated batch withdrawal for self-excluded players.

    Algorithm:
      1. Query DB for eligible players (KYC-approved, self-excluded, positive balance,
         no pending withdrawals)
      2. For each player, fetch available withdrawal options
      3. If exactly one option exists -> process full-balance withdrawal
      4. If multiple options -> skip and log (regulatory safety guard)
      5. Log all outcomes for audit trail
    """

    def __init__(
        self,
        dao: WithdrawDAO,
        platform: PlatformServiceClient,
    ) -> None:
        self._dao = dao
        self._platform = platform

    def run(self, cutoff_date: Any) -> tuple[int, int, int]:
        """
        Process all eligible players.

        Returns:
            (total_players, succeeded, skipped_multi_option)
        """
        log.info("withdraw_processor.start")
        players = self._dao.list_players_to_withdraw(cutoff_date=cutoff_date)
        log.info("withdraw_processor.players_found", count=len(players))

        succeeded = 0
        skipped = 0

        for player in players:
            try:
                resp = self._platform.get_withdraw_options(player.user_id)

                if len(resp.options) == 1:
                    withdraw_resp = self._platform.request_withdraw(
                        player.user_id, resp.options[0].id
                    )
                    log.info(
                        "withdraw.succeeded",
                        user_id=player.user_id,
                        amount=withdraw_resp.total,
                        txn_id=withdraw_resp.txn_id,
                        request_id=withdraw_resp.request_id,
                    )
                    succeeded += 1
                else:
                    log.warning(
                        "withdraw.skipped_multi_option",
                        user_id=player.user_id,
                        balance=player.balance,
                        option_count=len(resp.options),
                    )
                    skipped += 1

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "withdraw.failed",
                    user_id=player.user_id,
                    balance=player.balance,
                    error=str(exc),
                )

        log.info(
            "withdraw_processor.done",
            total=len(players),
            succeeded=succeeded,
            skipped=skipped,
        )
        return len(players), succeeded, skipped
