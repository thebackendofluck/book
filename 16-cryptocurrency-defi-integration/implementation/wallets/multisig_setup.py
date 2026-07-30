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
Gnosis Safe Multi-Sig Treasury Setup

Configures and manages Gnosis Safe (now Safe{Wallet}) multi-signature
wallets for crypto casino treasury operations:
- Safe deployment with configurable owners and threshold
- Role-based access (CFO, CTO, compliance, auditor)
- Transaction proposal and approval workflow
- Spending limits per signer role
- Integration with casino treasury operations
- Audit trail for all multi-sig operations

Typical casino treasury structure:
- Operations Safe (2/3): Daily withdrawals, hot wallet refills
- Treasury Safe (3/5): Large transfers, cold storage access
- Emergency Safe (4/7): Protocol upgrades, emergency actions

Prerequisites:
    pip install web3 safe-eth-py requests

Usage:
    setup = MultisigSetup(network="polygon")
    treasury = setup.create_treasury_safe(
        owners=["0x...", "0x...", "0x..."],
        threshold=2
    )
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Safe Configuration ────────────────────────────────────────────────

class SafeRole(Enum):
    CFO = "cfo"
    CTO = "cto"
    COMPLIANCE = "compliance"
    OPERATIONS = "operations"
    AUDITOR = "auditor"          # View-only, can propose but not approve
    BOARD_MEMBER = "board_member"


class SafeTier(Enum):
    OPERATIONS = "operations"    # Day-to-day: hot wallet refills, small payouts
    TREASURY = "treasury"        # Large movements: cold storage, provider payments
    EMERGENCY = "emergency"      # Protocol changes: contract upgrades, emergency stops


SAFE_NETWORK_ADDRESSES = {
    "mainnet": {
        "safe_proxy_factory": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
        "safe_singleton": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
        "fallback_handler": "0xf48f2B2d2a534e402487b3ee7C18c33Aec0Fe5e4",
        "safe_api": "https://safe-transaction-mainnet.safe.global/api/v1",
    },
    "polygon": {
        "safe_proxy_factory": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
        "safe_singleton": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
        "fallback_handler": "0xf48f2B2d2a534e402487b3ee7C18c33Aec0Fe5e4",
        "safe_api": "https://safe-transaction-polygon.safe.global/api/v1",
    },
    "arbitrum": {
        "safe_proxy_factory": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
        "safe_singleton": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
        "fallback_handler": "0xf48f2B2d2a534e402487b3ee7C18c33Aec0Fe5e4",
        "safe_api": "https://safe-transaction-arbitrum.safe.global/api/v1",
    },
    "bsc": {
        "safe_proxy_factory": "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2",
        "safe_singleton": "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552",
        "fallback_handler": "0xf48f2B2d2a534e402487b3ee7C18c33Aec0Fe5e4",
        "safe_api": "https://safe-transaction-bsc.safe.global/api/v1",
    },
}


@dataclass
class SafeOwner:
    """An owner/signer of a multi-sig safe."""
    address: str
    role: SafeRole
    name: str
    daily_limit: float = 0.0        # Daily spending limit in USD equivalent
    can_propose: bool = True
    can_approve: bool = True
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SafeConfig:
    """Configuration for a multi-sig safe."""
    tier: SafeTier
    name: str
    owners: list[SafeOwner]
    threshold: int                   # Required signatures
    daily_limit_usd: float          # Total daily spending limit
    single_tx_limit_usd: float     # Max single transaction
    allowed_tokens: list[str]       # Token addresses allowed
    auto_approve_below: float       # Auto-approve txs below this USD amount

    @property
    def owner_count(self) -> int:
        return len(self.owners)

    def validate(self) -> list[str]:
        """Validate safe configuration."""
        errors = []
        if self.threshold > self.owner_count:
            errors.append(f"Threshold ({self.threshold}) > owner count ({self.owner_count})")
        if self.threshold < 1:
            errors.append("Threshold must be >= 1")
        if self.owner_count < 2:
            errors.append("Multi-sig requires at least 2 owners")
        if self.threshold < 2:
            errors.append("Threshold should be >= 2 for security")

        # Check role distribution
        roles = [o.role for o in self.owners]
        if SafeRole.CFO not in roles and SafeRole.OPERATIONS not in roles:
            errors.append("Warning: No CFO or Operations role assigned")

        return errors


