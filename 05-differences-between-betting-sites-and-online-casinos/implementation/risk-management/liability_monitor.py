#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 05, Differences Between Betting Sites and Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 3: Betting vs Casino - Real-Time Liability Monitoring

Production-grade liability monitoring system for sportsbook operations.
Tracks liability exposure per event, market, and selection with:
- Real-time dashboard data generation
- Alert escalation (INFO -> WARNING -> CRITICAL -> SUSPEND)
- Liability heatmap per sport/league
- Worst-case scenario analysis
- Hedging recommendations when exposure is unbalanced
- PnL projection based on current book

Usage:
    monitor = LiabilityMonitor(config=MonitorConfig())
    monitor.ingest_bet(bet_data)
    dashboard = monitor.get_dashboard()
    alerts = monitor.get_active_alerts()
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    SUSPEND = "suspend"


@dataclass
class MonitorConfig:
    """Monitoring thresholds as percentage of liability cap."""
    info_threshold: float = 0.50        # 50% of cap
    warning_threshold: float = 0.70     # 70% of cap
    critical_threshold: float = 0.85    # 85% of cap
    suspend_threshold: float = 0.95     # 95% of cap -> suspend market
    imbalance_alert: float = 0.80       # Alert if 80%+ liability on one side
    sharp_exposure_alert: float = 0.40  # Alert if 40%+ exposure from sharps
    refresh_interval_seconds: int = 5   # Dashboard refresh rate


@dataclass
class Alert:
    alert_id: str
    level: AlertLevel
    event_id: str
    market_id: str
    selection_id: Optional[str]
    message: str
    liability_pct: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged: bool = False


@dataclass
class SelectionBook:
    """Book for a single selection within a market."""
    selection_id: str
    selection_name: str
    total_stakes: float = 0.0
    total_potential_payout: float = 0.0
    bet_count: int = 0
    sharp_stakes: float = 0.0
    max_single_bet: float = 0.0
    avg_odds: float = 0.0
    _odds_sum: float = 0.0

    @property
    def net_liability(self) -> float:
        return self.total_potential_payout - self.total_stakes

    def add_bet(self, stake: float, odds: float, is_sharp: bool = False):
        self.total_stakes += stake
        self.total_potential_payout += stake * odds
        self.bet_count += 1
        self._odds_sum += odds
        self.avg_odds = self._odds_sum / self.bet_count
        self.max_single_bet = max(self.max_single_bet, stake)
        if is_sharp:
            self.sharp_stakes += stake


@dataclass
class MarketBook:
    """Book for a complete market (e.g., 1X2 with Home/Draw/Away)."""
    market_id: str
    market_type: str
    event_id: str
    event_name: str
    sport: str
    league: str
    liability_cap: float
    selections: dict[str, SelectionBook] = field(default_factory=dict)
    status: str = "open"          # open, suspended, settled
    is_live: bool = False

    @property
    def total_stakes(self) -> float:
        return sum(s.total_stakes for s in self.selections.values())

    @property
    def total_bets(self) -> int:
        return sum(s.bet_count for s in self.selections.values())

    @property
    def worst_case_liability(self) -> float:
        """Maximum loss if the worst outcome occurs."""
        if not self.selections:
            return 0.0
        # For each possible outcome, calculate net PnL
        total_stakes = self.total_stakes
        worst = 0.0
        for sel in self.selections.values():
            # If this selection wins: pay out its liability, keep all other stakes
            loss = sel.total_potential_payout - total_stakes
            worst = max(worst, loss)
        return max(0, worst)

    @property
    def best_case_pnl(self) -> float:
        """Best PnL if the least backed outcome wins."""
        if not self.selections:
            return 0.0
        total_stakes = self.total_stakes
        best = float("-inf")
        for sel in self.selections.values():
            pnl = total_stakes - sel.total_potential_payout
            best = max(best, pnl)
        return best

    @property
    def imbalance_ratio(self) -> float:
        """How unbalanced the book is (0=perfect, 1=completely one-sided)."""
        if not self.selections or len(self.selections) < 2:
            return 0.0
        liabilities = [s.net_liability for s in self.selections.values()]
        max_l = max(liabilities)
        min_l = min(liabilities)
        total = sum(abs(l) for l in liabilities)
        if total == 0:
            return 0.0
        return (max_l - min_l) / total

    @property
    def sharp_exposure_pct(self) -> float:
        """Percentage of total stakes from sharp bettors."""
        total = self.total_stakes
        if total == 0:
            return 0.0
        sharp = sum(s.sharp_stakes for s in self.selections.values())
        return sharp / total

    def pnl_by_outcome(self) -> dict[str, float]:
        """Calculate PnL for each possible outcome."""
        total_stakes = self.total_stakes
        return {
            sel_id: round(total_stakes - sel.total_potential_payout, 2)
            for sel_id, sel in self.selections.items()
        }


