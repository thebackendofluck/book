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
Tests for the Fraud Detection Data Ingestion Service
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from src.data_ingestion.app import app
from src.data_ingestion.validation import DataValidator
from src.data_ingestion.enrichment import DataEnricher
from src.data_ingestion.metrics import MetricsCollector


class TestDataValidation:
    """Test data validation functionality"""

    def setup_method(self):
        self.validator = DataValidator()

    def test_valid_transaction(self):
        """Test validation of a valid transaction"""

        transaction = {
            "player_id": "player_123",
            "amount": 100.50,
            "currency": "USD",
            "transaction_type": "deposit",
            "payment_method": "credit_card",
            "timestamp": "2024-01-15T10:30:00Z"
        }

        result = self.validator.validate_transaction(transaction)

        assert result["valid"] is True
        assert result["errors"] == []

    def test_invalid_transaction_missing_fields(self):
        """Test validation of transaction with missing required fields"""

        transaction = {
            "amount": 100.50,
            "currency": "USD"
        }

        result = self.validator.validate_transaction(transaction)

        assert result["valid"] is False
        assert len(result["errors"]) == 2
        assert "Missing required field: player_id" in result["errors"]
        assert "Missing required field: transaction_type" in result["errors"]

    def test_invalid_transaction_amount(self):
        """Test validation of transaction with invalid amount"""

        transaction = {
            "player_id": "player_123",
            "amount": -50.0,
            "currency": "USD",
            "transaction_type": "deposit"
        }

        result = self.validator.validate_transaction(transaction)

        assert result["valid"] is False
        assert "Invalid amount: must be positive number" in result["errors"]

    def test_valid_user_event(self):
        """Test validation of a valid user event"""

        user_event = {
            "player_id": "player_123",
            "event_type": "login",
            "session_id": "session_456",
            "timestamp": "2024-01-15T10:30:00Z"
        }

        result = self.validator.validate_user_event(user_event)

        assert result["valid"] is True
        assert result["errors"] == []

    def test_invalid_user_event_type(self):
        """Test validation of user event with invalid event type"""

        user_event = {
            "player_id": "player_123",
            "event_type": "invalid_event",
            "timestamp": "2024-01-15T10:30:00Z"
        }

        result = self.validator.validate_user_event(user_event)

        assert result["valid"] is False
        assert "Invalid event_type: invalid_event" in result["errors"]

    def test_valid_game_event(self):
        """Test validation of a valid game event"""

        game_event = {
            "player_id": "player_123",
            "game_type": "slots",
            "game_session_id": "game_456",
            "event_type": "spin",
            "bet_amount": 10.0,
            "timestamp": "2024-01-15T10:30:00Z"
        }

        result = self.validator.validate_game_event(game_event)

        assert result["valid"] is True
        assert result["errors"] == []


class TestDataEnrichment:
    """Test data enrichment functionality"""

    def setup_method(self):
        self.enricher = DataEnricher()

    @pytest.mark.asyncio
    async def test_transaction_enrichment(self):
        """Test transaction data enrichment"""

        transaction = {
            "player_id": "player_123",
            "amount": 100.50,
            "currency": "USD",
            "transaction_type": "deposit",
            "ip_address": "8.8.8.8"
        }

        # Mock the IP geolocation call
        with patch.object(self.enricher, '_enrich_ip_geolocation', new_callable=AsyncMock) as mock_geo:
            mock_geo.return_value = {
                "country": "US",
                "city": "Mountain View",
                "latitude": 37.3860,
                "longitude": -122.0840
            }

            enriched = await self.enricher.enrich_transaction(transaction)

            assert "location_data" in enriched
            assert enriched["location_data"]["country"] == "US"
            assert "risk_indicators" in enriched
            assert "ingested_at" in enriched

    @pytest.mark.asyncio
    async def test_user_event_enrichment(self):
        """Test user event data enrichment"""

        user_event = {
            "player_id": "player_123",
            "event_type": "login",
            "ip_address": "8.8.8.8"
        }

        enriched = await self.enricher.enrich_user_event(user_event)

        assert "behavioral_indicators" in enriched
        assert "ingested_at" in enriched

    @pytest.mark.asyncio
    async def test_game_event_enrichment(self):
        """Test game event data enrichment"""

        game_event = {
            "player_id": "player_123",
            "game_type": "slots",
            "game_session_id": "game_456",
            "event_type": "spin"
        }

        enriched = await self.enricher.enrich_game_event(game_event)

        assert "game_risk_indicators" in enriched
        assert "ingested_at" in enriched