@dataclass
class MultisigTransaction:
    """A proposed multi-sig transaction."""
    tx_id: str
    safe_address: str
    safe_tier: SafeTier
    to_address: str
    value_wei: int
    data: str
    description: str
    proposed_by: str
    proposed_at: str
    usd_value: float
    approvals: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    status: str = "pending"         # pending, approved, executed, rejected, expired
    tx_hash: Optional[str] = None
    threshold_required: int = 0

    @property
    def approval_count(self) -> int:
        return len(self.approvals)

    @property
    def is_approved(self) -> bool:
        return self.approval_count >= self.threshold_required


# ── Casino Treasury Safe Templates ────────────────────────────────────

CASINO_SAFE_TEMPLATES = {
    SafeTier.OPERATIONS: {
        "name": "Casino Operations Safe",
        "description": "Day-to-day operations: player withdrawals, hot wallet refills",
        "threshold_ratio": 0.5,       # 50% of owners must sign (rounded up)
        "min_threshold": 2,
        "daily_limit_usd": 100_000,
        "single_tx_limit_usd": 25_000,
        "auto_approve_below_usd": 1_000,
        "recommended_owners": [SafeRole.OPERATIONS, SafeRole.CTO, SafeRole.CFO],
    },
    SafeTier.TREASURY: {
        "name": "Casino Treasury Safe",
        "description": "Large transfers: cold storage, provider payments, payroll",
        "threshold_ratio": 0.6,
        "min_threshold": 3,
        "daily_limit_usd": 1_000_000,
        "single_tx_limit_usd": 250_000,
        "auto_approve_below_usd": 0,   # No auto-approve for treasury
        "recommended_owners": [
            SafeRole.CFO, SafeRole.CTO, SafeRole.COMPLIANCE,
            SafeRole.BOARD_MEMBER, SafeRole.OPERATIONS,
        ],
    },
    SafeTier.EMERGENCY: {
        "name": "Casino Emergency Safe",
        "description": "Emergency actions: contract upgrades, pause, protocol changes",
        "threshold_ratio": 0.6,
        "min_threshold": 4,
        "daily_limit_usd": 10_000_000,
        "single_tx_limit_usd": 5_000_000,
        "auto_approve_below_usd": 0,
        "recommended_owners": [
            SafeRole.CFO, SafeRole.CTO, SafeRole.COMPLIANCE,
            SafeRole.BOARD_MEMBER, SafeRole.BOARD_MEMBER,
            SafeRole.OPERATIONS, SafeRole.AUDITOR,
        ],
    },
}