class LiabilityMonitor:
    """
    Real-time liability monitoring for sportsbook trading operations.

    Ingests bet data, maintains market books, generates alerts,
    and produces dashboard-ready data for trading terminals.
    """

    def __init__(self, config: MonitorConfig = None):  # ty:ignore[invalid-parameter-default]
        self.config = config or MonitorConfig()
        self.markets: dict[str, MarketBook] = {}
        self.alerts: list[Alert] = []
        self._alert_counter = 0

    def register_market(
        self,
        market_id: str,
        market_type: str,
        event_id: str,
        event_name: str,
        sport: str,
        league: str,
        selections: list[dict],
        liability_cap: float,
        is_live: bool = False,
    ):
        """Register a new market for monitoring."""
        book = MarketBook(
            market_id=market_id,
            market_type=market_type,
            event_id=event_id,
            event_name=event_name,
            sport=sport,
            league=league,
            liability_cap=liability_cap,
            is_live=is_live,
        )
        for sel in selections:
            book.selections[sel["id"]] = SelectionBook(
                selection_id=sel["id"],
                selection_name=sel["name"],
            )
        self.markets[market_id] = book
        logger.info(f"Registered market {market_id}: {event_name} ({market_type})")

    def ingest_bet(
        self,
        market_id: str,
        selection_id: str,
        stake: float,
        odds: float,
        is_sharp: bool = False,
    ):
        """Record a bet and check alerts."""
        if market_id not in self.markets:
            logger.error(f"Market {market_id} not registered")
            return

        book = self.markets[market_id]
        if book.status != "open":
            logger.warning(f"Market {market_id} is {book.status}, rejecting bet")
            return

        if selection_id not in book.selections:
            logger.error(f"Selection {selection_id} not in market {market_id}")
            return

        book.selections[selection_id].add_bet(stake, odds, is_sharp)
        self._evaluate_alerts(market_id)

    def _evaluate_alerts(self, market_id: str):
        """Check all alert conditions for a market."""
        book = self.markets[market_id]
        usage = book.worst_case_liability / book.liability_cap if book.liability_cap > 0 else 0

        # Liability threshold alerts
        if usage >= self.config.suspend_threshold:
            self._create_alert(
                AlertLevel.SUSPEND, book.event_id, market_id, None,
                f"SUSPEND: Liability at {usage*100:.1f}% of cap "
                f"(${book.worst_case_liability:,.2f} / ${book.liability_cap:,.2f})",
                usage,
            )
            book.status = "suspended"
            logger.critical(f"Market {market_id} SUSPENDED: liability at {usage*100:.1f}%")
        elif usage >= self.config.critical_threshold:
            self._create_alert(
                AlertLevel.CRITICAL, book.event_id, market_id, None,
                f"CRITICAL: Liability at {usage*100:.1f}% of cap",
                usage,
            )
        elif usage >= self.config.warning_threshold:
            self._create_alert(
                AlertLevel.WARNING, book.event_id, market_id, None,
                f"WARNING: Liability at {usage*100:.1f}% of cap",
                usage,
            )
        elif usage >= self.config.info_threshold:
            self._create_alert(
                AlertLevel.INFO, book.event_id, market_id, None,
                f"INFO: Liability at {usage*100:.1f}% of cap",
                usage,
            )

        # Imbalance alert
        if book.imbalance_ratio > self.config.imbalance_alert:
            self._create_alert(
                AlertLevel.WARNING, book.event_id, market_id, None,
                f"IMBALANCE: Book is {book.imbalance_ratio*100:.1f}% imbalanced. "
                f"Consider hedging.",
                book.imbalance_ratio,
            )

        # Sharp exposure alert
        if book.sharp_exposure_pct > self.config.sharp_exposure_alert:
            self._create_alert(
                AlertLevel.WARNING, book.event_id, market_id, None,
                f"SHARP: {book.sharp_exposure_pct*100:.1f}% of stakes from sharp bettors",
                book.sharp_exposure_pct,
            )

    def _create_alert(
        self, level: AlertLevel, event_id: str, market_id: str,
        selection_id: Optional[str], message: str, liability_pct: float,
    ):
        self._alert_counter += 1
        alert = Alert(
            alert_id=f"ALT-{self._alert_counter:06d}",
            level=level,
            event_id=event_id,
            market_id=market_id,
            selection_id=selection_id,
            message=message,
            liability_pct=liability_pct,
        )
        self.alerts.append(alert)
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
            AlertLevel.SUSPEND: logger.critical,
        }
        log_fn[level](f"[{alert.alert_id}] {message}")

    def get_active_alerts(self, min_level: AlertLevel = AlertLevel.WARNING) -> list[dict]:
        """Get unacknowledged alerts above minimum level."""
        levels = [AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.SUSPEND]
        min_idx = levels.index(min_level)
        return [
            {
                "alert_id": a.alert_id,
                "level": a.level.value,
                "event_id": a.event_id,
                "market_id": a.market_id,
                "message": a.message,
                "timestamp": a.timestamp,
            }
            for a in self.alerts
            if not a.acknowledged and levels.index(a.level) >= min_idx
        ]

    def get_hedging_recommendation(self, market_id: str) -> dict:
        """
        Calculate hedging recommendations for an unbalanced book.

        Returns stake amounts needed on underexposed selections
        to balance the book (guarantee profit regardless of outcome).
        """
        if market_id not in self.markets:
            return {"error": "Market not found"}

        book = self.markets[market_id]
        pnl_map = book.pnl_by_outcome()
        total_stakes = book.total_stakes

        if total_stakes == 0:
            return {"market_id": market_id, "recommendation": "No bets placed yet"}

        # Find the outcome that would cause the biggest loss
        worst_outcome = min(pnl_map, key=pnl_map.get)  # ty:ignore[no-matching-overload]
        worst_pnl = pnl_map[worst_outcome]

        if worst_pnl >= 0:
            return {
                "market_id": market_id,
                "recommendation": "Book is balanced - no hedging needed",
                "guaranteed_profit": round(min(pnl_map.values()), 2),
            }

        # Calculate lay/hedge amounts
        hedges = []
        for sel_id, sel in book.selections.items():
            outcome_pnl = pnl_map[sel_id]
            if outcome_pnl < min(pnl_map.values()) * 0.5:
                # This outcome needs hedging
                target_pnl = abs(worst_pnl) * 0.5  # Aim to halve the worst case
                # To hedge: we need to bet on this outcome at current odds
                hedge_stake = target_pnl / (sel.avg_odds - 1) if sel.avg_odds > 1 else 0
                hedges.append({
                    "selection": sel.selection_name,
                    "selection_id": sel_id,
                    "current_pnl_if_wins": outcome_pnl,
                    "recommended_hedge_stake": round(hedge_stake, 2),
                    "estimated_odds": round(sel.avg_odds, 3),
                })

        return {
            "market_id": market_id,
            "current_worst_case": round(worst_pnl, 2),
            "worst_outcome": worst_outcome,
            "hedges": hedges,
        }

    def get_dashboard(self) -> dict:
        """Generate dashboard data for all monitored markets."""
        markets_data = []
        total_exposure = 0
        total_stakes = 0

        for mid, book in self.markets.items():
            exposure = book.worst_case_liability
            total_exposure += exposure
            total_stakes += book.total_stakes

            markets_data.append({
                "market_id": mid,
                "event_name": book.event_name,
                "market_type": book.market_type,
                "sport": book.sport,
                "status": book.status,
                "is_live": book.is_live,
                "total_stakes": round(book.total_stakes, 2),
                "total_bets": book.total_bets,
                "worst_case_liability": round(exposure, 2),
                "best_case_pnl": round(book.best_case_pnl, 2),
                "liability_cap": book.liability_cap,
                "usage_pct": round(exposure / book.liability_cap * 100, 1) if book.liability_cap > 0 else 0,
                "imbalance_pct": round(book.imbalance_ratio * 100, 1),
                "sharp_exposure_pct": round(book.sharp_exposure_pct * 100, 1),
                "pnl_by_outcome": book.pnl_by_outcome(),
            })

        # Sort by usage (most exposed first)
        markets_data.sort(key=lambda x: x["usage_pct"], reverse=True)

        # Sport heatmap
        sport_exposure = {}
        for book in self.markets.values():
            key = f"{book.sport}/{book.league}"
            if key not in sport_exposure:
                sport_exposure[key] = {"exposure": 0, "stakes": 0, "markets": 0}
            sport_exposure[key]["exposure"] += book.worst_case_liability
            sport_exposure[key]["stakes"] += book.total_stakes
            sport_exposure[key]["markets"] += 1

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_markets": len(self.markets),
                "active_markets": sum(1 for m in self.markets.values() if m.status == "open"),
                "suspended_markets": sum(1 for m in self.markets.values() if m.status == "suspended"),
                "total_stakes": round(total_stakes, 2),
                "total_exposure": round(total_exposure, 2),
                "active_alerts": len(self.get_active_alerts(AlertLevel.WARNING)),
                "critical_alerts": len(self.get_active_alerts(AlertLevel.CRITICAL)),
            },
            "markets": markets_data,
            "sport_heatmap": sport_exposure,
            "alerts": self.get_active_alerts(),
        }


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    monitor = LiabilityMonitor(config=MonitorConfig(
        warning_threshold=0.60,
        critical_threshold=0.80,
        suspend_threshold=0.95,
    ))

    print("=" * 72)
    print("REAL-TIME LIABILITY MONITOR - Sportsbook Trading")
    print("=" * 72)

    # Register markets
    monitor.register_market(
        market_id="MKT-001", market_type="football_1x2",
        event_id="EVT-001", event_name="Arsenal vs Chelsea",
        sport="football", league="premier_league",
        selections=[
            {"id": "HOME", "name": "Arsenal"},
            {"id": "DRAW", "name": "Draw"},
            {"id": "AWAY", "name": "Chelsea"},
        ],
        liability_cap=75_000,
    )

    monitor.register_market(
        market_id="MKT-002", market_type="tennis_match_winner",
        event_id="EVT-002", event_name="Djokovic vs Alcaraz",
        sport="tennis", league="grand_slam",
        selections=[
            {"id": "P1", "name": "Djokovic"},
            {"id": "P2", "name": "Alcaraz"},
        ],
        liability_cap=40_000,
    )

    # Simulate bet flow
    print("\n--- Simulating bet flow ---\n")

    bets = [
        # Arsenal match - heavy on Home
        ("MKT-001", "HOME", 5_000, 2.10, False),
        ("MKT-001", "HOME", 8_000, 2.05, True),     # Sharp
        ("MKT-001", "HOME", 3_000, 2.10, False),
        ("MKT-001", "DRAW", 1_000, 3.40, False),
        ("MKT-001", "AWAY", 500, 3.60, False),
        ("MKT-001", "HOME", 12_000, 2.00, True),    # Big sharp bet
        ("MKT-001", "HOME", 6_000, 1.95, False),
        # Tennis match
        ("MKT-002", "P1", 10_000, 1.60, False),
        ("MKT-002", "P1", 15_000, 1.55, True),      # Sharp
        ("MKT-002", "P2", 2_000, 2.50, False),
    ]

    for mkt, sel, stake, odds, sharp in bets:
        tag = " [SHARP]" if sharp else ""
        print(f"  BET: {mkt}/{sel} ${stake:>8,.2f} @ {odds:.2f}{tag}")
        monitor.ingest_bet(mkt, sel, stake, odds, sharp)

    # Dashboard
    print("\n" + "=" * 72)
    print("DASHBOARD")
    print("=" * 72)
    dashboard = monitor.get_dashboard()
    print(json.dumps(dashboard["summary"], indent=2))

    print("\n--- Market Details ---")
    for m in dashboard["markets"]:
        print(f"\n  {m['event_name']} ({m['market_type']})")
        print(f"    Status: {m['status']} | Stakes: ${m['total_stakes']:,.2f} | Bets: {m['total_bets']}")
        print(f"    Worst-case: ${m['worst_case_liability']:,.2f} | Usage: {m['usage_pct']}%")
        print(f"    Imbalance: {m['imbalance_pct']}% | Sharp: {m['sharp_exposure_pct']}%")
        print(f"    PnL by outcome: {m['pnl_by_outcome']}")

    # Hedging recommendation
    print("\n--- Hedging Recommendations ---")
    for mid in ["MKT-001", "MKT-002"]:
        hedge = monitor.get_hedging_recommendation(mid)
        print(f"\n  {mid}:")
        print(json.dumps(hedge, indent=4))

    # Alerts
    print("\n--- Active Alerts ---")
    for alert in dashboard["alerts"]:
        print(f"  [{alert['level'].upper():>8s}] {alert['message']}")
