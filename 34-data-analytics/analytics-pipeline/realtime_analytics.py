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
Real-Time Analytics Pipeline for iGaming

This module provides streaming analytics capabilities including:
- Player activity monitoring (engagement, sessions)
- Game performance tracking (RTP, popularity)
- Revenue metrics (deposits, bets, GGR)
- Risk monitoring and alerting

Architecture:
- Apache Kafka for event streaming
- Apache Flink for stream processing
- Redis for real-time state and caching
- Alerting integration (PagerDuty, Slack)

Usage:
    pipeline = RealTimeAnalyticsPipeline(
        kafka_brokers=["localhost:9092"],
        redis_url="redis://localhost:6379"
    )

    # Start the pipeline
    await pipeline.start()

    # Stop gracefully
    await pipeline.stop()

Dependencies:
    pip install apache-flink redis aiohttp
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import asyncio
import json
import logging


@dataclass
class StreamEvent:
    """Base event for stream processing."""

    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    timestamp: datetime
    properties: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamEvent":
        """Create event from dictionary."""
        return cls(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", ""),
            entity_type=data.get("entity_type", ""),
            entity_id=data.get("entity_id", ""),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            properties=data.get("properties", {}),
        )


@dataclass
class WindowResult:
    """Result from a windowed aggregation."""

    window_start: datetime
    window_end: datetime
    key: str
    metrics: dict[str, Any]


@dataclass
class RiskSignal:
    """Risk signal for monitoring."""

    player_id: str
    signal_type: str
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    score: float
    details: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class StreamProcessor(ABC):
    """Abstract base class for stream processors."""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"{__name__}.{name}")
        self._running = False

    @abstractmethod
    async def process(self, event: StreamEvent) -> Optional[dict[str, Any]]:
        """Process a single event."""
        pass

    @abstractmethod
    async def get_window_result(self) -> Optional[WindowResult]:
        """Get aggregated window result."""
        pass


class PlayerActivityProcessor(StreamProcessor):
    """
    Processor for player activity metrics.

    Tracks:
    - Active players per minute
    - Session duration
    - Engagement scores
    - Activity patterns
    """

    def __init__(self) -> None:
        super().__init__("PlayerActivity")
        self.activity_buffer: dict[str, list[StreamEvent]] = {}
        self.window_start = datetime.now(timezone.utc)

    async def process(self, event: StreamEvent) -> Optional[dict[str, Any]]:
        """Process player activity event."""
        if event.entity_type != "player":
            return None

        player_id = event.entity_id

        # Initialize buffer for player
        if player_id not in self.activity_buffer:
            self.activity_buffer[player_id] = []

        self.activity_buffer[player_id].append(event)

        # Calculate real-time metrics
        player_events = self.activity_buffer[player_id]
        event_types = set(e.event_type for e in player_events)

        return {
            "player_id": player_id,
            "event_count": len(player_events),
            "unique_event_types": len(event_types),
            "last_activity": event.timestamp.isoformat(),
            "engagement_score": min(100, len(event_types) * 10 + len(player_events) * 2),
        }

    async def get_window_result(self) -> Optional[WindowResult]:
        """Get aggregated player activity for window."""
        if not self.activity_buffer:
            return None

        window_end = datetime.now(timezone.utc)

        active_players = len(self.activity_buffer)
        total_events = sum(len(events) for events in self.activity_buffer.values())

        result = WindowResult(
            window_start=self.window_start,
            window_end=window_end,
            key="player_activity",
            metrics={
                "active_players": active_players,
                "total_events": total_events,
                "events_per_player": total_events / active_players if active_players > 0 else 0,
            },
        )

        # Reset window
        self.activity_buffer.clear()
        self.window_start = window_end

        return result


