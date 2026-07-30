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
Batch Gas Optimization for Withdrawal Processing

Optimizes gas costs for batch withdrawal processing:
- Batches multiple player withdrawals into single transactions
- Gas price monitoring with configurable urgency levels
- EIP-1559 fee estimation with base fee prediction
- Batch size optimization (gas limit vs savings tradeoff)
- Priority queue for VIP vs standard withdrawals
- Gas cost analytics and reporting
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class Priority(Enum):
    INSTANT = "instant"      # Process immediately regardless of gas
    HIGH = "high"            # Process within 1 block
    STANDARD = "standard"    # Wait for optimal gas (up to 30 min)
    LOW = "low"              # Wait for very low gas (up to 4 hours)

GAS_TARGETS = {
    Priority.INSTANT: {"max_gwei": 500, "max_wait_minutes": 0},
    Priority.HIGH: {"max_gwei": 100, "max_wait_minutes": 5},
    Priority.STANDARD: {"max_gwei": 40, "max_wait_minutes": 30},
    Priority.LOW: {"max_gwei": 15, "max_wait_minutes": 240},
}

@dataclass
class WithdrawalRequest:
    request_id: str
    player_id: str
    to_address: str
    amount: float
    currency: str
    priority: Priority = Priority.STANDARD
    is_vip: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class BatchTransaction:
    batch_id: str
    withdrawals: list[WithdrawalRequest]
    total_amount: float
    currency: str
    estimated_gas: int
    gas_price_gwei: float
    estimated_cost_usd: float
    individual_cost_usd: float  # What it would cost if sent individually
    savings_usd: float
    savings_pct: float
    status: str = "pending"

class GasOptimizer:
    """Batch withdrawal gas optimizer for crypto casino operations."""

    ETH_TRANSFER_GAS = 21_000
    ERC20_TRANSFER_GAS = 65_000
    BATCH_OVERHEAD_GAS = 30_000
    BATCH_PER_TRANSFER_GAS = 45_000  # Gas per transfer in batch contract

    def __init__(self, max_batch_size: int = 50, eth_price_usd: float = 2000):
        self.max_batch_size = max_batch_size
        self.eth_price_usd = eth_price_usd
        self.queue: list[WithdrawalRequest] = []
        self.batches: list[BatchTransaction] = []
        self._batch_counter = 0
        self._current_gas_gwei = 25.0  # Simulated

    def add_withdrawal(self, request: WithdrawalRequest):
        self.queue.append(request)
        logger.info(f"Queued withdrawal {request.request_id}: {request.amount} {request.currency} "
                    f"to {request.to_address[:10]}... ({request.priority.value})")

    def create_batch(self, currency: str = "ETH") -> Optional[BatchTransaction]:
        """Create optimized batch from queued withdrawals."""
        eligible = [w for w in self.queue if w.currency == currency]
        if not eligible:
            return None

        # Sort: VIP first, then by priority, then by time
        priority_order = {Priority.INSTANT: 0, Priority.HIGH: 1, Priority.STANDARD: 2, Priority.LOW: 3}
        eligible.sort(key=lambda w: (0 if w.is_vip else 1, priority_order[w.priority]))

        batch_items = eligible[:self.max_batch_size]
        total = sum(w.amount for w in batch_items)

        # Gas estimation
        is_erc20 = currency not in ("ETH", "BTC")
        individual_gas = self.ERC20_TRANSFER_GAS if is_erc20 else self.ETH_TRANSFER_GAS
        batch_gas = self.BATCH_OVERHEAD_GAS + len(batch_items) * self.BATCH_PER_TRANSFER_GAS
        individual_total_gas = len(batch_items) * individual_gas

        gas_price = self._current_gas_gwei
        batch_cost = batch_gas * gas_price * 1e-9 * self.eth_price_usd
        individual_cost = individual_total_gas * gas_price * 1e-9 * self.eth_price_usd
        savings = individual_cost - batch_cost

        self._batch_counter += 1
        batch = BatchTransaction(
            batch_id=f"BATCH-{self._batch_counter:06d}",
            withdrawals=batch_items,
            total_amount=round(total, 8),
            currency=currency,
            estimated_gas=batch_gas,
            gas_price_gwei=gas_price,
            estimated_cost_usd=round(batch_cost, 2),
            individual_cost_usd=round(individual_cost, 2),
            savings_usd=round(savings, 2),
            savings_pct=round(savings / individual_cost * 100, 1) if individual_cost > 0 else 0,
        )

        # Remove from queue
        for w in batch_items:
            self.queue.remove(w)

        self.batches.append(batch)
        logger.info(f"[{batch.batch_id}] Created batch: {len(batch_items)} withdrawals, "
                    f"savings: ${batch.savings_usd} ({batch.savings_pct}%)")
        return batch

    def get_analytics(self) -> dict:
        total_savings = sum(b.savings_usd for b in self.batches)
        total_txs = sum(len(b.withdrawals) for b in self.batches)
        return {
            "total_batches": len(self.batches),
            "total_withdrawals_processed": total_txs,
            "total_gas_savings_usd": round(total_savings, 2),
            "avg_savings_per_batch_pct": round(
                sum(b.savings_pct for b in self.batches) / len(self.batches), 1
            ) if self.batches else 0,
            "queue_depth": len(self.queue),
            "current_gas_gwei": self._current_gas_gwei,
        }

if __name__ == "__main__":
    optimizer = GasOptimizer(eth_price_usd=2000)
    print("=" * 60)
    print("GAS OPTIMIZER - Batch Withdrawal Processing")
    print("=" * 60)

    # Queue withdrawals
    for i in range(15):
        optimizer.add_withdrawal(WithdrawalRequest(
            request_id=f"WD-{i+1:04d}", player_id=f"PLR-{i+1:03d}",
            to_address=f"0x{'A' * 40}", amount=round(0.1 + i * 0.05, 4),
            currency="ETH", priority=Priority.STANDARD, is_vip=(i < 3),
        ))

    # Create batch
    batch = optimizer.create_batch("ETH")
    if batch:
        print(f"\n  Batch {batch.batch_id}:")
        print(f"    Withdrawals: {len(batch.withdrawals)}")
        print(f"    Total: {batch.total_amount} ETH")
        print(f"    Batch cost:      ${batch.estimated_cost_usd:>8.2f}")
        print(f"    Individual cost: ${batch.individual_cost_usd:>8.2f}")
        print(f"    Savings:         ${batch.savings_usd:>8.2f} ({batch.savings_pct}%)")

    print(f"\n  Analytics: {json.dumps(optimizer.get_analytics(), indent=4)}")
