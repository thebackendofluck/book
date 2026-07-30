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
Business Intelligence Engine for iGaming Platforms

This module provides comprehensive BI metrics and dashboards including:
- Revenue analytics (deposits, withdrawals, GGR, NGR)
- Player metrics (DAU, MAU, retention, churn)
- Game performance (RTP, popularity, profitability)
- Operational KPIs

Features:
- Real-time and historical metrics
- Trend analysis
- Comparative reporting
- Executive dashboards

Usage:
    bi = BusinessIntelligenceEngine(clickhouse_client)
    metrics = bi.get_business_metrics(start_date, end_date)
    dashboard = bi.generate_executive_dashboard()

Dependencies:
    pip install clickhouse-driver
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Protocol
import logging


class ClickHouseClientProtocol(Protocol):
    """Protocol for ClickHouse client interface."""

    def execute(self, query: str, params: Optional[dict[str, Any]] = None) -> list[Any]: ...


@dataclass
class RevenueMetrics:
    """Revenue metrics for a period."""

    date: str
    deposits: float
    withdrawals: float
    bets: float
    wins: float
    ggr: float  # Gross Gaming Revenue (bets - wins)
    ngr: float  # Net Gaming Revenue (GGR - bonuses - taxes)
    house_edge_actual: float


@dataclass
class PlayerMetrics:
    """Player metrics for a period."""

    date: str
    active_players: int
    new_registrations: int
    first_time_depositors: int
    returning_players: int
    churned_players: int
    retention_rate: float


@dataclass
class GameMetrics:
    """Game performance metrics."""

    game_id: str
    game_name: str
    total_bets: float
    total_wins: float
    rounds_played: int
    unique_players: int
    rtp_actual: float
    popularity_rank: int


@dataclass
class ExecutiveDashboard:
    """Executive dashboard summary."""

    period_start: datetime
    period_end: datetime
    total_revenue: float
    revenue_change_percent: float
    active_players: int
    player_change_percent: float
    top_games: list[GameMetrics]
    key_alerts: list[str]
    recommendations: list[str]


