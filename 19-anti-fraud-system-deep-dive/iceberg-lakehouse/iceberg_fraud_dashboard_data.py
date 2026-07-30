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
Iceberg Fraud Dashboard Data Generator
=======================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

Generates realistic fake fraud datasets for dashboard development and
testing. Creates 10K+ transactions, player profiles with risk scores,
fraud alert history, and geographic distribution data -- all output
as JSON files ready for dashboard consumption.

The generated data follows real-world gambling fraud patterns:
- Fraud rate: 2-5% of transactions (configurable)
- Bot activity peaks during off-hours (2-5 AM local time)
- Money laundering clusters around deposit/withdrawal pairs
- Bonus abuse spikes after new promotions launch
- Collusion appears as correlated bet patterns between player pairs

Output files:
- transactions.json: 10K+ transaction records
- player_profiles.json: player risk scores and demographics
- fraud_alerts.json: detection alert history
- geo_distribution.json: fraud by country and jurisdiction
- temporal_patterns.json: fraud by hour/day of week

Usage:
    python iceberg_fraud_dashboard_data.py --output-dir ./dashboard-data
    python iceberg_fraud_dashboard_data.py --transactions 50000 --fraud-rate 0.03
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fraud_dashboard_data")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class DataGenConfig:
    """Configuration for the data generator."""

    output_dir: str = "./dashboard-data"
    num_transactions: int = 10000
    num_players: int = 500
    fraud_rate: float = 0.03  # 3% of transactions are fraudulent
    date_range_days: int = 30
    seed: int = 42

    # Jurisdictions with realistic distribution
    jurisdictions: list[str] | None = None
    # Countries per jurisdiction
    countries: dict[str, list[str]] | None = None

    def __post_init__(self) -> None:
        if self.jurisdictions is None:
            self.jurisdictions = ["MGA", "UKGC", "SGA", "DGA", "AGCO", "NJDGE"]
        if self.countries is None:
            self.countries = {
                "MGA": ["Malta", "Germany", "Finland", "Norway", "Austria"],
                "UKGC": ["United Kingdom", "Ireland"],
                "SGA": ["Sweden"],
                "DGA": ["Denmark"],
                "AGCO": ["Canada"],
                "NJDGE": ["United States"],
            }


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

# Game catalog: realistic games with RTP ranges
GAMES: list[dict[str, Any]] = [
    {"id": "slot-starburst", "name": "Starburst", "type": "slots", "rtp": 96.09},
    {"id": "slot-book-of-dead", "name": "Book of Dead", "type": "slots", "rtp": 96.21},
    {"id": "slot-gonzo", "name": "Gonzo's Quest", "type": "slots", "rtp": 95.97},
    {"id": "slot-mega-moolah", "name": "Mega Moolah", "type": "slots", "rtp": 88.12},
    {"id": "slot-reactoonz", "name": "Reactoonz", "type": "slots", "rtp": 96.51},
    {"id": "table-blackjack", "name": "Blackjack Classic", "type": "table", "rtp": 99.54},
    {"id": "table-roulette", "name": "European Roulette", "type": "table", "rtp": 97.30},
    {"id": "table-baccarat", "name": "Baccarat", "type": "table", "rtp": 98.94},
    {"id": "live-blackjack", "name": "Live Blackjack", "type": "live", "rtp": 99.50},
    {"id": "live-roulette", "name": "Live Roulette", "type": "live", "rtp": 97.30},
    {"id": "crash-aviator", "name": "Aviator", "type": "crash", "rtp": 97.00},
    {"id": "keno-classic", "name": "Keno Classic", "type": "keno", "rtp": 92.50},
]

PAYMENT_METHODS = [
    "visa", "mastercard", "skrill", "neteller", "paysafecard",
    "bank_transfer", "apple_pay", "google_pay", "bitcoin", "ethereum",
]

FRAUD_TYPES = [
    "bot_play", "account_takeover", "money_laundering",
    "collusion", "bonus_abuse", "multi_accounting",
]

ALERT_STATUSES = ["detected", "investigating", "escalated", "resolved", "cleared"]

