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
tests/test_account_history.py — Tests for the account history service.

Covers:
  - AccountEvent model: property validation
  - EventStore: append, batch append, query with filters
  - QueryService: transactions, sessions, game rounds, timeline
  - Aggregator: player stats, GGR by game, daily GGR, deposit frequency
  - FastAPI endpoints: events, transactions, sessions, game-rounds, stats
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aggregator import Aggregator
from event_store import EventStore
from main import app
from models import (
    AccountEvent,
    EventType,
    GameOutcome,
    GameRoundHistory,
    HistoryFilter,
    PaginatedResult,
    PlayerStats,
    SessionHistory,
    TransactionHistory,
    TransactionStatus,
)
from query_service import QueryService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_conn():
    """Return a MagicMock that looks like a psycopg2 connection."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__  = MagicMock(return_value=False)
    return conn


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def sample_event(now) -> AccountEvent:
    return AccountEvent(
        id=0,
        player_id=42,
        event_type=EventType.DEPOSIT,
        amount=10000.0,   # £100.00 in pence
        currency="GBP",
        occurred_at=now,
        reference="txn-001",
    )


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestModels:

    def test_account_event_fields(self, sample_event, now):
        assert sample_event.player_id == 42
        assert sample_event.event_type == EventType.DEPOSIT
        assert sample_event.amount == 10000.0
        assert sample_event.currency == "GBP"

    def test_game_round_ggr_win(self, now):
        round_ = GameRoundHistory(
            id=1, player_id=1, session_id=None,
            game_id="starburst", game_name="Starburst",
            bet_amount=500.0, win_amount=200.0,
            currency="GBP", outcome=GameOutcome.LOSS,
            started_at=now, ended_at=None,
        )
        assert round_.ggr == pytest.approx(300.0)

    def test_game_round_ggr_zero_on_push(self, now):
        round_ = GameRoundHistory(
            id=2, player_id=1, session_id=None,
            game_id="blackjack", game_name="Blackjack",
            bet_amount=1000.0, win_amount=1000.0,
            currency="GBP", outcome=GameOutcome.PUSH,
            started_at=now, ended_at=None,
        )
        assert round_.ggr == pytest.approx(0.0)

    def test_session_duration(self, now):
        ended = now + timedelta(minutes=45)
        session = SessionHistory(
            id=1, player_id=1, session_token="tok",
            started_at=now, ended_at=ended,
            ip_address=None, device_type=None, jurisdiction=None,
        )
        assert session.duration_seconds == pytest.approx(2700.0)

    def test_session_no_end_duration_is_none(self, now):
        session = SessionHistory(
            id=1, player_id=1, session_token="tok",
            started_at=now, ended_at=None,
            ip_address=None, device_type=None, jurisdiction=None,
        )
        assert session.duration_seconds is None

    def test_player_stats_net_deposits(self, now):
        stats = PlayerStats(
            player_id=1, from_date=now, to_date=now,
            total_deposits=50000.0, total_withdrawals=20000.0,
            total_bets=30000.0, total_wins=25000.0,
            bonus_awarded=1000.0, bonus_wagered=800.0,
            currency="GBP",
        )
        assert stats.net_deposits == pytest.approx(30000.0)
        assert stats.ggr == pytest.approx(5000.0)
        assert stats.ngr == pytest.approx(4000.0)

    def test_paginated_result_has_more(self):
        result = PaginatedResult(items=list(range(10)), total=25, limit=10, offset=0)
        assert result.has_more is True

    def test_paginated_result_no_more(self):
        result = PaginatedResult(items=list(range(5)), total=5, limit=10, offset=0)
        assert result.has_more is False


# ---------------------------------------------------------------------------
# EventStore tests
# ---------------------------------------------------------------------------

class TestEventStore:

    def _make_store_with_cursor(self, mock_conn, fetchone_val=None, fetchall_val=None):
        cursor_mock = MagicMock()
        cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
        cursor_mock.__exit__  = MagicMock(return_value=False)
        cursor_mock.fetchone.return_value = fetchone_val
        cursor_mock.fetchall.return_value = fetchall_val or []
        mock_conn.cursor.return_value = cursor_mock
        return EventStore(mock_conn), cursor_mock

    def test_append_returns_generated_id(self, mock_conn, sample_event):
        store, cursor = self._make_store_with_cursor(mock_conn, fetchone_val=(99,))
        event_id = store.append(sample_event)
        assert event_id == 99
        mock_conn.commit.assert_called_once()

    def test_append_batch_commits_once(self, mock_conn, sample_event, now):
        events = [
            AccountEvent(id=0, player_id=42, event_type=EventType.DEPOSIT,
                         amount=100.0, currency="GBP", occurred_at=now),
            AccountEvent(id=0, player_id=42, event_type=EventType.BET,
                         amount=50.0, currency="GBP", occurred_at=now),
        ]
        cursor_mock = MagicMock()
        cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
        cursor_mock.__exit__  = MagicMock(return_value=False)
        cursor_mock.fetchone.side_effect = [(1,), (2,)]
        mock_conn.cursor.return_value = cursor_mock

        store = EventStore(mock_conn)
        ids = store.append_batch(events)
        assert ids == [1, 2]
        mock_conn.commit.assert_called_once()

    def test_append_empty_batch_returns_empty(self, mock_conn):
        store = EventStore(mock_conn)
        ids = store.append_batch([])
        assert ids == []

    def test_get_by_id_returns_none_when_not_found(self, mock_conn):
        store, _ = self._make_store_with_cursor(mock_conn, fetchone_val=None)
        result = store.get_by_id(999)
        assert result is None

    def test_get_player_events_filters_by_event_type(self, mock_conn, now):
        row = {
            "id": 1, "player_id": 42, "event_type": "deposit",
            "amount": "100.00", "currency": "GBP",
            "occurred_at": now, "reference": None, "metadata": None,
        }
        store, cursor = self._make_store_with_cursor(
            mock_conn, fetchall_val=[row])
        events = store.get_player_events(
            42, event_types=["deposit"], limit=10, offset=0)
        assert len(events) == 1
        assert events[0].event_type == EventType.DEPOSIT


# ---------------------------------------------------------------------------
# QueryService tests
# ---------------------------------------------------------------------------

class TestQueryService:

    def _make_query_service(self, mock_conn):
        store = MagicMock(spec=EventStore)
        return QueryService(store, mock_conn), store

    def _cursor_returning(self, mock_conn, count_row, data_rows):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__  = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"cnt": count_row}
        cursor.fetchall.return_value = data_rows
        mock_conn.cursor.return_value = cursor

    def test_get_transactions_returns_paginated(self, mock_conn, now):
        txn_row = {
            "id": 1, "player_id": 42, "transaction_type": "deposit",
            "amount": "10000", "currency": "GBP", "status": "completed",
            "initiated_at": now, "completed_at": now,
            "payment_method": "card", "external_ref": "psp-1",
        }
        self._cursor_returning(mock_conn, count_row=1, data_rows=[txn_row])

        qs, _ = self._make_query_service(mock_conn)
        f = HistoryFilter(player_id=42, limit=10, offset=0)
        result = qs.get_transactions(f)
        assert result.total == 1
        assert isinstance(result.items[0], TransactionHistory)

    def test_get_sessions_returns_paginated(self, mock_conn, now):
        session_row = {
            "id": 1, "player_id": 42, "session_token": "tok-1",
            "started_at": now, "ended_at": now + timedelta(hours=1),
            "ip_address": "1.2.3.4", "device_type": "desktop",
            "jurisdiction": "GB",
        }
        self._cursor_returning(mock_conn, count_row=1, data_rows=[session_row])

        qs, _ = self._make_query_service(mock_conn)
        f = HistoryFilter(player_id=42, limit=10, offset=0)
        result = qs.get_sessions(f)
        assert result.total == 1
        assert isinstance(result.items[0], SessionHistory)

    def test_get_game_rounds_returns_paginated(self, mock_conn, now):
        round_row = {
            "id": 1, "player_id": 42, "session_id": None,
            "game_id": "starburst", "game_name": "Starburst",
            "bet_amount": "500", "win_amount": "0",
            "currency": "GBP", "outcome": "loss",
            "started_at": now, "ended_at": now + timedelta(seconds=30),
            "round_ref": "round-1",
        }
        self._cursor_returning(mock_conn, count_row=1, data_rows=[round_row])

        qs, _ = self._make_query_service(mock_conn)
        f = HistoryFilter(player_id=42, limit=10, offset=0)
        result = qs.get_game_rounds(f)
        assert result.total == 1
        assert isinstance(result.items[0], GameRoundHistory)
        assert result.items[0].ggr == pytest.approx(500.0)

    def test_get_account_timeline_delegates_to_store(self, mock_conn, now):
        qs, store_mock = self._make_query_service(mock_conn)
        sample = AccountEvent(id=1, player_id=42, event_type=EventType.WIN,
                              amount=200.0, currency="GBP", occurred_at=now)
        store_mock.get_player_events.return_value = [sample]
        store_mock.count_player_events.return_value = 1

        f = HistoryFilter(player_id=42, limit=10, offset=0)
        result = qs.get_account_timeline(f)
        assert result.total == 1
        assert result.items[0].event_type == EventType.WIN


# ---------------------------------------------------------------------------
# Aggregator tests
# ---------------------------------------------------------------------------

class TestAggregator:

    def _agg_with_cursor(self, mock_conn, responses):
        """
        Set up cursor to return different responses on successive fetchone calls.
        responses: list of dicts, one per SQL query.
        """
        call_count = [0]
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__  = MagicMock(return_value=False)

        def next_row():
            r = responses[call_count[0]]
            call_count[0] += 1
            return r
        cursor.fetchone.side_effect = next_row
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor
        return Aggregator(mock_conn)

    def test_player_stats_computes_ggr(self, mock_conn, now):
        agg = self._agg_with_cursor(mock_conn, [
            # transaction_stats query
            {"deposits": "50000", "withdrawals": "10000",
             "bonus_awarded": "500", "bonus_wagered": "400"},
            # game_round_stats query
            {"total_bets": "30000", "total_wins": "20000"},
            # session_stats query
            {"session_count": "15", "total_play_time_seconds": "54000"},
        ])
        stats = agg.get_player_stats(42, currency="GBP")
        assert stats.ggr == pytest.approx(10000.0)
        assert stats.ngr == pytest.approx(9500.0)
        assert stats.net_deposits == pytest.approx(40000.0)
        assert stats.session_count == 15

    def test_get_ggr_by_game(self, mock_conn):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__  = MagicMock(return_value=False)
        cursor.fetchall.return_value = [
            {"game_id": "slots-1", "game_name": "Book of Ra",
             "total_bets": "10000", "total_wins": "7000",
             "ggr": "3000", "round_count": "50"},
        ]
        mock_conn.cursor.return_value = cursor
        agg = Aggregator(mock_conn)
        results = agg.get_ggr_by_game(42)
        assert len(results) == 1
        assert results[0]["game_id"] == "slots-1"

    def test_get_deposit_frequency(self, mock_conn):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__  = MagicMock(return_value=False)
        cursor.fetchone.return_value = {
            "deposit_count": "10",
            "total_amount": "100000",
            "avg_amount": "10000",
            "min_amount": "5000",
            "max_amount": "20000",
        }
        mock_conn.cursor.return_value = cursor
        agg = Aggregator(mock_conn)
        freq = agg.get_deposit_frequency(42)
        assert freq["deposit_count"] == 10
        assert freq["avg_amount"] == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# FastAPI endpoint tests
# ---------------------------------------------------------------------------

class TestApiEndpoints:

    def test_health_returns_ok(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_append_event_returns_201(self, api_client, now):
        with patch("main._make_services") as mock_svc:
            store_mock = MagicMock()
            store_mock.append.return_value = 77
            mock_svc.return_value = (MagicMock(), store_mock, MagicMock(), MagicMock())

            resp = api_client.post("/events", json={
                "player_id":  42,
                "event_type": "deposit",
                "amount":     10000.0,
                "currency":   "GBP",
            })
        assert resp.status_code == 201
        assert resp.json()["event_id"] == 77

    def test_append_event_invalid_type_returns_422(self, api_client):
        with patch("main._make_services") as mock_svc:
            mock_svc.return_value = (MagicMock(), MagicMock(), MagicMock(), MagicMock())
            resp = api_client.post("/events", json={
                "player_id":  42,
                "event_type": "not_a_real_event",
                "amount":     100.0,
                "currency":   "GBP",
            })
        assert resp.status_code == 422

    def test_get_events_returns_paginated(self, api_client, now):
        event = AccountEvent(id=1, player_id=42, event_type=EventType.DEPOSIT,
                             amount=100.0, currency="GBP", occurred_at=now)
        paginated = PaginatedResult(items=[event], total=1, limit=100, offset=0)

        with patch("main._make_services") as mock_svc:
            query_mock = MagicMock()
            query_mock.get_account_timeline.return_value = paginated
            mock_svc.return_value = (MagicMock(), MagicMock(), query_mock, MagicMock())
            resp = api_client.get("/players/42/events")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_get_stats_returns_ggr(self, api_client, now):
        stats = PlayerStats(
            player_id=42, from_date=now, to_date=now,
            total_deposits=50000.0, total_withdrawals=10000.0,
            total_bets=30000.0, total_wins=20000.0,
            bonus_awarded=500.0, bonus_wagered=400.0,
            currency="GBP",
        )
        with patch("main._make_services") as mock_svc:
            agg_mock = MagicMock()
            agg_mock.get_player_stats.return_value = stats
            mock_svc.return_value = (MagicMock(), MagicMock(), MagicMock(), agg_mock)
            resp = api_client.get("/players/42/stats?currency=GBP")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ggr"] == pytest.approx(10000.0)
        assert data["net_deposits"] == pytest.approx(40000.0)

    def test_get_transactions_with_date_filter(self, api_client, now):
        paginated = PaginatedResult(items=[], total=0, limit=100, offset=0)
        with patch("main._make_services") as mock_svc:
            query_mock = MagicMock()
            query_mock.get_transactions.return_value = paginated
            mock_svc.return_value = (MagicMock(), MagicMock(), query_mock, MagicMock())
            resp = api_client.get(
                "/players/42/transactions"
                f"?from_date={now.isoformat()}"
                f"&to_date={now.isoformat()}"
            )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