class GamePerformanceProcessor(StreamProcessor):
    """
    Processor for game performance metrics.

    Tracks:
    - Bets and wins per game
    - RTP (Return to Player)
    - Popularity (plays per minute)
    - Unique players
    """

    def __init__(self) -> None:
        super().__init__("GamePerformance")
        self.game_metrics: dict[str, dict[str, Any]] = {}
        self.window_start = datetime.now(timezone.utc)

    async def process(self, event: StreamEvent) -> Optional[dict[str, Any]]:
        """Process game event."""
        if event.event_type not in ["bet_placed", "win", "game_round"]:
            return None

        game_id = event.properties.get("game_id", "unknown")
        amount = float(event.properties.get("amount", 0))

        # Initialize game metrics
        if game_id not in self.game_metrics:
            self.game_metrics[game_id] = {
                "total_bets": 0.0,
                "total_wins": 0.0,
                "rounds": 0,
                "players": set(),
            }

        metrics = self.game_metrics[game_id]

        if event.event_type == "bet_placed":
            metrics["total_bets"] += amount
        elif event.event_type == "win":
            metrics["total_wins"] += amount

        metrics["rounds"] += 1
        if isinstance(metrics["players"], set):
            metrics["players"].add(event.entity_id)

        # Calculate RTP
        rtp = (
            (metrics["total_wins"] / metrics["total_bets"] * 100)
            if metrics["total_bets"] > 0
            else 0
        )

        return {
            "game_id": game_id,
            "total_bets": metrics["total_bets"],
            "total_wins": metrics["total_wins"],
            "rtp_percentage": round(rtp, 2),
            "rounds": metrics["rounds"],
            "unique_players": len(metrics["players"]) if isinstance(metrics["players"], set) else 0,
        }

    async def get_window_result(self) -> Optional[WindowResult]:
        """Get aggregated game performance for window."""
        if not self.game_metrics:
            return None

        window_end = datetime.now(timezone.utc)

        # Aggregate all games
        total_bets = sum(m["total_bets"] for m in self.game_metrics.values())
        total_wins = sum(m["total_wins"] for m in self.game_metrics.values())
        total_rounds = sum(m["rounds"] for m in self.game_metrics.values())

        result = WindowResult(
            window_start=self.window_start,
            window_end=window_end,
            key="game_performance",
            metrics={
                "total_bets": total_bets,
                "total_wins": total_wins,
                "total_rounds": total_rounds,
                "overall_rtp": (total_wins / total_bets * 100) if total_bets > 0 else 0,
                "active_games": len(self.game_metrics),
            },
        )

        # Reset window
        self.game_metrics.clear()
        self.window_start = window_end

        return result


class RevenueProcessor(StreamProcessor):
    """
    Processor for revenue metrics.

    Tracks:
    - Deposits and withdrawals
    - Betting volume
    - GGR (Gross Gaming Revenue)
    - Real-time revenue
    """

    def __init__(self) -> None:
        super().__init__("Revenue")
        self.revenue_metrics: dict[str, float] = {
            "deposits": 0.0,
            "withdrawals": 0.0,
            "bets": 0.0,
            "wins": 0.0,
        }
        self.window_start = datetime.now(timezone.utc)

    async def process(self, event: StreamEvent) -> Optional[dict[str, Any]]:
        """Process financial event."""
        if event.event_type not in ["deposit", "withdrawal", "bet_placed", "win"]:
            return None

        amount = float(event.properties.get("amount", 0))

        if event.event_type == "deposit":
            self.revenue_metrics["deposits"] += amount
        elif event.event_type == "withdrawal":
            self.revenue_metrics["withdrawals"] += amount
        elif event.event_type == "bet_placed":
            self.revenue_metrics["bets"] += amount
        elif event.event_type == "win":
            self.revenue_metrics["wins"] += amount

        # Calculate GGR
        ggr = self.revenue_metrics["bets"] - self.revenue_metrics["wins"]

        return {
            "deposits": self.revenue_metrics["deposits"],
            "withdrawals": self.revenue_metrics["withdrawals"],
            "bets": self.revenue_metrics["bets"],
            "wins": self.revenue_metrics["wins"],
            "ggr": ggr,
            "net_deposits": self.revenue_metrics["deposits"] - self.revenue_metrics["withdrawals"],
        }

    async def get_window_result(self) -> Optional[WindowResult]:
        """Get aggregated revenue for window."""
        window_end = datetime.now(timezone.utc)

        ggr = self.revenue_metrics["bets"] - self.revenue_metrics["wins"]

        result = WindowResult(
            window_start=self.window_start,
            window_end=window_end,
            key="revenue",
            metrics={
                "deposits": self.revenue_metrics["deposits"],
                "withdrawals": self.revenue_metrics["withdrawals"],
                "bets": self.revenue_metrics["bets"],
                "wins": self.revenue_metrics["wins"],
                "ggr": ggr,
            },
        )

        # Reset window
        self.revenue_metrics = {
            "deposits": 0.0,
            "withdrawals": 0.0,
            "bets": 0.0,
            "wins": 0.0,
        }
        self.window_start = window_end

        return result