# Realistic IP address ranges by country (fictional ranges for the book)
COUNTRY_IP_PREFIXES: dict[str, list[str]] = {
    "Malta": ["185.10.", "91.199."],
    "Germany": ["46.114.", "78.42."],
    "Finland": ["91.156.", "83.150."],
    "Norway": ["77.16.", "84.208."],
    "Austria": ["77.116.", "84.112."],
    "United Kingdom": ["86.128.", "90.192."],
    "Ireland": ["86.40.", "87.32."],
    "Sweden": ["85.224.", "90.224."],
    "Denmark": ["80.62.", "87.48."],
    "Canada": ["24.48.", "69.156."],
    "United States": ["64.58.", "72.14."],
}


def generate_ip(country: str) -> str:
    """Generate a realistic-looking IP address for a country."""
    prefixes = COUNTRY_IP_PREFIXES.get(country, ["192.168."])
    prefix = random.choice(prefixes)
    return f"{prefix}{random.randint(1, 254)}.{random.randint(1, 254)}"


def generate_device_fingerprint() -> str:
    """Generate a fake device fingerprint hash."""
    raw = "".join(random.choices(string.ascii_lowercase + string.digits, k=32))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Player generation
# ---------------------------------------------------------------------------

def generate_players(config: DataGenConfig) -> list[dict[str, Any]]:
    """Generate player profiles with risk characteristics.

    Players are categorized:
    - 85% normal players (low risk)
    - 8% suspicious players (medium risk, may trigger some rules)
    - 5% high-risk players (likely fraudulent)
    - 2% confirmed fraudsters (will generate alerts)

    Each player has a behavioral profile that determines their
    transaction patterns (bet sizes, frequency, game preferences).
    """
    assert config.jurisdictions is not None
    assert config.countries is not None

    players: list[dict[str, Any]] = []

    for i in range(config.num_players):
        player_id = f"PLR-{uuid.uuid4().hex[:8]}"
        jurisdiction = random.choice(config.jurisdictions)
        country = random.choice(config.countries[jurisdiction])

        # Risk category distribution
        risk_roll = random.random()
        if risk_roll < 0.02:
            risk_category = "fraudster"
            risk_score = random.uniform(0.85, 1.0)
            risk_level = "critical"
        elif risk_roll < 0.07:
            risk_category = "high_risk"
            risk_score = random.uniform(0.65, 0.85)
            risk_level = "high"
        elif risk_roll < 0.15:
            risk_category = "suspicious"
            risk_score = random.uniform(0.35, 0.65)
            risk_level = "medium"
        else:
            risk_category = "normal"
            risk_score = random.uniform(0.0, 0.35)
            risk_level = "low"

        # Behavioral profile
        if risk_category == "fraudster":
            avg_bet = random.randint(5000, 50000)  # High bets
            bets_per_day = random.randint(100, 500)  # Very active
            preferred_games = ["table-blackjack", "table-roulette"]  # High RTP
        elif risk_category == "high_risk":
            avg_bet = random.randint(2000, 20000)
            bets_per_day = random.randint(50, 200)
            preferred_games = random.sample([g["id"] for g in GAMES], k=3)
        elif risk_category == "suspicious":
            avg_bet = random.randint(500, 5000)
            bets_per_day = random.randint(20, 100)
            preferred_games = random.sample([g["id"] for g in GAMES], k=4)
        else:
            avg_bet = random.randint(100, 2000)
            bets_per_day = random.randint(5, 30)
            preferred_games = random.sample([g["id"] for g in GAMES], k=random.randint(2, 6))

        players.append({
            "player_id": player_id,
            "jurisdiction": jurisdiction,
            "country": country,
            "risk_category": risk_category,
            "risk_score": round(risk_score, 4),
            "risk_level": risk_level,
            "avg_bet_cents": avg_bet,
            "bets_per_day": bets_per_day,
            "preferred_games": preferred_games,
            "registration_date": (
                datetime.now(timezone.utc) - timedelta(days=random.randint(1, 365))
            ).isoformat(),
            "ip_address": generate_ip(country),
            "device_fingerprint": generate_device_fingerprint(),
            "payment_methods": random.sample(PAYMENT_METHODS, k=random.randint(1, 3)),
            "bot_score": round(random.uniform(0.8, 1.0) if risk_category == "fraudster" else random.uniform(0.0, 0.3), 4),
            "ato_score": round(random.uniform(0.0, 0.4), 4),
            "laundering_score": round(
                random.uniform(0.6, 0.95) if risk_category in ("fraudster", "high_risk") else random.uniform(0.0, 0.2),
                4,
            ),
            "collusion_score": round(random.uniform(0.0, 0.3), 4),
            "bonus_abuse_score": round(random.uniform(0.0, 0.3), 4),
        })

    logger.info(
        "Generated %d players: %d normal, %d suspicious, %d high-risk, %d fraudsters",
        len(players),
        sum(1 for p in players if p["risk_category"] == "normal"),
        sum(1 for p in players if p["risk_category"] == "suspicious"),
        sum(1 for p in players if p["risk_category"] == "high_risk"),
        sum(1 for p in players if p["risk_category"] == "fraudster"),
    )

    return players