class TestMetricsCollector:
    """Test metrics collection functionality"""

    def setup_method(self):
        self.metrics = MetricsCollector()

    def test_counter_increment(self):
        """Test counter metric increment"""

        self.metrics.increment_counter("events_ingested_total", {"type": "transaction"}, 5)
        self.metrics.increment_counter("events_ingested_total", {"type": "user_event"}, 3)

        # Check that counters are updated
        txn_count = self.metrics.get_counter_value("events_ingested_total", {"type": "transaction"})
        user_count = self.metrics.get_counter_value("events_ingested_total", {"type": "user_event"})

        assert txn_count == 5
        assert user_count == 3

    def test_histogram_observation(self):
        """Test histogram metric observation"""

        self.metrics.observe_histogram("event_processing_duration_seconds", 0.1, {"type": "transaction"})
        self.metrics.observe_histogram("event_processing_duration_seconds", 0.2, {"type": "transaction"})
        self.metrics.observe_histogram("event_processing_duration_seconds", 0.15, {"type": "transaction"})

        avg_duration = self.metrics.get_histogram_avg("event_processing_duration_seconds", {"type": "transaction"})
        assert abs(avg_duration - 0.15) < 0.01  # Should be approximately 0.15

    def test_gauge_setting(self):
        """Test gauge metric setting"""

        self.metrics.set_gauge("active_connections", 10)
        self.metrics.set_gauge("queue_size", 5, {"queue_type": "transactions"})

        # Gauges are stored in Prometheus registry, not in our in-memory metrics
        # This test mainly ensures no exceptions are raised

    def test_summary_stats(self):
        """Test summary statistics generation"""

        # Add some test data
        self.metrics.increment_counter("events_ingested_total", {"type": "transaction"}, 100)
        self.metrics.increment_counter("events_ingested_total", {"type": "user_event"}, 50)
        self.metrics.increment_counter("ingestion_errors_total", {"type": "transaction"}, 2)

        stats = self.metrics.get_summary_stats()

        assert stats["events_ingested"]["total"] == 150
        assert stats["errors"]["total"] == 2
        assert len(stats["events_ingested"]["by_type"]) == 2


class TestAPIEndpoints:
    """Test API endpoints"""

    def setup_method(self):
        self.transport = ASGITransport(app=app)

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test health check endpoint"""

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_ready_endpoint(self):
        """Test readiness endpoint"""

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.get("/ready")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ready"

    @pytest.mark.asyncio
    @patch('src.data_ingestion.kafka_producer.KafkaEventProducer.send_event')
    async def test_ingest_transaction_success(self, mock_send):
        """Test successful transaction ingestion"""

        mock_send.return_value = None  # Simulate successful send

        transaction = {
            "player_id": "player_123",
            "amount": 100.50,
            "currency": "USD",
            "transaction_type": "deposit"
        }

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.post("/api/v1/transactions",
                                       json=transaction,
                                       headers={"Content-Type": "application/json"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"
            assert "event_id" in data

    @pytest.mark.asyncio
    async def test_ingest_transaction_validation_error(self):
        """Test transaction ingestion with validation error"""

        transaction = {
            "amount": 100.50,
            "currency": "USD"
            # Missing required fields
        }

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.post("/api/v1/transactions",
                                       json=transaction,
                                       headers={"Content-Type": "application/json"})

            assert response.status_code == 400
            data = response.json()
            assert "detail" in data

    @pytest.mark.asyncio
    @patch('src.data_ingestion.kafka_producer.KafkaEventProducer.send_event')
    async def test_ingest_user_event_success(self, mock_send):
        """Test successful user event ingestion"""

        mock_send.return_value = None

        user_event = {
            "player_id": "player_123",
            "event_type": "login"
        }

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.post("/api/v1/user-events",
                                       json=user_event,
                                       headers={"Content-Type": "application/json"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"

    @pytest.mark.asyncio
    @patch('src.data_ingestion.kafka_producer.KafkaEventProducer.send_event')
    async def test_ingest_game_event_success(self, mock_send):
        """Test successful game event ingestion"""

        mock_send.return_value = None

        game_event = {
            "player_id": "player_123",
            "game_type": "slots",
            "game_session_id": "game_456",
            "event_type": "spin"
        }

        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            response = await client.post("/api/v1/game-events",
                                       json=game_event,
                                       headers={"Content-Type": "application/json"})

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"


class TestIntegration:
    """Integration tests"""

    @pytest.mark.asyncio
    @patch('src.data_ingestion.kafka_producer.KafkaEventProducer')
    async def test_full_ingestion_flow(self, mock_producer_class):
        """Test full ingestion flow"""

        # Mock the producer
        mock_producer = MagicMock()
        mock_producer.send_event = AsyncMock()
        mock_producer_class.return_value = mock_producer

        # Test data
        transaction = {
            "player_id": "player_123",
            "amount": 100.50,
            "currency": "USD",
            "transaction_type": "deposit",
            "ip_address": "8.8.8.8"
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/transactions",
                                       json=transaction,
                                       headers={"Content-Type": "application/json"})

            assert response.status_code == 200

            # Verify Kafka producer was called
            mock_producer.send_event.assert_called_once()
            call_args = mock_producer.send_event.call_args
            assert call_args[0][0] == "transactions"  # topic
            assert call_args[0][2] == "player_123"    # key

            # Verify enriched data was sent
            sent_data = call_args[0][1]
            assert "location_data" in sent_data
            assert "risk_indicators" in sent_data
            assert "ingested_at" in sent_data


if __name__ == "__main__":
    pytest.main([__file__])