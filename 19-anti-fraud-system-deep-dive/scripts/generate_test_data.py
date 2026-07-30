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
Test Data Generator for Fraud Detection System

Generates realistic test data for transactions, user events, and game events
to support development, testing, and demonstration of the fraud detection system.
"""

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
import faker  # ty:ignore[unresolved-import]
import numpy as np


class FraudDataGenerator:
    """Generates realistic fraud detection test data"""

    def __init__(self, seed: Optional[int] = None):
        if seed:
            random.seed(seed)
            np.random.seed(seed)
            faker.Faker.seed(seed)

        self.fake = faker.Faker()

        # Data generation parameters
        self.player_ids = [f"player_{i:03d}" for i in range(1, 101)]  # 100 players
        self.game_types = ["slots", "blackjack", "roulette", "baccarat", "poker", "craps"]
        self.payment_methods = ["credit_card", "debit_card", "paypal", "bank_transfer", "crypto"]
        self.currencies = ["USD", "EUR", "GBP", "CAD"]
        self.countries = ["US", "CA", "GB", "DE", "FR", "AU", "JP"]

        # Fraud patterns
        self.fraud_patterns = {
            "normal": 0.85,      # 85% normal behavior
            "suspicious": 0.10,  # 10% suspicious behavior
            "fraudulent": 0.05   # 5% fraudulent behavior
        }

    def generate_transaction(self, player_id: str, timestamp: datetime,
                           pattern: str = "normal") -> Dict[str, Any]:
        """Generate a single transaction"""

        # Base amounts by pattern
        if pattern == "normal":
            amount = random.uniform(10, 500)
        elif pattern == "suspicious":
            amount = random.uniform(100, 2000)
        else:  # fraudulent
            amount = random.uniform(500, 10000)

        # Transaction type weights
        txn_types = ["bet", "deposit", "withdrawal", "win"]
        weights = [0.6, 0.2, 0.15, 0.05] if pattern == "normal" else [0.8, 0.1, 0.05, 0.05]
        txn_type = random.choices(txn_types, weights=weights)[0]

        transaction = {
            "event_id": str(uuid.uuid4()),
            "player_id": player_id,
            "amount": round(amount, 2),
            "currency": random.choice(self.currencies),
            "transaction_type": txn_type,
            "payment_method": random.choice(self.payment_methods) if txn_type in ["deposit", "withdrawal"] else None,
            "game_type": random.choice(self.game_types) if txn_type in ["bet", "win"] else None,
            "game_session_id": str(uuid.uuid4()) if txn_type in ["bet", "win"] else None,
            "external_transaction_id": f"ext_{random.randint(100000, 999999)}",
            "ip_address": self.fake.ipv4(),
            "user_agent": self.fake.user_agent(),
            "device_fingerprint": f"fp_{random.randint(1000000, 9999999)}",
            "location_data": {
                "country": random.choice(self.countries),
                "city": self.fake.city(),
                "latitude": round(random.uniform(-90, 90), 6),
                "longitude": round(random.uniform(-180, 180), 6)
            },
            "timestamp": timestamp.isoformat(),
            "metadata": {
                "user_segment": random.choice(["vip", "regular", "new"]),
                "campaign_id": f"camp_{random.randint(1, 10)}" if random.random() < 0.3 else None
            }
        }

        return transaction

    def generate_user_event(self, player_id: str, timestamp: datetime,
                          pattern: str = "normal") -> Dict[str, Any]:
        """Generate a single user event"""

        event_types = ["login", "logout", "page_view", "button_click", "game_start", "game_end"]
        weights = [0.2, 0.2, 0.3, 0.2, 0.05, 0.05]

        if pattern == "suspicious":
            # More button clicks and page views for suspicious behavior
            weights = [0.15, 0.15, 0.4, 0.25, 0.03, 0.02]

        event_type = random.choices(event_types, weights=weights)[0]

        event = {
            "event_id": str(uuid.uuid4()),
            "player_id": player_id,
            "event_type": event_type,
            "session_id": str(uuid.uuid4()),
            "page_url": self.fake.uri() if event_type == "page_view" else None,
            "element_id": f"btn_{random.randint(1, 100)}" if event_type == "button_click" else None,
            "game_type": random.choice(self.game_types) if event_type in ["game_start", "game_end"] else None,
            "game_session_id": str(uuid.uuid4()) if event_type in ["game_start", "game_end"] else None,
            "duration_seconds": random.randint(30, 3600) if event_type in ["game_end", "logout"] else None,
            "ip_address": self.fake.ipv4(),
            "user_agent": self.fake.user_agent(),
            "device_fingerprint": f"fp_{random.randint(1000000, 9999999)}",
            "location_data": {
                "country": random.choice(self.countries),
                "city": self.fake.city(),
                "latitude": round(random.uniform(-90, 90), 6),
                "longitude": round(random.uniform(-180, 180), 6)
            },
            "timestamp": timestamp.isoformat(),
            "event_data": self._generate_event_data(event_type)
        }

        return event

    def generate_game_event(self, player_id: str, timestamp: datetime,
                          pattern: str = "normal") -> Dict[str, Any]:
        """Generate a single game event"""

        game_type = random.choice(self.game_types)
        event_types = ["game_start", "game_end", "spin", "bet", "win", "loss", "bonus", "jackpot"]
        weights = [0.1, 0.1, 0.3, 0.25, 0.15, 0.05, 0.03, 0.02]

        if pattern == "fraudulent":
            # More wins and bonuses for fraudulent behavior
            weights = [0.08, 0.08, 0.25, 0.2, 0.25, 0.04, 0.08, 0.02]

        event_type = random.choices(event_types, weights=weights)[0]
        game_session_id = str(uuid.uuid4())

        event = {
            "event_id": str(uuid.uuid4()),
            "player_id": player_id,
            "game_type": game_type,
            "game_session_id": game_session_id,
            "event_type": event_type,
            "bet_amount": round(random.uniform(1, 100), 2) if event_type in ["bet", "spin"] else None,
            "win_amount": round(random.uniform(0, 1000), 2) if event_type in ["win", "bonus", "jackpot"] else None,
            "game_state": self._generate_game_state(game_type) if random.random() < 0.5 else None,
            "ip_address": self.fake.ipv4(),
            "device_fingerprint": f"fp_{random.randint(1000000, 9999999)}",
            "timestamp": timestamp.isoformat()
        }

        return event

    def _generate_event_data(self, event_type: str) -> Dict[str, Any]:
        """Generate event-specific data"""

        if event_type == "page_view":
            return {
                "page_category": random.choice(["casino", "sports", "live_dealer", "promotions"]),
                "time_on_page": random.randint(10, 300),
                "scroll_depth": random.uniform(0.1, 1.0)
            }
        elif event_type == "button_click":
            return {
                "button_type": random.choice(["bet", "spin", "deposit", "withdraw", "login"]),
                "element_position": {"x": random.randint(0, 1920), "y": random.randint(0, 1080)}
            }
        else:
            return {}

    def _generate_game_state(self, game_type: str) -> Dict[str, Any]:
        """Generate game-specific state data"""

        if game_type == "slots":
            return {
                "reel_positions": [random.randint(1, 20) for _ in range(3)],
                "multiplier": random.choice([1, 2, 3, 5, 10]),
                "free_spins": random.randint(0, 10)
            }
        elif game_type == "blackjack":
            return {
                "player_cards": [random.randint(1, 13) for _ in range(random.randint(2, 5))],
                "dealer_card": random.randint(1, 13),
                "player_score": random.randint(4, 21),
                "dealer_score": random.randint(2, 21)
            }
        elif game_type == "roulette":
            return {
                "last_numbers": [random.randint(0, 36) for _ in range(5)],
                "bet_type": random.choice(["straight", "split", "corner", "red", "black", "even", "odd"])
            }
        else:
            return {"state": "active"}

    def generate_player_history(self, player_id: str, days: int = 30,
                              pattern: str = "normal") -> Dict[str, Any]:
        """Generate complete history for a player"""

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        end_date = datetime.now(timezone.utc)

        # Determine behavior pattern
        pattern_weights = list(self.fraud_patterns.values())
        pattern_labels = list(self.fraud_patterns.keys())
        actual_pattern = random.choices(pattern_labels, weights=pattern_weights)[0]

        # Override if specified
        if pattern != "normal":
            actual_pattern = pattern

        history: Dict[str, Any] = {
            "player_id": player_id,
            "pattern": actual_pattern,
            "transactions": [],
            "user_events": [],
            "game_events": []
        }

        current_time = start_date

        # Generate events over time
        while current_time < end_date:
            # Transactions (0-5 per hour for normal, more for suspicious)
            txn_count = random.randint(0, 8) if actual_pattern == "fraudulent" else random.randint(0, 5)
            for _ in range(txn_count):
                history["transactions"].append(
                    self.generate_transaction(player_id, current_time, actual_pattern)
                )

            # User events (1-10 per hour)
            event_count = random.randint(1, 15) if actual_pattern == "suspicious" else random.randint(1, 10)
            for _ in range(event_count):
                history["user_events"].append(
                    self.generate_user_event(player_id, current_time, actual_pattern)
                )

            # Game events (0-20 per hour)
            game_count = random.randint(0, 30) if actual_pattern == "fraudulent" else random.randint(0, 20)
            for _ in range(game_count):
                history["game_events"].append(
                    self.generate_game_event(player_id, current_time, actual_pattern)
                )

            # Advance time by 1 hour
            current_time += timedelta(hours=1)

        return history

    def generate_bulk_data(self, count: int, output_format: str = "json",
                          pattern_distribution: Optional[Dict[str, float]] = None) -> str:
        """Generate bulk test data"""

        if pattern_distribution:
            self.fraud_patterns = pattern_distribution

        all_data: Dict[str, Any] = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_records": count,
                "pattern_distribution": self.fraud_patterns
            },
            "transactions": [],
            "user_events": [],
            "game_events": []
        }

        # Generate data for random players
        for _ in range(count):
            player_id = random.choice(self.player_ids)
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 1440))

            # Random event type
            event_type = random.choice(["transaction", "user_event", "game_event"])

            if event_type == "transaction":
                # Determine pattern
                pattern = random.choices(
                    list(self.fraud_patterns.keys()),
                    weights=list(self.fraud_patterns.values())
                )[0]

                all_data["transactions"].append(
                    self.generate_transaction(player_id, timestamp, pattern)
                )
            elif event_type == "user_event":
                pattern = random.choices(
                    list(self.fraud_patterns.keys()),
                    weights=list(self.fraud_patterns.values())
                )[0]

                all_data["user_events"].append(
                    self.generate_user_event(player_id, timestamp, pattern)
                )
            else:  # game_event
                pattern = random.choices(
                    list(self.fraud_patterns.keys()),
                    weights=list(self.fraud_patterns.values())
                )[0]

                all_data["game_events"].append(
                    self.generate_game_event(player_id, timestamp, pattern)
                )

        if output_format == "json":
            return json.dumps(all_data, indent=2, default=str)
        else:
            # CSV-like format
            lines = ["event_type,player_id,timestamp,amount,game_type,event_details"]
            for txn in all_data["transactions"]:
                lines.append(f"transaction,{txn['player_id']},{txn['timestamp']},{txn['amount']},{txn.get('game_type', '')},{txn['transaction_type']}")
            for event in all_data["user_events"]:
                lines.append(f"user_event,{event['player_id']},{event['timestamp']},,{event.get('game_type', '')},{event['event_type']}")
            for event in all_data["game_events"]:
                lines.append(f"game_event,{event['player_id']},{event['timestamp']},{event.get('bet_amount', '')},{event['game_type']},{event['event_type']}")
            return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate test data for fraud detection system")
    parser.add_argument("--count", type=int, default=1000, help="Number of records to generate")
    parser.add_argument("--output", type=str, default="test_data.json", help="Output file path")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    parser.add_argument("--pattern", choices=["normal", "suspicious", "fraudulent"], help="Force specific pattern")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible data")

    args = parser.parse_args()

    generator = FraudDataGenerator(seed=args.seed)

    if args.pattern:
        # Override pattern distribution
        pattern_dist = {args.pattern: 1.0}
        data = generator.generate_bulk_data(args.count, args.format, pattern_dist)
    else:
        data = generator.generate_bulk_data(args.count, args.format)

    with open(args.output, 'w') as f:
        f.write(data)

    print(f"Generated {args.count} test records in {args.output}")


if __name__ == "__main__":
    main()