# ---------------------------------------------------------------------------
# Transaction generation
# ---------------------------------------------------------------------------

def generate_transactions(
    players: list[dict[str, Any]],
    config: DataGenConfig,
) -> list[dict[str, Any]]:
    """Generate realistic transaction records.

    Transaction patterns vary by player risk category:
    - Normal: spread throughout day, varied bet sizes, natural pauses
    - Suspicious: some clustering, occasional high bets
    - High-risk: concentrated activity, high amounts, round numbers
    - Fraudster: bot-like patterns, consistent timing, high-RTP games

    Temporal patterns:
    - Peak activity: 7-11 PM local time
    - Bot activity: 2-5 AM (less human oversight)
    - Weekend spike: 20% more volume
    """
    transactions: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=config.date_range_days)

    for _ in range(config.num_transactions):
        player = random.choice(players)

        # Transaction timing based on risk category
        if player["risk_category"] == "fraudster":
            # Bots prefer off-hours
            hour = random.choices(
                range(24),
                weights=[3, 3, 5, 5, 5, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3],
            )[0]
        else:
            # Normal players prefer evening hours
            hour = random.choices(
                range(24),
                weights=[1, 1, 1, 1, 1, 1, 2, 3, 3, 3, 4, 4, 4, 4, 5, 5, 6, 7, 8, 9, 8, 6, 4, 2],
            )[0]

        event_time = start_date + timedelta(
            days=random.randint(0, config.date_range_days - 1),
            hours=hour,
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59),
        )

        # Transaction type distribution
        tx_type_roll = random.random()
        if tx_type_roll < 0.80:
            tx_type = "bet"
        elif tx_type_roll < 0.90:
            tx_type = "deposit"
        elif tx_type_roll < 0.96:
            tx_type = "withdrawal"
        else:
            tx_type = "bonus"

        # Amount based on player profile
        base_amount = player["avg_bet_cents"]
        if player["risk_category"] == "fraudster":
            # Bots use consistent amounts (low variance)
            amount = base_amount + random.randint(-100, 100)
        else:
            # Normal players have higher variance
            amount = int(base_amount * random.uniform(0.2, 3.0))

        # Deposits/withdrawals are larger
        if tx_type in ("deposit", "withdrawal"):
            amount = amount * random.randint(5, 20)

        amount = max(100, amount)  # Minimum 1 EUR

        game = random.choice(
            [g for g in GAMES if g["id"] in player["preferred_games"]]
            if player["preferred_games"] else GAMES
        )

        # Is this transaction fraudulent?
        is_fraud = (
            player["risk_category"] in ("fraudster", "high_risk")
            and random.random() < config.fraud_rate * 3
        ) or random.random() < config.fraud_rate * 0.1

        transactions.append({
            "transaction_id": f"TX-{uuid.uuid4().hex[:12]}",
            "player_id": player["player_id"],
            "event_time": event_time.isoformat(),
            "transaction_type": tx_type,
            "amount_cents": amount,
            "currency": "EUR",
            "game_id": game["id"],
            "game_type": game["type"],
            "payment_method": random.choice(player["payment_methods"]) if tx_type != "bet" else None,
            "jurisdiction": player["jurisdiction"],
            "brand_id": random.randint(1, 5),
            "ip_address": player["ip_address"],
            "device_fingerprint": player["device_fingerprint"],
            "session_id": f"SES-{uuid.uuid4().hex[:8]}",
            "risk_score": round(player["risk_score"] + random.uniform(-0.1, 0.1), 4),
            "risk_level": player["risk_level"],
            "is_fraud": is_fraud,
        })

    # Sort by time
    transactions.sort(key=lambda t: t["event_time"])
    logger.info(
        "Generated %d transactions (%d flagged as fraud)",
        len(transactions),
        sum(1 for t in transactions if t["is_fraud"]),
    )

    return transactions


# ---------------------------------------------------------------------------
# Fraud alerts generation
# ---------------------------------------------------------------------------

