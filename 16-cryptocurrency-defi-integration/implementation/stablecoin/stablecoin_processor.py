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
USDT/USDC Deposit & Withdrawal Processor

Stablecoin payment processor for crypto casino operations:
- Multi-chain USDT/USDC support (Ethereum, Polygon, Arbitrum, BSC, Tron)
- Deposit detection with configurable confirmation requirements
- Withdrawal processing with daily limits and approval workflow
- Automatic chain selection based on fees and speed
- De-peg monitoring and circuit breaker
- Reconciliation against on-chain balances
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class StablecoinType(Enum):
    USDT = "USDT"
    USDC = "USDC"
    DAI = "DAI"
    BUSD = "BUSD"

class Chain(Enum):
    ETHEREUM = "ethereum"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    BSC = "bsc"
    TRON = "tron"
    AVALANCHE = "avalanche"

CHAIN_CONFIG = {
    Chain.ETHEREUM: {"confirmations": 12, "avg_fee_usd": 5.00, "avg_time_sec": 180,
                     "supported": [StablecoinType.USDT, StablecoinType.USDC, StablecoinType.DAI]},
    Chain.POLYGON: {"confirmations": 30, "avg_fee_usd": 0.01, "avg_time_sec": 10,
                    "supported": [StablecoinType.USDT, StablecoinType.USDC]},
    Chain.ARBITRUM: {"confirmations": 1, "avg_fee_usd": 0.10, "avg_time_sec": 5,
                     "supported": [StablecoinType.USDT, StablecoinType.USDC]},
    Chain.BSC: {"confirmations": 15, "avg_fee_usd": 0.05, "avg_time_sec": 15,
                "supported": [StablecoinType.USDT, StablecoinType.USDC, StablecoinType.BUSD]},
    Chain.TRON: {"confirmations": 20, "avg_fee_usd": 1.00, "avg_time_sec": 6,
                 "supported": [StablecoinType.USDT]},
}

