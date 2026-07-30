#!/usr/bin/env python3
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
Sample Data Ingestion Script

Ingests sample data into the fraud detection system for testing and demonstration.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Any
import httpx
import structlog

logger = structlog.get_logger(__name__)


class SampleDataIngester:
    """Ingests sample data into the fraud detection system"""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def ingest_transaction(self, transaction: Dict[str, Any]) -> bool:
        """Ingest a single transaction"""

        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/transactions",
                json=transaction,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                logger.info("Transaction ingested successfully",
                          event_id=result.get("event_id"))
                return True
            else:
                logger.error("Failed to ingest transaction",
                           status_code=response.status_code,
                           response=response.text)
                return False

        except Exception as e:
            logger.error("Error ingesting transaction", error=str(e))
            return False

    async def ingest_user_event(self, user_event: Dict[str, Any]) -> bool:
        """Ingest a single user event"""

        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/user-events",
                json=user_event,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                logger.info("User event ingested successfully",
                          event_id=result.get("event_id"))
                return True
            else:
                logger.error("Failed to ingest user event",
                           status_code=response.status_code,
                           response=response.text)
                return False

        except Exception as e:
            logger.error("Error ingesting user event", error=str(e))
            return False

    async def ingest_game_event(self, game_event: Dict[str, Any]) -> bool:
        """Ingest a single game event"""

        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/game-events",
                json=game_event,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                logger.info("Game event ingested successfully",
                          event_id=result.get("event_id"))
                return True
            else:
                logger.error("Failed to ingest game event",
                           status_code=response.status_code,
                           response=response.text)
                return False

        except Exception as e:
            logger.error("Error ingesting game event", error=str(e))
            return False

    async def ingest_bulk_events(self, events: List[Dict[str, Any]],
                               batch_size: int = 100) -> Dict[str, Any]:
        """Ingest events in bulk"""

        results = {
            "total_events": len(events),
            "processed_events": 0,
            "errors": 0,
            "start_time": time.time()
        }

        # Process in batches
        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]

            try:
                response = await self.client.post(
                    f"{self.base_url}/api/v1/bulk-events",
                    json=batch,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200:
                    batch_result = response.json()
                    results["processed_events"] += batch_result.get("processed_events", 0)
                    results["errors"] += batch_result.get("errors", 0)

                    logger.info("Batch processed",
                              batch_start=i,
                              batch_end=min(i + batch_size, len(events)),
                              processed=batch_result.get("processed_events", 0),
                              errors=batch_result.get("errors", 0))
                else:
                    logger.error("Failed to process batch",
                               batch_start=i,
                               batch_end=min(i + batch_size, len(events)),
                               status_code=response.status_code,
                               response=response.text)
                    results["errors"] += len(batch)

            except Exception as e:
                logger.error("Error processing batch",
                           batch_start=i,
                           batch_end=min(i + batch_size, len(events)),
                           error=str(e))
                results["errors"] += len(batch)

            # Small delay between batches to avoid overwhelming the system
            await asyncio.sleep(0.1)

        results["end_time"] = time.time()
        results["duration_seconds"] = results["end_time"] - results["start_time"]
        results["events_per_second"] = results["processed_events"] / results["duration_seconds"] if results["duration_seconds"] > 0 else 0

        return results

    async def load_and_ingest_file(self, file_path: str,
                                 event_type: str = "auto") -> Dict[str, Any]:
        """Load data from file and ingest it"""

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        logger.info("Loading data from file", file_path=str(path))

        # Load JSON data
        with open(path, 'r') as f:
            data = json.load(f)

        results: Dict[str, Any] = {
            "file_path": str(file_path),
            "transactions": {"total": 0, "success": 0, "errors": 0},
            "user_events": {"total": 0, "success": 0, "errors": 0},
            "game_events": {"total": 0, "success": 0, "errors": 0}
        }

        # Ingest transactions
        if "transactions" in data and data["transactions"]:
            results["transactions"]["total"] = len(data["transactions"])
            logger.info("Ingesting transactions",
                       count=len(data["transactions"]))

            for transaction in data["transactions"]:
                success = await self.ingest_transaction(transaction)
                if success:
                    results["transactions"]["success"] += 1
                else:
                    results["transactions"]["errors"] += 1

        # Ingest user events
        if "user_events" in data and data["user_events"]:
            results["user_events"]["total"] = len(data["user_events"])
            logger.info("Ingesting user events",
                       count=len(data["user_events"]))

            for user_event in data["user_events"]:
                success = await self.ingest_user_event(user_event)
                if success:
                    results["user_events"]["success"] += 1
                else:
                    results["user_events"]["errors"] += 1

        # Ingest game events
        if "game_events" in data and data["game_events"]:
            results["game_events"]["total"] = len(data["game_events"])
            logger.info("Ingesting game events",
                       count=len(data["game_events"]))

            for game_event in data["game_events"]:
                success = await self.ingest_game_event(game_event)
                if success:
                    results["game_events"]["success"] += 1
                else:
                    results["game_events"]["errors"] += 1

        return results

    async def wait_for_service(self, timeout: int = 60) -> bool:
        """Wait for the service to be ready"""

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = await self.client.get(f"{self.base_url}/health")
                if response.status_code == 200:
                    health_data = response.json()
                    if health_data.get("status") == "healthy":
                        logger.info("Service is ready")
                        return True

                logger.debug("Service not ready yet, waiting...")
                await asyncio.sleep(2)

            except Exception as e:
                logger.debug("Service health check failed", error=str(e))
                await asyncio.sleep(2)

        logger.error("Service failed to become ready within timeout")
        return False

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


async def main():
    """Main ingestion function"""

    import argparse

    parser = argparse.ArgumentParser(description="Ingest sample data into fraud detection system")
    parser.add_argument("--file", type=str, help="JSON file containing sample data")
    parser.add_argument("--url", type=str, default="http://localhost:8080",
                       help="Base URL of the fraud detection service")
    parser.add_argument("--count", type=int, default=100,
                       help="Number of sample events to generate and ingest")
    parser.add_argument("--batch-size", type=int, default=50,
                       help="Batch size for bulk ingestion")
    parser.add_argument("--wait", action="store_true",
                       help="Wait for service to be ready before ingesting")

    args = parser.parse_args()

    ingester = SampleDataIngester(args.url)

    try:
        # Wait for service if requested
        if args.wait:
            logger.info("Waiting for service to be ready...")
            if not await ingester.wait_for_service():
                logger.error("Service failed to become ready")
                return 1

        if args.file:
            # Load and ingest from file
            logger.info("Ingesting data from file", file=args.file)
            results = await ingester.load_and_ingest_file(args.file)

            print("\nIngestion Results:")
            print(f"File: {results['file_path']}")
            print(f"Transactions: {results['transactions']['success']}/{results['transactions']['total']} successful")
            print(f"User Events: {results['user_events']['success']}/{results['user_events']['total']} successful")
            print(f"Game Events: {results['game_events']['success']}/{results['game_events']['total']} successful")

        else:
            # Generate and ingest sample data
            from generate_test_data import FraudDataGenerator

            logger.info("Generating sample data", count=args.count)
            generator = FraudDataGenerator()
            sample_data_json = generator.generate_bulk_data(args.count)
            sample_data = json.loads(sample_data_json)

            # Combine all events for bulk ingestion
            all_events = []
            all_events.extend(sample_data.get("transactions", []))
            all_events.extend(sample_data.get("user_events", []))
            all_events.extend(sample_data.get("game_events", []))

            logger.info("Ingesting sample data", total_events=len(all_events))
            results = await ingester.ingest_bulk_events(all_events, args.batch_size)

            print("\nBulk Ingestion Results:")
            print(f"Total Events: {results['total_events']}")
            print(f"Processed: {results['processed_events']}")
            print(f"Errors: {results['errors']}")
            print(".2f")
            print(".2f")

    except KeyboardInterrupt:
        logger.info("Ingestion interrupted by user")
    except Exception as e:
        logger.error("Ingestion failed", error=str(e))
        return 1
    finally:
        await ingester.close()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())