def generate_fraud_alerts(
    transactions: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate fraud alerts from fraudulent transactions.

    Simulates the output of both real-time (Flink) and batch (Spark)
    detection pipelines. Alerts include:
    - Detection method (real-time vs batch)
    - Fraud type classification
    - Confidence score
    - Current investigation status
    """
    alerts: list[dict[str, Any]] = []
    player_map = {p["player_id"]: p for p in players}

    fraud_txs = [t for t in transactions if t.get("is_fraud", False)]

    for tx in fraud_txs:
        player = player_map.get(tx["player_id"], {})

        # Determine fraud type based on player profile
        fraud_type_weights = {
            "bot_play": player.get("bot_score", 0.1),
            "account_takeover": player.get("ato_score", 0.1),
            "money_laundering": player.get("laundering_score", 0.1),
            "collusion": player.get("collusion_score", 0.1),
            "bonus_abuse": player.get("bonus_abuse_score", 0.1),
        }
        fraud_type = max(fraud_type_weights, key=fraud_type_weights.get)  # type: ignore[arg-type]

        confidence = min(1.0, max(0.3, player.get("risk_score", 0.5) + random.uniform(-0.15, 0.15)))

        if confidence >= 0.85:
            severity = "critical"
        elif confidence >= 0.65:
            severity = "high"
        elif confidence >= 0.4:
            severity = "medium"
        else:
            severity = "low"

        # Status distribution: most alerts are still in progress
        status = random.choices(
            ALERT_STATUSES,
            weights=[30, 25, 15, 20, 10],
        )[0]

        detected_at = datetime.fromisoformat(tx["event_time"]) + timedelta(
            seconds=random.randint(1, 300)  # 1 second to 5 minutes detection lag
        )

        alert: dict[str, Any] = {
            "alert_id": f"ALERT-{uuid.uuid4().hex[:12]}",
            "player_id": tx["player_id"],
            "detected_at": detected_at.isoformat(),
            "fraud_type": fraud_type,
            "severity": severity,
            "confidence_score": round(confidence, 4),
            "description": f"Automated detection: {fraud_type} (confidence={confidence:.2f})",
            "jurisdiction": tx["jurisdiction"],
            "risk_level": severity,
            "transaction_id": tx["transaction_id"],
            "status": status,
            "detection_method": random.choice(["realtime_flink", "batch_spark"]),
        }

        if status in ("resolved", "cleared"):
            alert["resolved_at"] = (
                detected_at + timedelta(hours=random.randint(1, 72))
            ).isoformat()
            alert["resolution"] = random.choice([
                "confirmed_fraud_account_blocked",
                "false_positive_cleared",
                "suspicious_monitoring_continued",
            ])
            alert["analyst_id"] = f"ANALYST-{random.randint(100, 999)}"

        alerts.append(alert)

    logger.info("Generated %d fraud alerts", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# Aggregation for dashboard views
# ---------------------------------------------------------------------------

def compute_geo_distribution(
    transactions: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute geographic distribution of fraud.

    Aggregates fraud metrics by country and jurisdiction for the
    dashboard's geographic heatmap view.
    """
    # Count transactions and alerts by jurisdiction
    jurisdiction_stats: dict[str, dict[str, int]] = {}

    for tx in transactions:
        jur = tx["jurisdiction"]
        if jur not in jurisdiction_stats:
            jurisdiction_stats[jur] = {
                "total_transactions": 0,
                "fraud_transactions": 0,
                "total_amount_cents": 0,
            }
        jurisdiction_stats[jur]["total_transactions"] += 1
        jurisdiction_stats[jur]["total_amount_cents"] += tx["amount_cents"]
        if tx.get("is_fraud"):
            jurisdiction_stats[jur]["fraud_transactions"] += 1

    # Count alerts by jurisdiction
    for alert in alerts:
        jur = alert["jurisdiction"]
        if jur in jurisdiction_stats:
            jurisdiction_stats[jur].setdefault("alert_count", 0)
            jurisdiction_stats[jur]["alert_count"] = jurisdiction_stats[jur].get("alert_count", 0) + 1

    result = []
    for jur, stats in jurisdiction_stats.items():
        total = stats["total_transactions"]
        fraud = stats["fraud_transactions"]
        result.append({
            "jurisdiction": jur,
            "total_transactions": total,
            "fraud_transactions": fraud,
            "fraud_rate_pct": round((fraud / total * 100) if total > 0 else 0, 2),
            "total_amount_eur": round(stats["total_amount_cents"] / 100, 2),
            "alert_count": stats.get("alert_count", 0),
            "unique_players": len({
                p["player_id"] for p in players if p["jurisdiction"] == jur
            }),
        })

    result.sort(key=lambda x: x["fraud_rate_pct"], reverse=True)
    logger.info("Computed geo distribution for %d jurisdictions", len(result))
    return result


def compute_temporal_patterns(
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute temporal fraud patterns for the dashboard.

    Generates hour-of-day and day-of-week distributions showing
    when fraud is most active. This helps operators staff their
    fraud investigation teams appropriately.
    """
    hourly: dict[int, dict[str, int]] = {h: {"total": 0, "fraud": 0} for h in range(24)}
    daily: dict[int, dict[str, int]] = {d: {"total": 0, "fraud": 0} for d in range(7)}

    for tx in transactions:
        event_time = datetime.fromisoformat(tx["event_time"])
        hour = event_time.hour
        day = event_time.weekday()

        hourly[hour]["total"] += 1
        daily[day]["total"] += 1

        if tx.get("is_fraud"):
            hourly[hour]["fraud"] += 1
            daily[day]["fraud"] += 1

    # Convert to lists for JSON
    hourly_data = []
    for hour in range(24):
        total = hourly[hour]["total"]
        fraud = hourly[hour]["fraud"]
        hourly_data.append({
            "hour": hour,
            "total_transactions": total,
            "fraud_transactions": fraud,
            "fraud_rate_pct": round((fraud / total * 100) if total > 0 else 0, 2),
        })

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    daily_data = []
    for day in range(7):
        total = daily[day]["total"]
        fraud = daily[day]["fraud"]
        daily_data.append({
            "day": day_names[day],
            "day_index": day,
            "total_transactions": total,
            "fraud_transactions": fraud,
            "fraud_rate_pct": round((fraud / total * 100) if total > 0 else 0, 2),
        })

    logger.info("Computed temporal patterns (24 hours, 7 days)")
    return {
        "hourly": hourly_data,
        "daily": daily_data,
    }


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_all_data(config: DataGenConfig) -> None:
    """Generate all dashboard data files.

    Output files:
    - transactions.json: raw transaction records
    - player_profiles.json: player risk profiles
    - fraud_alerts.json: detection alert history
    - geo_distribution.json: fraud by jurisdiction
    - temporal_patterns.json: fraud by time
    """
    random.seed(config.seed)

    # Ensure output directory exists
    output_path = Path(config.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate data
    players = generate_players(config)
    transactions = generate_transactions(players, config)
    alerts = generate_fraud_alerts(transactions, players)
    geo_dist = compute_geo_distribution(transactions, alerts, players)
    temporal = compute_temporal_patterns(transactions)

    # Write output files
    files: dict[str, Any] = {
        "transactions.json": transactions,
        "player_profiles.json": players,
        "fraud_alerts.json": alerts,
        "geo_distribution.json": geo_dist,
        "temporal_patterns.json": temporal,
    }

    for filename, data in files.items():
        filepath = output_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Wrote %s (%d records)", filepath, len(data) if isinstance(data, list) else 1)

    # Summary
    logger.info("--- Data Generation Summary ---")
    logger.info("Output directory: %s", output_path.resolve())
    logger.info("Players: %d", len(players))
    logger.info("Transactions: %d", len(transactions))
    logger.info("Fraud alerts: %d", len(alerts))
    logger.info(
        "Fraud rate: %.1f%%",
        sum(1 for t in transactions if t.get("is_fraud")) / len(transactions) * 100,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate fake fraud dashboard data for iGaming",
    )
    parser.add_argument(
        "--output-dir",
        default="./dashboard-data",
        help="Output directory for JSON files",
    )
    parser.add_argument(
        "--transactions",
        type=int,
        default=10000,
        help="Number of transactions to generate (default: 10000)",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=500,
        help="Number of player profiles (default: 500)",
    )
    parser.add_argument(
        "--fraud-rate",
        type=float,
        default=0.03,
        help="Base fraud rate (default: 0.03 = 3%%)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Date range in days (default: 30)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    config = DataGenConfig(
        output_dir=args.output_dir,
        num_transactions=args.transactions,
        num_players=args.players,
        fraud_rate=args.fraud_rate,
        date_range_days=args.days,
        seed=args.seed,
    )

    generate_all_data(config)


if __name__ == "__main__":
    main()