@dataclass
class DepositRecord:
    deposit_id: str
    player_id: str
    stablecoin: StablecoinType
    chain: Chain
    amount: float
    tx_hash: str
    confirmations: int = 0
    required_confirmations: int = 0
    credited: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class WithdrawalRecord:
    withdrawal_id: str
    player_id: str
    stablecoin: StablecoinType
    chain: Chain
    amount: float
    to_address: str
    fee: float = 0.0
    status: str = "pending"
    tx_hash: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class StablecoinProcessor:
    """Multi-chain stablecoin deposit/withdrawal processor."""

    DE_PEG_THRESHOLD = 0.02  # 2% deviation triggers circuit breaker
    MIN_DEPOSIT = 5.0
    MAX_DEPOSIT = 100_000.0
    DAILY_WITHDRAWAL_LIMIT = 50_000.0

    def __init__(self):
        self.deposits: list[DepositRecord] = []
        self.withdrawals: list[WithdrawalRecord] = []
        self.player_balances: dict[str, float] = {}
        self.daily_withdrawn: dict[str, float] = {}
        self._dep_counter = 0
        self._wd_counter = 0
        self.circuit_breaker_active = False
        # Simulated peg prices
        self.current_pegs = {StablecoinType.USDT: 1.0001, StablecoinType.USDC: 1.0000,
                            StablecoinType.DAI: 0.9998, StablecoinType.BUSD: 1.0000}

    def recommend_chain(self, stablecoin: StablecoinType, amount: float) -> Chain:
        """Recommend optimal chain based on fees and speed."""
        options = []
        for chain, config in CHAIN_CONFIG.items():
            if stablecoin in config["supported"]:  # ty:ignore[unsupported-operator]
                score = config["avg_fee_usd"] * 2 + config["avg_time_sec"] * 0.1  # ty:ignore[unsupported-operator]
                options.append((chain, score, config))
        options.sort(key=lambda x: x[1])
        best = options[0][0] if options else Chain.POLYGON
        logger.info(f"Recommended chain for {amount} {stablecoin.value}: {best.value}")
        return best

    def process_deposit(self, player_id: str, stablecoin: StablecoinType,
                        chain: Chain, amount: float, tx_hash: str) -> DepositRecord:
        if self.circuit_breaker_active:
            raise ValueError("Circuit breaker active - deposits suspended")
        if amount < self.MIN_DEPOSIT or amount > self.MAX_DEPOSIT:
            raise ValueError(f"Amount must be between ${self.MIN_DEPOSIT} and ${self.MAX_DEPOSIT:,.0f}")

        self._dep_counter += 1
        config = CHAIN_CONFIG[chain]
        deposit = DepositRecord(
            deposit_id=f"DEP-{self._dep_counter:08d}", player_id=player_id,
            stablecoin=stablecoin, chain=chain, amount=amount,
            tx_hash=tx_hash, required_confirmations=config["confirmations"],  # ty:ignore[invalid-argument-type]
        )

        # Simulate instant confirmation for demo
        deposit.confirmations = config["confirmations"]  # ty:ignore[invalid-assignment]
        deposit.credited = True
        self.player_balances[player_id] = self.player_balances.get(player_id, 0) + amount

        self.deposits.append(deposit)
        logger.info(f"[{deposit.deposit_id}] Deposited ${amount:,.2f} {stablecoin.value} "
                    f"via {chain.value} for {player_id}")
        return deposit

    def process_withdrawal(self, player_id: str, stablecoin: StablecoinType,
                           chain: Chain, amount: float, to_address: str) -> WithdrawalRecord:
        if self.circuit_breaker_active:
            raise ValueError("Circuit breaker active - withdrawals suspended")

        balance = self.player_balances.get(player_id, 0)
        if amount > balance:
            raise ValueError(f"Insufficient balance: ${balance:,.2f} < ${amount:,.2f}")

        daily = self.daily_withdrawn.get(player_id, 0)
        if daily + amount > self.DAILY_WITHDRAWAL_LIMIT:
            raise ValueError(f"Daily limit exceeded: ${daily + amount:,.2f} > ${self.DAILY_WITHDRAWAL_LIMIT:,.2f}")

        fee = CHAIN_CONFIG[chain]["avg_fee_usd"]
        self._wd_counter += 1

        withdrawal = WithdrawalRecord(
            withdrawal_id=f"WD-{self._wd_counter:08d}", player_id=player_id,
            stablecoin=stablecoin, chain=chain, amount=amount,
            to_address=to_address, fee=fee, status="completed",  # ty:ignore[invalid-argument-type]
            tx_hash=f"0x{'B' * 64}",
        )

        self.player_balances[player_id] -= amount
        self.daily_withdrawn[player_id] = daily + amount
        self.withdrawals.append(withdrawal)
        logger.info(f"[{withdrawal.withdrawal_id}] Withdrew ${amount:,.2f} {stablecoin.value} "
                    f"via {chain.value} (fee: ${fee:.2f})")
        return withdrawal

    def check_peg(self, stablecoin: StablecoinType) -> dict:
        price = self.current_pegs.get(stablecoin, 1.0)
        deviation = abs(price - 1.0)
        de_pegged = deviation > self.DE_PEG_THRESHOLD
        if de_pegged:
            self.circuit_breaker_active = True
            logger.critical(f"DE-PEG DETECTED: {stablecoin.value} at ${price} ({deviation*100:.2f}% deviation)")
        return {"stablecoin": stablecoin.value, "price": price, "deviation_pct": round(deviation*100, 3),
                "de_pegged": de_pegged, "circuit_breaker": self.circuit_breaker_active}

    def get_summary(self) -> dict:
        return {
            "total_deposits": len(self.deposits),
            "total_withdrawals": len(self.withdrawals),
            "total_deposited": round(sum(d.amount for d in self.deposits), 2),
            "total_withdrawn": round(sum(w.amount for w in self.withdrawals), 2),
            "active_balances": {k: round(v, 2) for k, v in self.player_balances.items() if v > 0},
            "circuit_breaker": self.circuit_breaker_active,
        }

if __name__ == "__main__":
    processor = StablecoinProcessor()
    print("=" * 60)
    print("STABLECOIN PROCESSOR - Crypto Casino")
    print("=" * 60)

    # Chain recommendations
    print("\n--- Chain Recommendations ---")
    for coin in [StablecoinType.USDT, StablecoinType.USDC]:
        chain = processor.recommend_chain(coin, 1000)
        config = CHAIN_CONFIG[chain]
        print(f"  {coin.value}: {chain.value} (fee: ${config['avg_fee_usd']}, time: {config['avg_time_sec']}s)")

    # Process deposits
    print("\n--- Deposits ---")
    processor.process_deposit("PLR-001", StablecoinType.USDT, Chain.POLYGON, 5000, "0xDEP1")
    processor.process_deposit("PLR-002", StablecoinType.USDC, Chain.ARBITRUM, 10000, "0xDEP2")
    processor.process_deposit("PLR-001", StablecoinType.USDT, Chain.POLYGON, 2000, "0xDEP3")

    # Process withdrawals
    print("\n--- Withdrawals ---")
    processor.process_withdrawal("PLR-001", StablecoinType.USDT, Chain.POLYGON, 3000, "0xPLAYER_WALLET")
    processor.process_withdrawal("PLR-002", StablecoinType.USDC, Chain.ARBITRUM, 5000, "0xPLAYER_WALLET2")

    # Peg check
    print("\n--- Peg Status ---")
    for coin in StablecoinType:
        status = processor.check_peg(coin)
        print(f"  {status['stablecoin']}: ${status['price']} (deviation: {status['deviation_pct']}%)")

    print(f"\n--- Summary ---")
    print(json.dumps(processor.get_summary(), indent=2))