class RiskMonitoringProcessor(StreamProcessor):
    """
    Processor for risk monitoring.

    Detects:
    - Large transactions
    - Unusual betting patterns
    - Suspicious activity
    - Fraud indicators
    """

    def __init__(self) -> None:
        super().__init__("RiskMonitoring")
        self.risk_signals: dict[str, list[RiskSignal]] = {}
        self.thresholds = {
            "large_deposit": 10000.0,
            "large_withdrawal": 5000.0,
            "rapid_bets_count": 10,  # Bets in 1 minute
            "unusual_win_rate": 0.7,
        }

    async def process(self, event: StreamEvent) -> Optional[dict[str, Any]]:
        """Process event for risk signals."""
        signals = []

        amount = float(event.properties.get("amount", 0))
        player_id = event.entity_id

        # Large transaction detection
        if event.event_type == "deposit" and amount > self.thresholds["large_deposit"]:
            signal = RiskSignal(
                player_id=player_id,
                signal_type="large_deposit",
                severity="MEDIUM",
                score=min(100, amount / self.thresholds["large_deposit"] * 50),
                details={"amount": amount, "currency": event.properties.get("currency", "USD")},
            )
            signals.append(signal)

        if event.event_type == "withdrawal" and amount > self.thresholds["large_withdrawal"]:
            signal = RiskSignal(
                player_id=player_id,
                signal_type="large_withdrawal",
                severity="HIGH",
                score=min(100, amount / self.thresholds["large_withdrawal"] * 60),
                details={"amount": amount},
            )
            signals.append(signal)

        # Failed login detection
        if event.event_type == "login_failed":
            signal = RiskSignal(
                player_id=player_id,
                signal_type="failed_login",
                severity="LOW",
                score=20,
                details={"ip_address": event.properties.get("ip_address")},
            )
            signals.append(signal)

        # Store signals
        if signals:
            if player_id not in self.risk_signals:
                self.risk_signals[player_id] = []
            self.risk_signals[player_id].extend(signals)

        # Return aggregated risk for player
        if player_id in self.risk_signals:
            player_signals = self.risk_signals[player_id]
            total_score = sum(s.score for s in player_signals)

            return {
                "player_id": player_id,
                "signal_count": len(player_signals),
                "total_risk_score": total_score,
                "risk_level": self._get_risk_level(total_score),
                "signals": [
                    {
                        "type": s.signal_type,
                        "severity": s.severity,
                        "score": s.score,
                    }
                    for s in player_signals[-5:]  # Last 5 signals
                ],
            }

        return None

    def _get_risk_level(self, score: float) -> str:
        """Convert risk score to level."""
        if score >= 70:
            return "CRITICAL"
        elif score >= 50:
            return "HIGH"
        elif score >= 30:
            return "MEDIUM"
        else:
            return "LOW"

    async def get_window_result(self) -> Optional[WindowResult]:
        """Get aggregated risk signals for window."""
        if not self.risk_signals:
            return None

        total_signals = sum(len(signals) for signals in self.risk_signals.values())
        high_risk_players = sum(
            1 for signals in self.risk_signals.values()
            if sum(s.score for s in signals) >= 50
        )

        result = WindowResult(
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            key="risk_monitoring",
            metrics={
                "total_signals": total_signals,
                "players_with_signals": len(self.risk_signals),
                "high_risk_players": high_risk_players,
            },
        )

        # Reset window
        self.risk_signals.clear()

        return result


