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
Balance mismatch fix services.

Provides:
- ElasticsearchClient: scrolling search for failed Kafka message log lines
- KafkaRepublisher: re-publishes parsed events to Kafka in chronological order
- UserRepository: loads user records by ID list from the DB
- BalanceMismatchProcessor: main orchestration logic

Architecture:
  Elasticsearch stores application logs including lines like:
    "Failed to send message to ACCOUNTS_EVENTS_TOPIC partition 3: AccountsEvent(...)"

  This tool:
    1. Fetches all such lines within a date range via Elasticsearch scroll API
    2. Parses the embedded serialised Scala case class toString() back into events
    3. Filters to only the users in the provided ID list
    4. Re-publishes in timestamp order to Kafka

  The Scala toString parsing is a quirk of the original -- the events were logged
  as Scala case class toString() representations, not JSON. The Python version
  uses a simple regex/JSON parser instead.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests
import structlog
from confluent_kafka import Producer
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .models import (
    AccountsEvent,
    BalanceInfo,
    CoreEventInfo,
    FailedMessagePush,
    GameActivityDetails,
    RoundPlayedEvent,
    TransactionDetails,
    UserRecord,
)

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "https://localhost:9200")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


# ---------------------------------------------------------------------------
# Elasticsearch client
# ---------------------------------------------------------------------------