class MultisigSetup:
    """
    Gnosis Safe multi-sig setup and management for crypto casinos.

    Creates and configures multi-sig wallets following casino industry
    best practices for treasury security.
    """

    def __init__(self, network: str = "polygon"):
        if network not in SAFE_NETWORK_ADDRESSES:
            raise ValueError(f"Unsupported network: {network}")
        self.network = network
        self.net_config = SAFE_NETWORK_ADDRESSES[network]
        self.safes: dict[SafeTier, SafeConfig] = {}
        self.transactions: list[MultisigTransaction] = []
        self._tx_counter = 0
        self._deployed_addresses: dict[SafeTier, str] = {}

    def create_safe_config(
        self,
        tier: SafeTier,
        owners: list[dict],
        threshold: Optional[int] = None,
        custom_limits: Optional[dict] = None,
    ) -> SafeConfig:
        """
        Create a safe configuration from template.

        Args:
            tier: Safe tier (OPERATIONS, TREASURY, EMERGENCY)
            owners: List of {"address": "0x...", "role": "cfo", "name": "John"}
            threshold: Override default threshold
            custom_limits: Override default limits

        Returns:
            SafeConfig ready for deployment.
        """
        template = CASINO_SAFE_TEMPLATES[tier]

        safe_owners = [
            SafeOwner(
                address=o["address"],
                role=SafeRole(o.get("role", "operations")),
                name=o.get("name", f"Owner-{i}"),
                daily_limit=o.get("daily_limit", 0),
                can_approve=SafeRole(o.get("role", "operations")) != SafeRole.AUDITOR,
            )
            for i, o in enumerate(owners)
        ]

        if threshold is None:
            threshold = max(
                template["min_threshold"],
                int(len(owners) * template["threshold_ratio"] + 0.5),  # ty:ignore[unsupported-operator]
            )  # ty:ignore[invalid-assignment]

        config = SafeConfig(
            tier=tier,
            name=template["name"],  # ty:ignore[invalid-argument-type]
            owners=safe_owners,
            threshold=threshold,  # ty:ignore[invalid-argument-type]
            daily_limit_usd=custom_limits.get("daily", template["daily_limit_usd"]) if custom_limits else template["daily_limit_usd"],  # ty:ignore[invalid-argument-type]
            single_tx_limit_usd=custom_limits.get("single_tx", template["single_tx_limit_usd"]) if custom_limits else template["single_tx_limit_usd"],  # ty:ignore[invalid-argument-type]
            allowed_tokens=[],
            auto_approve_below=template["auto_approve_below_usd"],  # ty:ignore[invalid-argument-type]
        )

        errors = config.validate()
        if errors:
            for err in errors:
                logger.warning(f"Config validation: {err}")

        self.safes[tier] = config
        logger.info(f"Created {tier.value} safe config: {threshold}/{len(owners)} threshold")

        return config

    def deploy_safe(self, tier: SafeTier, dry_run: bool = True) -> dict:
        """
        Deploy a Gnosis Safe contract.

        Args:
            tier: Which safe to deploy
            dry_run: If True, only return deployment parameters

        Returns:
            Deployment info including safe address.
        """
        if tier not in self.safes:
            raise ValueError(f"No config for {tier.value}. Call create_safe_config first.")

        config = self.safes[tier]
        owner_addresses = [o.address for o in config.owners]

        deployment_params = {
            "network": self.network,
            "chain_id": {"mainnet": 1, "polygon": 137, "arbitrum": 42161, "bsc": 56}[self.network],
            "safe_type": tier.value,
            "safe_name": config.name,
            "owners": owner_addresses,
            "threshold": config.threshold,
            "proxy_factory": self.net_config["safe_proxy_factory"],
            "singleton": self.net_config["safe_singleton"],
            "fallback_handler": self.net_config["fallback_handler"],
            "salt_nonce": int(datetime.now(timezone.utc).timestamp()),
        }

        if dry_run:
            logger.info(f"DRY RUN: Would deploy {config.name} on {self.network}")
            deployment_params["status"] = "dry_run"
            deployment_params["estimated_address"] = f"0x{'0' * 38}FF"  # Placeholder
            return deployment_params

        # Production deployment would use safe-eth-py:
        # from safe_eth.safe import Safe
        # safe = Safe.create(ethereum_client, deployer, owners, threshold)

        logger.info(f"Deploying {config.name} on {self.network}...")
        logger.info("In production, this would call Safe.create() via safe-eth-py")

        return deployment_params

    def propose_transaction(
        self,
        tier: SafeTier,
        to_address: str,
        value_eth: float,
        description: str,
        proposer_address: str,
        data: str = "0x",
    ) -> MultisigTransaction:
        """
        Propose a new multi-sig transaction.

        Validates against spending limits and role permissions.
        """
        if tier not in self.safes:
            raise ValueError(f"No safe configured for {tier.value}")

        config = self.safes[tier]

        # Check proposer is an owner
        proposer = next((o for o in config.owners if o.address.lower() == proposer_address.lower()), None)
        if not proposer:
            raise ValueError(f"Address {proposer_address} is not an owner of {tier.value} safe")
        if not proposer.can_propose:
            raise ValueError(f"Owner {proposer.name} does not have proposal permission")

        # Check single transaction limit (approximate USD value)
        usd_value = value_eth * 2000  # Simplified ETH/USD rate
        if usd_value > config.single_tx_limit_usd:
            raise ValueError(
                f"Transaction value ${usd_value:,.2f} exceeds single tx limit "
                f"${config.single_tx_limit_usd:,.2f} for {tier.value} safe"
            )

        self._tx_counter += 1
        tx = MultisigTransaction(
            tx_id=f"MSTX-{self._tx_counter:06d}",
            safe_address=self._deployed_addresses.get(tier, "0x_NOT_DEPLOYED"),
            safe_tier=tier,
            to_address=to_address,
            value_wei=int(value_eth * 10**18),
            data=data,
            description=description,
            proposed_by=proposer_address,
            proposed_at=datetime.now(timezone.utc).isoformat(),
            usd_value=usd_value,
            threshold_required=config.threshold,
        )

        # Auto-approve if below threshold
        if usd_value < config.auto_approve_below and config.auto_approve_below > 0:
            tx.approvals.append(proposer_address)
            tx.status = "auto_approved" if tx.is_approved else "pending"
            logger.info(f"[{tx.tx_id}] Auto-approved (${usd_value:,.2f} < ${config.auto_approve_below:,.2f})")
        else:
            tx.approvals.append(proposer_address)  # Proposer auto-approves

        self.transactions.append(tx)
        logger.info(f"[{tx.tx_id}] Transaction proposed: {description} "
                    f"({value_eth} ETH / ${usd_value:,.2f}) - {tx.approval_count}/{config.threshold} approvals")

        return tx

    def approve_transaction(self, tx_id: str, approver_address: str) -> MultisigTransaction:
        """Approve a pending transaction."""
        tx = next((t for t in self.transactions if t.tx_id == tx_id), None)
        if not tx:
            raise ValueError(f"Transaction {tx_id} not found")

        config = self.safes[tx.safe_tier]
        approver = next((o for o in config.owners if o.address.lower() == approver_address.lower()), None)

        if not approver:
            raise ValueError(f"Address {approver_address} is not an owner")
        if not approver.can_approve:
            raise ValueError(f"Owner {approver.name} cannot approve transactions")
        if approver_address in tx.approvals:
            raise ValueError(f"Address already approved this transaction")

        tx.approvals.append(approver_address)
        logger.info(f"[{tx.tx_id}] Approved by {approver.name} ({approver.role.value}) "
                    f"- {tx.approval_count}/{tx.threshold_required}")

        if tx.is_approved:
            tx.status = "approved"
            logger.info(f"[{tx.tx_id}] THRESHOLD MET - Ready for execution")

        return tx

    def get_pending_transactions(self, tier: Optional[SafeTier] = None) -> list[dict]:
        """Get all pending transactions, optionally filtered by tier."""
        pending = []
        for tx in self.transactions:
            if tx.status != "pending":
                continue
            if tier and tx.safe_tier != tier:
                continue
            pending.append({
                "tx_id": tx.tx_id,
                "safe_tier": tx.safe_tier.value,
                "description": tx.description,
                "value_eth": tx.value_wei / 10**18,
                "usd_value": tx.usd_value,
                "approvals": f"{tx.approval_count}/{tx.threshold_required}",
                "proposed_by": tx.proposed_by,
                "proposed_at": tx.proposed_at,
            })
        return pending

    def get_setup_summary(self) -> dict:
        """Get summary of all configured safes."""
        summary: dict[str, Any] = {"network": self.network, "safes": {}}

        for tier, config in self.safes.items():
            summary["safes"][tier.value] = {
                "name": config.name,
                "owners": [
                    {"name": o.name, "role": o.role.value, "address": o.address[:10] + "..."}
                    for o in config.owners
                ],
                "threshold": f"{config.threshold}/{config.owner_count}",
                "daily_limit_usd": f"${config.daily_limit_usd:,.2f}",
                "single_tx_limit_usd": f"${config.single_tx_limit_usd:,.2f}",
                "auto_approve_below_usd": f"${config.auto_approve_below:,.2f}",
                "deployed": tier in self._deployed_addresses,
            }

        return summary


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 72)
    print("GNOSIS SAFE MULTI-SIG TREASURY SETUP - Crypto Casino")
    print("=" * 72)

    setup = MultisigSetup(network="polygon")

    # Define team members
    team = {
        "cfo": {"address": "0xCF0111111111111111111111111111111111111111", "role": "cfo", "name": "Sarah (CFO)"},
        "cto": {"address": "0xCT0222222222222222222222222222222222222222", "role": "cto", "name": "Alex (CTO)"},
        "compliance": {"address": "0xCO0333333333333333333333333333333333333333", "role": "compliance", "name": "Maria (Compliance)"},
        "ops": {"address": "0xOP0444444444444444444444444444444444444444", "role": "operations", "name": "James (Ops)"},
        "board1": {"address": "0xBD0555555555555555555555555555555555555555", "role": "board_member", "name": "David (Board)"},
        "board2": {"address": "0xBD0666666666666666666666666666666666666666", "role": "board_member", "name": "Lisa (Board)"},
        "auditor": {"address": "0xAU0777777777777777777777777777777777777777", "role": "auditor", "name": "Robert (Auditor)"},
    }

    # 1. Operations Safe (2/3)
    print("\n[1] Configuring Operations Safe...")
    ops_config = setup.create_safe_config(
        tier=SafeTier.OPERATIONS,
        owners=[team["ops"], team["cto"], team["cfo"]],
    )
    print(f"    Threshold: {ops_config.threshold}/{ops_config.owner_count}")
    print(f"    Daily limit: ${ops_config.daily_limit_usd:,.2f}")

    # 2. Treasury Safe (3/5)
    print("\n[2] Configuring Treasury Safe...")
    treasury_config = setup.create_safe_config(
        tier=SafeTier.TREASURY,
        owners=[team["cfo"], team["cto"], team["compliance"], team["board1"], team["ops"]],
    )
    print(f"    Threshold: {treasury_config.threshold}/{treasury_config.owner_count}")
    print(f"    Daily limit: ${treasury_config.daily_limit_usd:,.2f}")

    # 3. Emergency Safe (4/7)
    print("\n[3] Configuring Emergency Safe...")
    emergency_config = setup.create_safe_config(
        tier=SafeTier.EMERGENCY,
        owners=list(team.values()),
    )
    print(f"    Threshold: {emergency_config.threshold}/{emergency_config.owner_count}")

    # 4. Deploy (dry run)
    print("\n[4] Deploying safes (dry run)...")
    for tier in SafeTier:
        result = setup.deploy_safe(tier, dry_run=True)
        print(f"    {tier.value}: {result.get('safe_name')} - {result.get('threshold')}/{len(result.get('owners', []))}")

    # 5. Simulate transaction workflow
    print("\n[5] Transaction Workflow Simulation...")
    tx = setup.propose_transaction(
        tier=SafeTier.OPERATIONS,
        to_address="0xHOT_WALLET_ADDRESS",
        value_eth=5.0,
        description="Refill hot wallet with 5 ETH for player withdrawals",
        proposer_address=team["ops"]["address"],
    )
    print(f"    Proposed: {tx.tx_id} - {tx.approval_count}/{tx.threshold_required} approvals")

    # CTO approves
    setup.approve_transaction(tx.tx_id, team["cto"]["address"])
    print(f"    After CTO approval: {tx.approval_count}/{tx.threshold_required} - Status: {tx.status}")

    # Summary
    print("\n" + "=" * 72)
    print("TREASURY SETUP SUMMARY")
    print("=" * 72)
    summary = setup.get_setup_summary()
    print(json.dumps(summary, indent=2))
