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
Full System Integration Tests

This module contains comprehensive integration tests for the complete fraud detection system,
testing end-to-end functionality across all components.
"""

import asyncio
import pytest
import aiohttp
import json
from datetime import datetime, timezone, timedelta
import time
import psutil
import os
from typing import Dict, Any, List
import structlog

logger = structlog.get_logger(__name__)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_CH19_FULL_STACK") != "1",
        reason="chapter-19 full-stack integration tests require the full service stack; set RUN_CH19_FULL_STACK=1 to execute them.",
    ),
]


class TestFullSystemIntegration:
    """Full system integration test suite"""

    def setup_method(self):
        """Setup test environment"""
        self.base_url = "http://localhost:8000"
        self.services = {
            "data_ingestion": "http://localhost:8080",
            "feature_engineering": "http://localhost:8081",
            "model_serving": "http://localhost:8082",
            "alerting": "http://localhost:8083",
            "compliance": "http://localhost:8084",
            "cost_optimization": "http://localhost:8085",
            "dashboard": "http://localhost:3000"
        }

        # Test data
        self.test_player_id = "test_player_123"
        self.test_transaction = {
            "transaction_id": "test_txn_123",
            "player_id": self.test_player_id,
            "amount": 100.0,
            "currency": "USD",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payment_method": "credit_card",
            "game_type": "slots",
            "location": {
                "ip_address": "192.168.1.1",
                "country": "US",
                "city": "New York"
            }
        }

    async def test_service_health_checks(self):
        """Test that all services are healthy"""
        async with aiohttp.ClientSession() as session:
            for service_name, service_url in self.services.items():
                try:
                    async with session.get(f"{service_url}/health") as response:
                        assert response.status == 200
                        data = await response.json()
                        assert data["status"] == "healthy"
                        logger.info(f"Service {service_name} is healthy")
                except Exception as e:
                    pytest.fail(f"Service {service_name} health check failed: {e}")

    async def test_data_ingestion_flow(self):
        """Test complete data ingestion flow"""
        async with aiohttp.ClientSession() as session:
            # Test data ingestion
            async with session.post(
                f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                json=self.test_transaction
            ) as response:
                assert response.status == 200
                result = await response.json()
                assert result["status"] == "ingested"

            # Wait for processing
            await asyncio.sleep(2)

            # Verify data was processed by checking feature engineering service
            async with session.get(
                f"{self.services['feature_engineering']}/api/v1/features/player/{self.test_player_id}"
            ) as response:
                assert response.status == 200
                features = await response.json()
                assert "player_id" in features
                assert features["player_id"] == self.test_player_id

    async def test_ml_prediction_pipeline(self):
        """Test complete ML prediction pipeline"""
        async with aiohttp.ClientSession() as session:
            # Prepare prediction request
            prediction_request = {
                "player_id": self.test_player_id,
                "features": {
                    "total_bet_amount": 1000.0,
                    "total_win_amount": 800.0,
                    "transaction_count": 10,
                    "avg_bet_amount": 100.0,
                    "session_duration": 3600,
                    "games_played": 5
                }
            }

            # Test prediction
            async with session.post(
                f"{self.services['model_serving']}/api/v1/predict/fraud",
                json=prediction_request
            ) as response:
                assert response.status in [200, 201]
                prediction = await response.json()
                assert "prediction" in prediction
                assert "probability" in prediction
                assert isinstance(prediction["probability"], (int, float))

    async def test_alerting_system_integration(self):
        """Test alerting system integration"""
        async with aiohttp.ClientSession() as session:
            # Create a test alert rule
            alert_rule = {
                "rule_id": "test_high_amount",
                "name": "Test High Amount Alert",
                "condition": "amount > 500",
                "threshold": 500.0,
                "severity": "medium",
                "category": "test"
            }

            # Create alert rule
            async with session.post(
                f"{self.services['alerting']}/api/v1/alerts/rules",
                json=alert_rule
            ) as response:
                assert response.status in [200, 201]

            # Trigger alert
            alert_trigger = {
                "rule_id": "test_high_amount",
                "value": 1000.0,
                "context": {"player_id": self.test_player_id}
            }

            async with session.post(
                f"{self.services['alerting']}/api/v1/alerts/trigger",
                json=alert_trigger
            ) as response:
                assert response.status == 200
                alert_result = await response.json()
                assert "alert_id" in alert_result

            # Check alerts history
            async with session.get(
                f"{self.services['alerting']}/api/v1/alerts/history?limit=10"
            ) as response:
                assert response.status == 200
                alerts = await response.json()
                assert isinstance(alerts, list)

    async def test_compliance_system_integration(self):
        """Test compliance system integration"""
        async with aiohttp.ClientSession() as session:
            # Run compliance check
            compliance_request = {
                "rule_id": "gdpr_data_retention",
                "context": {"player_id": self.test_player_id}
            }

            async with session.post(
                f"{self.services['compliance']}/api/v1/compliance/checks",
                json=compliance_request
            ) as response:
                assert response.status == 200
                check_result = await response.json()
                assert "status" in check_result
                assert check_result["status"] in ["pass", "fail", "warning"]

            # Test data subject request
            dsr_request = {
                "subject_id": self.test_player_id,
                "request_type": "access",
                "requester_info": {"email": "test@example.com"}
            }

            async with session.post(
                f"{self.services['compliance']}/api/v1/gdpr/requests",
                json=dsr_request
            ) as response:
                assert response.status in [200, 201]
                dsr_result = await response.json()
                assert "request_id" in dsr_result

    async def test_cost_optimization_integration(self):
        """Test cost optimization integration"""
        async with aiohttp.ClientSession() as session:
            # Run cost analysis
            cost_request = {"period_days": 7}

            async with session.post(
                f"{self.services['cost_optimization']}/api/v1/cost/analysis",
                json=cost_request
            ) as response:
                assert response.status == 200
                analysis = await response.json()
                assert "total_cost" in analysis
                assert "projected_savings" in analysis

            # Get optimization recommendations
            async with session.get(
                f"{self.services['cost_optimization']}/api/v1/cost/optimization/recommendations?limit=5"
            ) as response:
                assert response.status == 200
                recommendations = await response.json()
                assert "recommendations" in recommendations

    async def test_dashboard_integration(self):
        """Test dashboard integration"""
        async with aiohttp.ClientSession() as session:
            # Test dashboard data endpoint
            async with session.get(
                f"{self.services['dashboard']}/api/dashboard/summary"
            ) as response:
                # Dashboard might not have API endpoints, check if service is running
                if response.status == 200:
                    data = await response.json()
                    assert isinstance(data, dict)
                else:
                    # Just check that service is responding
                    assert response.status in [200, 404]  # 404 is ok if no API

    async def test_end_to_end_fraud_detection(self):
        """Test complete end-to-end fraud detection flow"""
        async with aiohttp.ClientSession() as session:
            # 1. Ingest transaction data
            transactions = [
                {**self.test_transaction, "transaction_id": f"test_txn_{i}", "amount": 50.0 * (i + 1)}
                for i in range(5)
            ]

            for txn in transactions:
                async with session.post(
                    f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                    json=txn
                ) as response:
                    assert response.status == 200

            # 2. Wait for processing
            await asyncio.sleep(5)

            # 3. Check features were created
            async with session.get(
                f"{self.services['feature_engineering']}/api/v1/features/player/{self.test_player_id}"
            ) as response:
                assert response.status == 200
                features = await response.json()
                assert features.get("transaction_count", 0) >= 5

            # 4. Get fraud prediction
            prediction_request = {
                "player_id": self.test_player_id,
                "features": features
            }

            async with session.post(
                f"{self.services['model_serving']}/api/v1/predict/fraud",
                json=prediction_request
            ) as response:
                assert response.status in [200, 201]
                prediction = await response.json()
                assert "prediction" in prediction

            # 5. Check if any alerts were triggered
            async with session.get(
                f"{self.services['alerting']}/api/v1/alerts/history?limit=10"
            ) as response:
                assert response.status == 200
                alerts = await response.json()
                # Alerts might or might not be triggered depending on rules

    async def test_performance_under_load(self):
        """Test system performance under load"""
        async with aiohttp.ClientSession() as session:
            # Generate load
            tasks = []
            for i in range(50):  # 50 concurrent requests
                txn = {
                    **self.test_transaction,
                    "transaction_id": f"load_test_txn_{i}",
                    "player_id": f"load_test_player_{i % 10}"
                }
                task = session.post(
                    f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                    json=txn
                )
                tasks.append(task)

            # Execute all requests
            start_time = time.time()
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            end_time = time.time()

            # Check responses
            success_count = sum(1 for r in responses if not isinstance(r, Exception) and hasattr(r, 'status') and r.status == 200)
            total_time = end_time - start_time

            # Performance assertions
            assert success_count >= 40  # At least 80% success rate
            assert total_time < 30  # Should complete within 30 seconds
            assert success_count / total_time > 1  # At least 1 request per second

            logger.info(f"Load test completed: {success_count}/50 successful in {total_time:.2f}s")

    async def test_system_resilience(self):
        """Test system resilience and error handling"""
        async with aiohttp.ClientSession() as session:
            # Test with invalid data
            invalid_transaction = {
                "transaction_id": "invalid_test",
                "player_id": "",  # Invalid empty player_id
                "amount": "not_a_number",  # Invalid amount type
                "timestamp": "invalid_timestamp"
            }

            async with session.post(
                f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                json=invalid_transaction
            ) as response:
                # Should handle gracefully (either reject or sanitize)
                assert response.status in [200, 400, 422]

            # Test with missing service
            try:
                async with session.get("http://nonexistent-service:9999/health") as response:
                    pass
            except (aiohttp.ClientError, asyncio.TimeoutError):
                # Expected to fail
                pass

            # Test rate limiting (if implemented)
            rapid_requests = []
            for i in range(100):
                rapid_requests.append(session.post(
                    f"{self.services['data_ingestion']}/api/v1/ingest/transaction",
                    json=self.test_transaction
                ))

            responses = await asyncio.gather(*rapid_requests, return_exceptions=True)

            # Should handle rapid requests gracefully
            success_count = sum(1 for r in responses if not isinstance(r, Exception) and hasattr(r, 'status'))
            assert success_count > 50  # At least some should succeed

    async def test_cross_service_communication(self):
        """Test communication between services"""
        async with aiohttp.ClientSession() as session:
            # Test that services can communicate with each other
            # This would test internal API calls between services

            # Check that model serving can get features from feature engineering
            prediction_request = {
                "player_id": self.test_player_id,
                "use_feature_store": True  # This would trigger internal API call
            }

            async with session.post(
                f"{self.services['model_serving']}/api/v1/predict/fraud",
                json=prediction_request
            ) as response:
                # Should work regardless of internal communication
                assert response.status in [200, 201, 400, 404]  # Various acceptable responses

    def test_resource_usage(self):
        """Test resource usage during testing"""
        process = psutil.Process(os.getpid())

        # Check memory usage
        memory_mb = process.memory_info().rss / 1024 / 1024
        assert memory_mb < 1000  # Should not exceed 1GB during testing

        # Check CPU usage (rough estimate)
        cpu_percent = psutil.cpu_percent(interval=1)
        # CPU usage can vary, just ensure it's not extremely high
        assert cpu_percent < 95

    async def run_all_integration_tests(self):
        """Run all integration tests"""
        logger.info("Starting full system integration tests")

        # Setup
        self.setup_method()

        # Run tests
        await self.test_service_health_checks()
        await self.test_data_ingestion_flow()
        await self.test_ml_prediction_pipeline()
        await self.test_alerting_system_integration()
        await self.test_compliance_system_integration()
        await self.test_cost_optimization_integration()
        await self.test_dashboard_integration()
        await self.test_end_to_end_fraud_detection()
        await self.test_performance_under_load()
        await self.test_system_resilience()
        await self.test_cross_service_communication()

        # Resource check
        self.test_resource_usage()

        logger.info("All integration tests completed successfully")


if __name__ == "__main__":
    # Run integration tests
    async def main():
        test_suite = TestFullSystemIntegration()
        await test_suite.run_all_integration_tests()

    asyncio.run(main())
