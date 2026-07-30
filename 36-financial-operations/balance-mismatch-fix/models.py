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
Domain models for the balance mismatch fix tool.

This is a one-shot remediation script used to replay failed Kafka messages
that were logged to Elasticsearch. It:
  1. Queries Elasticsearch for log lines matching "Failed to send message to <topic>"
  2. Parses the serialized event from the log message (AccountsEvent or RoundPlayedEvent)
  3. Re-publishes them to Kafka in chronological order, filtered by a specified user list

The tool is designed to be run idempotently: re-publishing an event to Kafka
may result in duplicate processing on the consumer side, so consumers must
implement their own idempotency (e.g., checking if a transaction already exists).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Elasticsearch result models
# ---------------------------------------------------------------------------

class FailedMessagePush(BaseModel):
    """
    A Kafka message that failed to be produced, captured from Elasticsearch logs.

    Log format: "Failed to send message to {topic} partition {partition}: {message}"
    """
    topic: str
    message: str

    @classmethod
    def parse_from_log_line(cls, source: str) -> "FailedMessagePush | None":
        """
        Parse a log line into a FailedMessagePush.

        Returns None if the line doesn't match the expected format.
        """
        import re
        pattern = r"Failed to send message to (\S+) partition \d+: (.+)"
        match = re.match(pattern, source)
        if match:
            return cls(topic=match.group(1), message=match.group(2))
        return None


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------

class CoreEventInfo(BaseModel):
    event_type: str
    transaction_ref: str
    timestamp: datetime
    brand_id: int
    brand_name: str
    external_id: str | None = None
    user_id: int = 0
    correlation_id: str | None = None
    country: str = ""
    state: str = ""


class TransactionDetails(BaseModel):
    currency: str
    amount: int
    account_id: int
    is_round: bool | None = None
    is_debit: bool = False
    bonus_amount: int = 0
    is_free_round: bool = False


class GameActivityDetails(BaseModel):
    game_id: str
    game_category_id: int
    supplier_name: str
    game_name: str


class TransactionHistory(BaseModel):
    transaction_id: int
    account_id: int
    account_type: int
    transaction_type: str
    supplier_action_code: str
    amount: int
    is_debit: bool
    balance_after: int
    round_id: int | None = None
    timestamp: datetime
    brand_id: int
    user_id: int
    bonus_id: int | None = None


class BalanceInfo(BaseModel):
    cash_balance: int
    bonus_balance: int


class AccountsEvent(BaseModel):
    core_info: CoreEventInfo
    transaction_details: TransactionDetails
    game_activity: GameActivityDetails | None = None
    transaction_history: list[TransactionHistory] = Field(default_factory=list)
    extra_data: dict[str, Any] = Field(default_factory=dict)
    balance_info: BalanceInfo | None = None


class RoundPlayedEvent(BaseModel):
    """Game round event from the game service."""
    core_info: CoreEventInfo
    round_id: str
    game_id: str
    supplier: str
    amount: int
    currency: str
    user_external_id: str | None = None


# ---------------------------------------------------------------------------
# User data
# ---------------------------------------------------------------------------

class UserRecord(BaseModel):
    id: int
    external_id: str
    email: str | None = None
    brand_id: int | None = None
