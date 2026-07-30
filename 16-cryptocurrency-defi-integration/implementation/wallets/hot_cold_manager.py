#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
Hot/Cold Wallet Management with Threshold Sweeping

Production-grade hot/cold wallet management for crypto casino operations:
- Configurable hot wallet thresholds per currency (min/max balance)
- Automatic sweeping from hot to cold when thresholds are exceeded
- Automatic refill from cold to hot when balance drops below minimum
- Multi-currency support with independent thresholds
- Transaction batching for gas optimization
- Alert system for unusual balance changes
- Full audit trail with reconciliation support
- Simulated balance tracking for development/testing

Security model:
- Hot wallet: online, handles player deposits/withdrawals (hours of float)
- Warm wallet: semi-online, intermediate buffer (days of float)
- Cold wallet: offline/air-gapped, bulk storage (HSM/hardware wallet)

Usage:
    manager = HotColdManager()
    manager.configure_currency("ETH", hot_min=5, hot_max=50, warm_max=200)
    manager.process_deposit("ETH", 10.0)
    actions = manager.check_thresholds("ETH")
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class WalletTier(Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class SweepAction(Enum):
    SWEEP_TO_COLD = "sweep_hot_to_cold"
    SWEEP_TO_WARM = "sweep_hot_to_warm"
    REFILL_FROM_WARM = "refill_hot_from_warm"
    REFILL_FROM_COLD = "refill_hot_from_cold"
    WARM_TO_COLD = "sweep_warm_to_cold"
    NONE = "no_action"


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class WalletBalance:
    """Balance state for a single wallet tier and currency."""
    currency: str
    tier: WalletTier
    balance: float = 0.0
    pending_in: float = 0.0
    pending_out: float = 0.0
    address: str = ""
    last_updated: str = ""

    @property
    def available(self) -> float:
        return self.balance - self.pending_out

    @property
    def projected(self) -> float:
        return self.balance + self.pending_in - self.pending_out


@dataclass
class CurrencyConfig:
    """Threshold configuration for a single currency."""
    currency: str
    hot_min: float          # Minimum hot wallet balance (trigger refill)
    hot_max: float          # Maximum hot wallet balance (trigger sweep)
    hot_target: float       # Target hot wallet balance after sweep/refill
    warm_max: float         # Maximum warm wallet balance (sweep to cold)
    warm_target: float      # Target warm wallet balance
    sweep_batch_size: float # Minimum amount to sweep (gas efficiency)
    alert_critical: float   # Balance below this = critical alert
    decimals: int = 18
    enabled: bool = True


@dataclass
class SweepTransaction:
    """Record of a sweep/refill transaction."""
    tx_id: str
    action: SweepAction
    currency: str
    amount: float
    from_tier: WalletTier
    to_tier: WalletTier
    from_address: str
    to_address: str
    status: str = "pending"     # pending, confirmed, failed
    tx_hash: Optional[str] = None
    gas_cost: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confirmed_at: Optional[str] = None


@dataclass
class WalletAlert:
    alert_id: str
    severity: AlertSeverity
    currency: str
    tier: WalletTier
    message: str
    balance: float
    threshold: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HotColdManager:
    """
    Multi-tier wallet management system for crypto casino operations.

    Maintains optimal balance distribution across hot, warm, and cold wallets
    with automatic threshold-based sweeping and refilling.
    """

    # Default addresses (use real addresses in production)
    DEFAULT_ADDRESSES = {
        WalletTier.HOT: {
            "ETH": "0xHOT_ETH_ADDRESS",
            "BTC": "bc1q_HOT_BTC_ADDRESS",
            "USDT": "0xHOT_USDT_ADDRESS",
            "USDC": "0xHOT_USDC_ADDRESS",
        },
        WalletTier.WARM: {
            "ETH": "0xWARM_ETH_ADDRESS",
            "BTC": "bc1q_WARM_BTC_ADDRESS",
            "USDT": "0xWARM_USDT_ADDRESS",
            "USDC": "0xWARM_USDC_ADDRESS",
        },
        WalletTier.COLD: {
            "ETH": "0xCOLD_ETH_MULTISIG",
            "BTC": "bc1q_COLD_BTC_MULTISIG",
            "USDT": "0xCOLD_USDT_MULTISIG",
            "USDC": "0xCOLD_USDC_MULTISIG",
        },
    }

    def __init__(self):
        self.configs: dict[str, CurrencyConfig] = {}
        self.balances: dict[str, dict[WalletTier, WalletBalance]] = {}
        self.transactions: list[SweepTransaction] = []
        self.alerts: list[WalletAlert] = []
        self._tx_counter = 0
        self._alert_counter = 0

        # Initialize default configurations
        self._setup_defaults()

    def _setup_defaults(self):
        """Set up default currency configurations."""
        defaults = {
            "ETH": CurrencyConfig(
                currency="ETH", hot_min=5, hot_max=50, hot_target=25,
                warm_max=200, warm_target=100, sweep_batch_size=2,
                alert_critical=1, decimals=18,
            ),
            "BTC": CurrencyConfig(
                currency="BTC", hot_min=0.5, hot_max=5, hot_target=2,
                warm_max=20, warm_target=10, sweep_batch_size=0.2,
                alert_critical=0.1, decimals=8,
            ),
            "USDT": CurrencyConfig(
                currency="USDT", hot_min=10_000, hot_max=200_000, hot_target=100_000,
                warm_max=1_000_000, warm_target=500_000, sweep_batch_size=5_000,
                alert_critical=2_000, decimals=6,
            ),
            "USDC": CurrencyConfig(
                currency="USDC", hot_min=10_000, hot_max=200_000, hot_target=100_000,
                warm_max=1_000_000, warm_target=500_000, sweep_batch_size=5_000,
                alert_critical=2_000, decimals=6,
            ),
        }
        for currency, config in defaults.items():
            self.configure_currency_obj(config)

    def configure_currency(
        self,
        currency: str,
        hot_min: float,
        hot_max: float,
        warm_max: float,
        hot_target: float = None,  # ty:ignore[invalid-parameter-default]
        warm_target: float = None,  # ty:ignore[invalid-parameter-default]
        sweep_batch_size: float = None,  # ty:ignore[invalid-parameter-default]
        alert_critical: float = None,  # ty:ignore[invalid-parameter-default]
    ):
        """Configure thresholds for a currency."""
        config = CurrencyConfig(
            currency=currency,
            hot_min=hot_min,
            hot_max=hot_max,
            hot_target=hot_target or (hot_min + hot_max) / 2,
            warm_max=warm_max,
            warm_target=warm_target or warm_max / 2,
            sweep_batch_size=sweep_batch_size or hot_min * 0.5,
            alert_critical=alert_critical or hot_min * 0.2,
        )
        self.configure_currency_obj(config)

    def configure_currency_obj(self, config: CurrencyConfig):
        """Configure from CurrencyConfig object."""
        self.configs[config.currency] = config
        if config.currency not in self.balances:
            self.balances[config.currency] = {}
            for tier in WalletTier:
                addr = self.DEFAULT_ADDRESSES.get(tier, {}).get(config.currency, "")
                self.balances[config.currency][tier] = WalletBalance(
                    currency=config.currency,
                    tier=tier,
                    address=addr,
                )

    def set_balance(self, currency: str, tier: WalletTier, balance: float):
        """Set wallet balance (for initialization or sync from blockchain)."""
        if currency not in self.balances:
            raise ValueError(f"Currency {currency} not configured")
        self.balances[currency][tier].balance = balance
        self.balances[currency][tier].last_updated = datetime.now(timezone.utc).isoformat()

    def process_deposit(self, currency: str, amount: float):
        """Record a player deposit (increases hot wallet balance)."""
        self.balances[currency][WalletTier.HOT].balance += amount
        logger.info(f"Deposit: +{amount} {currency} to hot wallet "
                    f"(new balance: {self.balances[currency][WalletTier.HOT].balance})")
        return self.check_thresholds(currency)

    def process_withdrawal(self, currency: str, amount: float) -> bool:
        """
        Process a player withdrawal (decreases hot wallet balance).

        Returns True if sufficient balance, False if insufficient.
        """
        hot = self.balances[currency][WalletTier.HOT]
        if hot.available < amount:
            logger.warning(f"Insufficient hot wallet balance for {amount} {currency} "
                          f"withdrawal (available: {hot.available})")
            return False

        hot.balance -= amount
        logger.info(f"Withdrawal: -{amount} {currency} from hot wallet "
                    f"(new balance: {hot.balance})")
        self.check_thresholds(currency)
        return True

    def check_thresholds(self, currency: str) -> list[SweepTransaction]:
        """
        Check balance thresholds and generate sweep/refill actions.

        Returns list of SweepTransaction actions to execute.
        """
        if currency not in self.configs:
            return []

        config = self.configs[currency]
        hot = self.balances[currency][WalletTier.HOT]
        warm = self.balances[currency][WalletTier.WARM]
        cold = self.balances[currency][WalletTier.COLD]
        actions = []

        # Check critical alert
        if hot.balance < config.alert_critical:
            self._create_alert(
                AlertSeverity.CRITICAL, currency, WalletTier.HOT,
                f"CRITICAL: Hot wallet {currency} balance ({hot.balance}) "
                f"below critical threshold ({config.alert_critical})",
                hot.balance, config.alert_critical,
            )

        # Hot wallet too high -> sweep to warm/cold
        if hot.balance > config.hot_max:
            sweep_amount = hot.balance - config.hot_target
            if sweep_amount >= config.sweep_batch_size:
                if warm.balance < config.warm_max:
                    # Sweep to warm first
                    to_warm = min(sweep_amount, config.warm_max - warm.balance)
                    if to_warm >= config.sweep_batch_size:
                        tx = self._create_sweep(
                            SweepAction.SWEEP_TO_WARM, currency, to_warm,
                            WalletTier.HOT, WalletTier.WARM,
                        )
                        actions.append(tx)
                        sweep_amount -= to_warm

                if sweep_amount >= config.sweep_batch_size:
                    # Remaining goes to cold
                    tx = self._create_sweep(
                        SweepAction.SWEEP_TO_COLD, currency, sweep_amount,
                        WalletTier.HOT, WalletTier.COLD,
                    )
                    actions.append(tx)

        # Hot wallet too low -> refill from warm/cold
        elif hot.balance < config.hot_min:
            refill_amount = config.hot_target - hot.balance

            if warm.balance >= refill_amount:
                tx = self._create_sweep(
                    SweepAction.REFILL_FROM_WARM, currency, refill_amount,
                    WalletTier.WARM, WalletTier.HOT,
                )
                actions.append(tx)
            elif warm.balance > 0:
                # Partial from warm, rest from cold
                if warm.balance >= config.sweep_batch_size:
                    tx = self._create_sweep(
                        SweepAction.REFILL_FROM_WARM, currency, warm.balance,
                        WalletTier.WARM, WalletTier.HOT,
                    )
                    actions.append(tx)
                    refill_amount -= warm.balance

                if refill_amount >= config.sweep_batch_size:
                    tx = self._create_sweep(
                        SweepAction.REFILL_FROM_COLD, currency, refill_amount,
                        WalletTier.COLD, WalletTier.HOT,
                    )
                    actions.append(tx)
                    self._create_alert(
                        AlertSeverity.HIGH, currency, WalletTier.HOT,
                        f"Cold wallet refill required for {currency}: {refill_amount}",
                        hot.balance, config.hot_min,
                    )
            else:
                # Only cold available
                tx = self._create_sweep(
                    SweepAction.REFILL_FROM_COLD, currency, refill_amount,
                    WalletTier.COLD, WalletTier.HOT,
                )
                actions.append(tx)
                self._create_alert(
                    AlertSeverity.HIGH, currency, WalletTier.WARM,
                    f"Warm wallet empty for {currency}. Refilling from cold storage.",
                    0, config.warm_target,
                )

        # Warm wallet too high -> sweep to cold
        if warm.balance > config.warm_max:
            sweep_amount = warm.balance - config.warm_target
            if sweep_amount >= config.sweep_batch_size:
                tx = self._create_sweep(
                    SweepAction.WARM_TO_COLD, currency, sweep_amount,
                    WalletTier.WARM, WalletTier.COLD,
                )
                actions.append(tx)

        return actions

    def _create_sweep(
        self, action: SweepAction, currency: str, amount: float,
        from_tier: WalletTier, to_tier: WalletTier,
    ) -> SweepTransaction:
        self._tx_counter += 1
        tx = SweepTransaction(
            tx_id=f"SWP-{self._tx_counter:06d}",
            action=action,
            currency=currency,
            amount=round(amount, 8),
            from_tier=from_tier,
            to_tier=to_tier,
            from_address=self.balances[currency][from_tier].address,
            to_address=self.balances[currency][to_tier].address,
        )
        self.transactions.append(tx)

        # Simulate balance update
        self.balances[currency][from_tier].balance -= amount
        self.balances[currency][to_tier].balance += amount

        logger.info(f"[{tx.tx_id}] {action.value}: {amount} {currency} "
                    f"({from_tier.value} -> {to_tier.value})")

        return tx

    def _create_alert(
        self, severity: AlertSeverity, currency: str, tier: WalletTier,
        message: str, balance: float, threshold: float,
    ):
        self._alert_counter += 1
        alert = WalletAlert(
            alert_id=f"WLT-ALT-{self._alert_counter:04d}",
            severity=severity,
            currency=currency,
            tier=tier,
            message=message,
            balance=balance,
            threshold=threshold,
        )
        self.alerts.append(alert)
        log_fn = {
            AlertSeverity.LOW: logger.info,
            AlertSeverity.MEDIUM: logger.warning,
            AlertSeverity.HIGH: logger.warning,
            AlertSeverity.CRITICAL: logger.critical,
        }
        log_fn[severity](f"[{alert.alert_id}] {message}")

    def get_dashboard(self) -> dict:
        """Generate dashboard data for all currencies and tiers."""
        dashboard: dict[str, Any] = {"currencies": {}, "alerts": [], "recent_transactions": []}

        for currency, tiers in self.balances.items():
            config = self.configs.get(currency)
            currency_data: dict[str, Any] = {"tiers": {}, "total": 0.0}

            for tier, balance in tiers.items():
                currency_data["tiers"][tier.value] = {
                    "balance": round(balance.balance, 8),
                    "address": balance.address,
                    "last_updated": balance.last_updated,
                }
                currency_data["total"] += balance.balance

            if config:
                hot_bal = tiers[WalletTier.HOT].balance
                currency_data["hot_wallet_status"] = (
                    "CRITICAL" if hot_bal < config.alert_critical else
                    "LOW" if hot_bal < config.hot_min else
                    "HIGH" if hot_bal > config.hot_max else
                    "NORMAL"  # ty:ignore[invalid-assignment]
                )
                currency_data["hot_utilization_pct"] = round(
                    hot_bal / config.hot_max * 100, 1
                ) if config.hot_max > 0 else 0

            currency_data["total"] = round(currency_data["total"], 8)
            dashboard["currencies"][currency] = currency_data

        dashboard["alerts"] = [
            {
                "alert_id": a.alert_id,
                "severity": a.severity.value,
                "currency": a.currency,
                "message": a.message,
                "timestamp": a.timestamp,
            }
            for a in self.alerts[-10:]  # Last 10 alerts
        ]

        dashboard["recent_transactions"] = [
            {
                "tx_id": t.tx_id,
                "action": t.action.value,
                "currency": t.currency,
                "amount": t.amount,
                "from": t.from_tier.value,
                "to": t.to_tier.value,
                "status": t.status,
                "timestamp": t.timestamp,
            }
            for t in self.transactions[-20:]
        ]

        return dashboard

    def reconcile(self, currency: str, actual_balances: dict[str, float]) -> dict:
        """
        Reconcile internal balances against on-chain actual balances.

        Args:
            currency: Currency to reconcile
            actual_balances: {"hot": 25.5, "warm": 100.2, "cold": 500.0}
        """
        discrepancies = []
        for tier_name, actual in actual_balances.items():
            tier = WalletTier(tier_name)
            internal = self.balances[currency][tier].balance
            diff = actual - internal

            if abs(diff) > 0.0001:
                discrepancies.append({
                    "tier": tier_name,
                    "internal_balance": round(internal, 8),
                    "actual_balance": round(actual, 8),
                    "discrepancy": round(diff, 8),
                    "severity": "HIGH" if abs(diff) / max(actual, 0.01) > 0.01 else "LOW",
                })

        return {
            "currency": currency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discrepancies_found": len(discrepancies),
            "status": "CLEAN" if not discrepancies else "DISCREPANCY",
            "details": discrepancies,
        }


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 72)
    print("HOT/COLD WALLET MANAGER - Crypto Casino Treasury")
    print("=" * 72)

    manager = HotColdManager()

    # Initialize balances
    manager.set_balance("ETH", WalletTier.HOT, 30.0)
    manager.set_balance("ETH", WalletTier.WARM, 100.0)
    manager.set_balance("ETH", WalletTier.COLD, 500.0)
    manager.set_balance("USDT", WalletTier.HOT, 150_000)
    manager.set_balance("USDT", WalletTier.WARM, 500_000)
    manager.set_balance("USDT", WalletTier.COLD, 2_000_000)

    print("\n--- Initial State ---")
    for currency in ["ETH", "USDT"]:
        for tier in WalletTier:
            bal = manager.balances[currency][tier].balance
            print(f"  {currency} {tier.value:>5}: {bal:>14,.4f}")

    # Simulate activity
    print("\n--- Simulating deposits ---")
    manager.process_deposit("ETH", 15.0)
    manager.process_deposit("ETH", 20.0)  # This should trigger sweep (65 > 50 max)

    print("\n--- Simulating withdrawals ---")
    for _ in range(8):
        manager.process_withdrawal("USDT", 25_000)  # Drains hot wallet

    # Check all currencies
    print("\n--- Final threshold check ---")
    for currency in ["ETH", "USDT", "BTC", "USDC"]:
        actions = manager.check_thresholds(currency)
        if actions:
            for a in actions:
                print(f"  [{a.tx_id}] {a.action.value}: {a.amount} {a.currency}")

    # Dashboard
    print("\n" + "=" * 72)
    print("WALLET DASHBOARD")
    print("=" * 72)
    dashboard = manager.get_dashboard()
    for currency, data in dashboard["currencies"].items():
        if data["total"] > 0:
            print(f"\n  {currency}:")
            for tier, info in data["tiers"].items():
                print(f"    {tier:>5}: {info['balance']:>14,.4f}")
            print(f"    Total: {data['total']:>14,.4f}")
            print(f"    Hot Status: {data.get('hot_wallet_status', 'N/A')}")

    # Reconciliation
    print("\n--- Reconciliation ---")
    recon = manager.reconcile("ETH", {
        "hot": manager.balances["ETH"][WalletTier.HOT].balance + 0.005,  # Small discrepancy
        "warm": manager.balances["ETH"][WalletTier.WARM].balance,
        "cold": manager.balances["ETH"][WalletTier.COLD].balance,
    })
    print(json.dumps(recon, indent=2))