class BusinessIntelligenceEngine:
    """
    Business Intelligence engine for iGaming platforms.

    Provides comprehensive metrics, dashboards, and analytics
    for executive decision-making and operational monitoring.
    """

    def __init__(self, clickhouse_client: ClickHouseClientProtocol):
        self.clickhouse = clickhouse_client
        self.logger = logging.getLogger(__name__)

        # Target KPIs
        self.kpi_targets = {
            "daily_revenue": 100000.0,  # Target daily revenue
            "player_retention": 0.7,  # 70% retention target
            "rtp_target": 0.96,  # 96% RTP target
            "new_player_target": 500,  # New players per day
        }

    def get_revenue_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[RevenueMetrics]:
        """
        Get revenue metrics for a date range.

        Args:
            start_date: Start of period
            end_date: End of period

        Returns:
            List of daily revenue metrics
        """
        try:
            results = self.clickhouse.execute(
                """
                SELECT
                    toDate(timestamp) as date,
                    sum(CASE WHEN event_type = 'deposit'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as deposits,
                    sum(CASE WHEN event_type = 'withdrawal'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as withdrawals,
                    sum(CASE WHEN event_type = 'bet_placed'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as bets,
                    sum(CASE WHEN event_type = 'win'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as wins
                FROM casino_events
                WHERE timestamp BETWEEN %(start_date)s AND %(end_date)s
                GROUP BY date
                ORDER BY date
            """,
                {"start_date": start_date, "end_date": end_date},
            )

            metrics = []
            for row in results:
                date_str = row[0].isoformat() if isinstance(row[0], datetime) else str(row[0])
                deposits = float(row[1])
                withdrawals = float(row[2])
                bets = float(row[3])
                wins = float(row[4])
                ggr = bets - wins
                # Assume 20% of GGR goes to bonuses/taxes for NGR calculation
                ngr = ggr * 0.8
                house_edge = (ggr / bets * 100) if bets > 0 else 0

                metrics.append(
                    RevenueMetrics(
                        date=date_str,
                        deposits=deposits,
                        withdrawals=withdrawals,
                        bets=bets,
                        wins=wins,
                        ggr=ggr,
                        ngr=ngr,
                        house_edge_actual=house_edge,
                    )
                )

            return metrics

        except Exception as e:
            self.logger.error(f"Failed to get revenue metrics: {e}")
            return []

    def get_player_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[PlayerMetrics]:
        """
        Get player metrics for a date range.

        Args:
            start_date: Start of period
            end_date: End of period

        Returns:
            List of daily player metrics
        """
        try:
            results = self.clickhouse.execute(
                """
                SELECT
                    toDate(timestamp) as date,
                    uniq(entity_id) as active_players,
                    countIf(event_type = 'registration') as new_registrations,
                    countIf(event_type = 'first_deposit') as first_time_depositors,
                    countIf(event_type = 'login') as logins
                FROM casino_events
                WHERE timestamp BETWEEN %(start_date)s AND %(end_date)s
                  AND entity_type = 'player'
                GROUP BY date
                ORDER BY date
            """,
                {"start_date": start_date, "end_date": end_date},
            )

            metrics = []
            prev_active = 0

            for row in results:
                date_str = row[0].isoformat() if isinstance(row[0], datetime) else str(row[0])
                active_players = int(row[1])
                new_registrations = int(row[2])
                first_time_depositors = int(row[3])

                # Calculate returning players and retention
                returning = max(0, active_players - new_registrations)
                retention_rate = (returning / prev_active) if prev_active > 0 else 0

                # Estimate churned (simplified)
                churned = max(0, prev_active - returning) if prev_active > 0 else 0

                metrics.append(
                    PlayerMetrics(
                        date=date_str,
                        active_players=active_players,
                        new_registrations=new_registrations,
                        first_time_depositors=first_time_depositors,
                        returning_players=returning,
                        churned_players=churned,
                        retention_rate=retention_rate,
                    )
                )

                prev_active = active_players

            return metrics

        except Exception as e:
            self.logger.error(f"Failed to get player metrics: {e}")
            return []

    def get_game_performance(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 20,
    ) -> list[GameMetrics]:
        """
        Get game performance metrics.

        Args:
            start_date: Start of period
            end_date: End of period
            limit: Maximum number of games to return

        Returns:
            List of game metrics sorted by popularity
        """
        try:
            results = self.clickhouse.execute(
                """
                SELECT
                    JSONExtractString(properties, 'game_id') as game_id,
                    count(*) as rounds,
                    sum(CASE WHEN event_type = 'bet_placed'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as total_bets,
                    sum(CASE WHEN event_type = 'win'
                        THEN JSONExtractFloat(properties, 'amount') ELSE 0 END) as total_wins,
                    uniq(entity_id) as unique_players
                FROM casino_events
                WHERE timestamp BETWEEN %(start_date)s AND %(end_date)s
                  AND event_type IN ('bet_placed', 'win', 'game_round')
                  AND JSONExtractString(properties, 'game_id') != ''
                GROUP BY game_id
                ORDER BY total_bets DESC
                LIMIT %(limit)s
            """,
                {"start_date": start_date, "end_date": end_date, "limit": limit},
            )

            metrics = []
            for rank, row in enumerate(results, 1):
                game_id = str(row[0])
                rounds = int(row[1])
                total_bets = float(row[2])
                total_wins = float(row[3])
                unique_players = int(row[4])

                rtp = (total_wins / total_bets * 100) if total_bets > 0 else 0

                metrics.append(
                    GameMetrics(
                        game_id=game_id,
                        game_name=f"Game {game_id}",  # Would lookup actual name
                        total_bets=total_bets,
                        total_wins=total_wins,
                        rounds_played=rounds,
                        unique_players=unique_players,
                        rtp_actual=rtp,
                        popularity_rank=rank,
                    )
                )

            return metrics

        except Exception as e:
            self.logger.error(f"Failed to get game performance: {e}")
            return []

    def get_business_intelligence(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """
        Get comprehensive business intelligence metrics.

        Args:
            start_date: Start of period
            end_date: End of period

        Returns:
            Dictionary with all BI metrics
        """
        revenue = self.get_revenue_metrics(start_date, end_date)
        players = self.get_player_metrics(start_date, end_date)
        games = self.get_game_performance(start_date, end_date)

        # Calculate summary metrics
        total_ggr = sum(r.ggr for r in revenue)
        total_ngr = sum(r.ngr for r in revenue)
        total_active = sum(p.active_players for p in players)
        total_new = sum(p.new_registrations for p in players)
        avg_retention = (
            sum(p.retention_rate for p in players) / len(players)
            if players
            else 0
        )

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "revenue_metrics": [
                {
                    "date": r.date,
                    "deposits": r.deposits,
                    "withdrawals": r.withdrawals,
                    "ggr": r.ggr,
                    "ngr": r.ngr,
                    "house_edge": r.house_edge_actual,
                }
                for r in revenue
            ],
            "player_metrics": [
                {
                    "date": p.date,
                    "active_players": p.active_players,
                    "new_registrations": p.new_registrations,
                    "retention_rate": p.retention_rate,
                }
                for p in players
            ],
            "game_performance": [
                {
                    "game_id": g.game_id,
                    "total_bets": g.total_bets,
                    "rtp": g.rtp_actual,
                    "unique_players": g.unique_players,
                    "rank": g.popularity_rank,
                }
                for g in games
            ],
            "summary": {
                "total_ggr": total_ggr,
                "total_ngr": total_ngr,
                "total_active_players": total_active,
                "total_new_registrations": total_new,
                "average_retention_rate": avg_retention,
                "top_game": games[0].game_id if games else None,
            },
        }

    def generate_executive_dashboard(
        self,
        period_days: int = 7,
    ) -> ExecutiveDashboard:
        """
        Generate executive dashboard with key metrics.

        Args:
            period_days: Number of days for the report

        Returns:
            ExecutiveDashboard with summary and alerts
        """
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=period_days)

        # Previous period for comparison
        prev_end = start_date
        prev_start = prev_end - timedelta(days=period_days)

        # Get current period metrics
        current_bi = self.get_business_intelligence(start_date, end_date)
        prev_bi = self.get_business_intelligence(prev_start, prev_end)

        # Calculate changes
        current_revenue = current_bi["summary"]["total_ggr"]
        prev_revenue = prev_bi["summary"]["total_ggr"]
        revenue_change = (
            ((current_revenue - prev_revenue) / prev_revenue * 100)
            if prev_revenue > 0
            else 0
        )

        current_players = current_bi["summary"]["total_active_players"]
        prev_players = prev_bi["summary"]["total_active_players"]
        player_change = (
            ((current_players - prev_players) / prev_players * 100)
            if prev_players > 0
            else 0
        )

        # Generate alerts
        alerts = self._generate_alerts(current_bi)

        # Generate recommendations
        recommendations = self._generate_recommendations(current_bi, revenue_change)

        # Get top games
        top_games = self.get_game_performance(start_date, end_date, limit=5)

        return ExecutiveDashboard(
            period_start=start_date,
            period_end=end_date,
            total_revenue=current_revenue,
            revenue_change_percent=revenue_change,
            active_players=current_players,
            player_change_percent=player_change,
            top_games=top_games,
            key_alerts=alerts,
            recommendations=recommendations,
        )

    def _generate_alerts(self, bi_data: dict[str, Any]) -> list[str]:
        """Generate alerts based on BI data."""
        alerts = []

        summary = bi_data["summary"]

        # Check revenue target
        daily_revenue = summary["total_ggr"] / 7  # Assuming 7-day period
        if daily_revenue < self.kpi_targets["daily_revenue"] * 0.8:
            alerts.append(
                f"Revenue below target: ${daily_revenue:,.0f}/day "
                f"vs ${self.kpi_targets['daily_revenue']:,.0f}/day target"
            )

        # Check retention
        retention = summary["average_retention_rate"]
        if retention < self.kpi_targets["player_retention"]:
            alerts.append(
                f"Retention below target: {retention:.1%} "
                f"vs {self.kpi_targets['player_retention']:.1%} target"
            )

        # Check new player acquisition
        daily_new = summary["total_new_registrations"] / 7
        if daily_new < self.kpi_targets["new_player_target"] * 0.8:
            alerts.append(
                f"New player acquisition low: {daily_new:.0f}/day "
                f"vs {self.kpi_targets['new_player_target']}/day target"
            )

        return alerts

    def _generate_recommendations(
        self,
        bi_data: dict[str, Any],
        revenue_change: float,
    ) -> list[str]:
        """Generate recommendations based on BI data."""
        recommendations = []

        if revenue_change < -10:
            recommendations.append(
                "Launch promotional campaign to boost revenue"
            )
            recommendations.append(
                "Review game portfolio for underperforming titles"
            )

        if bi_data["summary"]["average_retention_rate"] < 0.6:
            recommendations.append(
                "Implement player re-engagement campaigns"
            )
            recommendations.append(
                "Review VIP program benefits"
            )

        # Always include optimization suggestions
        recommendations.append(
            "Continue A/B testing on top-performing games"
        )

        return recommendations

    def print_dashboard(self, dashboard: ExecutiveDashboard) -> None:
        """Print formatted executive dashboard."""
        print("\n" + "=" * 70)
        print("  EXECUTIVE DASHBOARD")
        print(
            f"  Period: {dashboard.period_start.strftime('%Y-%m-%d')} to "
            f"{dashboard.period_end.strftime('%Y-%m-%d')}"
        )
        print("=" * 70)

        print("\n📊 KEY METRICS")
        print("-" * 70)
        revenue_arrow = "↑" if dashboard.revenue_change_percent > 0 else "↓"
        player_arrow = "↑" if dashboard.player_change_percent > 0 else "↓"

        print(f"  Total Revenue:    ${dashboard.total_revenue:>15,.2f}  "
              f"{revenue_arrow} {abs(dashboard.revenue_change_percent):.1f}%")
        print(f"  Active Players:   {dashboard.active_players:>15,}  "
              f"{player_arrow} {abs(dashboard.player_change_percent):.1f}%")

        print("\n🎮 TOP GAMES")
        print("-" * 70)
        for game in dashboard.top_games[:5]:
            print(f"  {game.popularity_rank}. {game.game_id:<20} "
                  f"Bets: ${game.total_bets:>12,.2f}  "
                  f"RTP: {game.rtp_actual:.1f}%")

        if dashboard.key_alerts:
            print("\n⚠️ ALERTS")
            print("-" * 70)
            for alert in dashboard.key_alerts:
                print(f"  • {alert}")

        print("\n💡 RECOMMENDATIONS")
        print("-" * 70)
        for rec in dashboard.recommendations:
            print(f"  • {rec}")

        print("\n" + "=" * 70)


def main() -> None:
    """Example usage of Business Intelligence Engine."""
    print("Business Intelligence Engine")
    print("=" * 50)
    print()
    print("This module provides:")
    print("  - Revenue analytics (GGR, NGR, house edge)")
    print("  - Player metrics (DAU, retention, churn)")
    print("  - Game performance analysis")
    print("  - Executive dashboards")
    print()
    print("Example usage:")
    print("  bi = BusinessIntelligenceEngine(clickhouse_client)")
    print("  dashboard = bi.generate_executive_dashboard(period_days=7)")
    print("  bi.print_dashboard(dashboard)")


if __name__ == "__main__":
    main()