class ElasticsearchClient:
    """
    Scrolling Elasticsearch client for fetching failed Kafka message log lines.

    Uses the Kibana proxy API (same as the Scala original).
    Paginates via the scroll API to handle large result sets without OOM risk.
    """

    DEFAULT_KEEP_ALIVE = "10m"
    DEFAULT_PAGE_SIZE = 500

    def __init__(
        self,
        kibana_host: str = ELASTICSEARCH_URL,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
        page_size: int = DEFAULT_PAGE_SIZE,
        verify_ssl: bool = False,
    ) -> None:
        self._base_url = f"{kibana_host}/_plugin/kibana/api/console/proxy"
        self._keep_alive = keep_alive
        self._page_size = page_size
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._headers = {
            "accept": "text/plain, */*; q=0.01",
            "kbn-version": "7.4.2",
            "content-type": "application/json",
        }

    def fetch(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[FailedMessagePush]:
        """
        Fetch all failed-message log entries in the given date range.

        Returns parsed FailedMessagePush objects (skips unparseable lines).
        """
        query = self._build_search_query(from_dt, to_dt)
        first_page, scroll_id, total = self._fetch_first_page(query)

        all_hits = list(first_page)
        pages = int(total / self._page_size) + 1

        for _ in range(1, pages):
            hits = self._fetch_next_page(scroll_id)
            all_hits.extend(hits)
            if not hits:
                break

        results = []
        for hit in all_hits:
            source = hit.get("_source", {}).get("message", "")
            parsed = FailedMessagePush.parse_from_log_line(source)
            if parsed is not None:
                results.append(parsed)
        return results

    def _fetch_first_page(
        self, query: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str, int]:
        url = f"{self._base_url}?path=_search%3Fscroll%3D{self._keep_alive}&method=POST"
        resp = self._session.post(url, headers=self._headers, json=query, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        hits = data["hits"]["hits"]
        scroll_id = data["_scroll_id"]
        total = data["hits"]["total"]["value"]
        return hits, scroll_id, total

    def _fetch_next_page(self, scroll_id: str) -> list[dict[str, Any]]:
        url = f"{self._base_url}?path=_search/scroll&method=POST"
        body = {"scroll": self._keep_alive, "scroll_id": scroll_id}
        resp = self._session.post(url, headers=self._headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["hits"]["hits"]

    def _build_search_query(
        self, from_dt: datetime, to_dt: datetime
    ) -> dict[str, Any]:
        return {
            "version": True,
            "track_total_hits": True,
            "size": self._page_size,
            "sort": [{"@timestamp": {"order": "asc", "unmapped_type": "boolean"}}],
            "query": {
                "bool": {
                    "must": [],
                    "filter": [
                        {
                            "bool": {
                                "should": [
                                    {"match_phrase": {"message": "Failed to send message to "}}
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                        {
                            "range": {
                                "@timestamp": {
                                    "format": "strict_date_optional_time",
                                    "gte": from_dt.isoformat(),
                                    "lte": to_dt.isoformat(),
                                }
                            }
                        },
                    ],
                    "should": [],
                    "must_not": [],
                }
            },
        }


# ---------------------------------------------------------------------------
# User repository
# ---------------------------------------------------------------------------

class UserRepository:
    """Loads user records from the platform database."""

    USER_BY_IDS_SQL = """
        SELECT u.id, u.external_id, ui.email, u.affiliateid AS brand_id
        FROM platform.users u
        LEFT JOIN platform.user_info ui ON ui.userid = u.id
        WHERE u.id = ANY(:ids)
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_users_by_id_list(self, ids: list[int]) -> list[UserRecord]:
        if not ids:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(self.USER_BY_IDS_SQL), {"ids": ids}
            ).mappings()
            return [
                UserRecord(
                    id=r["id"],
                    external_id=str(r["external_id"]),
                    email=r.get("email"),
                    brand_id=r.get("brand_id"),
                )
                for r in rows
            ]


# ---------------------------------------------------------------------------
# Kafka re-publisher
# ---------------------------------------------------------------------------

class KafkaRepublisher:
    """Synchronously publishes messages to Kafka topics."""

    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def send(self, topic: str, key: str, value: bytes) -> None:
        """Synchronous send -- waits for delivery confirmation."""
        result: list[Exception | None] = [None]

        def delivery_callback(err: Any, msg: Any) -> None:
            if err:
                result[0] = Exception(str(err))

        self._producer.produce(topic, key=key.encode(), value=value, callback=delivery_callback)
        self._producer.flush()
        if result[0]:
            raise result[0]

    def flush(self) -> None:
        self._producer.flush()


# ---------------------------------------------------------------------------
# Main processor
# ---------------------------------------------------------------------------

class BalanceMismatchProcessor:
    """
    Orchestrates the balance mismatch remediation flow.

    For each user in the ID list:
      - Filter fetched messages to that user's events
      - Sort by timestamp
      - Re-publish in order to Kafka
    """

    def __init__(
        self,
        elastic_client: ElasticsearchClient,
        user_repository: UserRepository,
        kafka_republisher: KafkaRepublisher,
        dry_run: bool = True,
    ) -> None:
        self._elastic = elastic_client
        self._users = user_repository
        self._kafka = kafka_republisher
        self._dry_run = dry_run

    def run(
        self,
        from_dt: datetime,
        to_dt: datetime,
        user_id_list: list[int],
    ) -> None:
        if self._dry_run:
            log.info("balance_mismatch.dry_run", from_dt=from_dt, to_dt=to_dt)
        else:
            log.info("balance_mismatch.processing", from_dt=from_dt, to_dt=to_dt)

        raw_messages = self._elastic.fetch(from_dt, to_dt)
        log.info("balance_mismatch.fetched", count=len(raw_messages))

        users = self._users.get_users_by_id_list(user_id_list)
        log.info(
            "balance_mismatch.users_loaded",
            found=len(users),
            requested=len(user_id_list),
        )

        # Separate messages by type and parse timestamps
        accounts_events: list[tuple[FailedMessagePush, datetime]] = []
        round_events: list[tuple[FailedMessagePush, datetime]] = []
        failed = 0

        for msg in raw_messages:
            ts = self._extract_timestamp(msg.message)
            if ts is None:
                failed += 1
                continue
            if msg.message.startswith("AccountsEvent("):
                accounts_events.append((msg, ts))
            elif msg.message.startswith("RoundPlayedEvent("):
                round_events.append((msg, ts))
            else:
                log.warning("balance_mismatch.unknown_event_type", message=msg.message[:80])

        log.info(
            "balance_mismatch.parsed",
            accounts_events=len(accounts_events),
            round_events=len(round_events),
            failed=failed,
        )

        for user in users:
            user_ae = [(m, ts) for m, ts in accounts_events if self._is_accounts_event_for_user(m.message, user.id)]
            user_rpe = [(m, ts) for m, ts in round_events if self._is_round_event_for_user(m.message, user.external_id)]

            log.info(
                "balance_mismatch.user",
                user_id=user.id,
                external_id=user.external_id,
                accounts_events=len(user_ae),
                round_events=len(user_rpe),
                dry_run=self._dry_run,
            )

            if self._dry_run:
                continue

            for msg, _ts in sorted(user_ae, key=lambda x: x[1]):
                self._kafka.send(msg.topic, str(user.id), msg.message.encode())

            for msg, _ts in sorted(user_rpe, key=lambda x: x[1]):
                self._kafka.send(msg.topic, str(user.id), msg.message.encode())

        log.info("balance_mismatch.done")

    def _extract_timestamp(self, message: str) -> datetime | None:
        """
        Extract an ISO timestamp from a serialised event string.
        Events embed the timestamp in the CoreEventInfo field.
        """
        match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", message)
        if match:
            try:
                return datetime.fromisoformat(match.group(1))
            except ValueError:
                pass
        return None

    def _is_accounts_event_for_user(self, message: str, user_id: int) -> bool:
        return f"userId={user_id}" in message or f",{user_id}," in message

    def _is_round_event_for_user(self, message: str, external_id: str) -> bool:
        return external_id in message