class RealTimeAnalyticsPipeline:
    """
    Real-time analytics pipeline orchestrator.

    Coordinates multiple stream processors and manages
    output to various sinks (Redis, Kafka, alerts).
    """

    def __init__(
        self,
        kafka_brokers: list[str],
        redis_url: str,
        window_seconds: int = 60,
    ):
        self.kafka_brokers = kafka_brokers
        self.redis_url = redis_url
        self.window_seconds = window_seconds
        self.logger = logging.getLogger(__name__)

        # Initialize processors
        self.processors: list[StreamProcessor] = [
            PlayerActivityProcessor(),
            GamePerformanceProcessor(),
            RevenueProcessor(),
            RiskMonitoringProcessor(),
        ]

        # State
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start the analytics pipeline."""
        self.logger.info("Starting Real-Time Analytics Pipeline...")
        self._running = True

        # Start window aggregation task
        self._tasks.append(
            asyncio.create_task(self._window_aggregator())
        )

        self.logger.info(f"Pipeline started with {len(self.processors)} processors")

    async def stop(self) -> None:
        """Stop the analytics pipeline."""
        self.logger.info("Stopping Real-Time Analytics Pipeline...")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        self.logger.info("Pipeline stopped")

    async def process_event(self, event_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process an event through all processors.

        Args:
            event_data: Raw event data

        Returns:
            Dictionary with results from all processors
        """
        event = StreamEvent.from_dict(event_data)
        results = {}

        for processor in self.processors:
            try:
                result = await processor.process(event)
                if result:
                    results[processor.name] = result
            except Exception as e:
                self.logger.error(f"Error in {processor.name}: {e}")

        return results

    async def _window_aggregator(self) -> None:
        """Background task for window aggregation."""
        while self._running:
            try:
                await asyncio.sleep(self.window_seconds)

                # Get window results from all processors
                for processor in self.processors:
                    try:
                        result = await processor.get_window_result()
                        if result:
                            await self._publish_window_result(result)
                    except Exception as e:
                        self.logger.error(f"Error getting window result from {processor.name}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Window aggregator error: {e}")

    async def _publish_window_result(self, result: WindowResult) -> None:
        """Publish window result to output sinks."""
        self.logger.info(
            f"Window result [{result.key}]: {json.dumps(result.metrics, default=str)}"
        )
        # Would publish to Redis, Kafka, etc.

    def print_status(self) -> None:
        """Print pipeline status."""
        print("\n" + "=" * 70)
        print("  REAL-TIME ANALYTICS PIPELINE STATUS")
        print("=" * 70)
        print(f"  Running: {self._running}")
        print(f"  Window Size: {self.window_seconds} seconds")
        print(f"  Kafka Brokers: {', '.join(self.kafka_brokers)}")
        print(f"  Redis URL: {self.redis_url}")
        print()
        print("  Processors:")
        for processor in self.processors:
            print(f"    - {processor.name}")
        print("=" * 70)


async def main() -> None:
    """Example usage of Real-Time Analytics Pipeline."""
    logging.basicConfig(level=logging.INFO)

    pipeline = RealTimeAnalyticsPipeline(
        kafka_brokers=["localhost:9092"],
        redis_url="redis://localhost:6379",
        window_seconds=60,
    )

    pipeline.print_status()

    print("\nExample event processing:")
    print("-" * 50)

    # Sample events
    events = [
        {
            "event_id": "evt_001",
            "event_type": "bet_placed",
            "entity_type": "player",
            "entity_id": "player_123",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": {"amount": 100, "game_id": "slots_777"},
        },
        {
            "event_id": "evt_002",
            "event_type": "deposit",
            "entity_type": "player",
            "entity_id": "player_456",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": {"amount": 15000, "currency": "USD"},
        },
    ]

    for event in events:
        results = await pipeline.process_event(event)
        print(f"\nEvent: {event['event_type']}")
        for processor_name, result in results.items():
            print(f"  {processor_name}: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
