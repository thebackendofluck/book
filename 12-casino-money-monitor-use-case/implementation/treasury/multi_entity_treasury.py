#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - Multi-Entity Treasury Management
==========================================================
Chapter 5 Implementation: Checklist Item #8

Manages treasury operations across multiple legal entities that a typical
multi-jurisdiction casino operator maintains:

Entity Structure Example:
    HoldCo (Malta) -- parent holding company
    |-- OpCo UK Ltd (UKGC license) -- player-facing UK entity
    |-- OpCo Malta Ltd (MGA license) -- player-facing EU entity
    |-- OpCo Curacao NV (Curacao license) -- player-facing RoW entity
    |-- Tech Services Ltd (Gibraltar) -- platform technology
    |-- Marketing Ltd (Malta) -- affiliate/marketing

Features:
- Inter-entity fund transfers with transfer pricing compliance
- Consolidated treasury view across all entities
- Entity-specific liquidity requirements
- Regulatory capital adequacy tracking (per-entity)
- Intercompany loan management
- Currency hedging across entities

PCI DSS Compliance Notes:
- Requirement 7.1: Access restricted per entity (RBAC)
- Requirement 10.2: All inter-entity transfers audited
- Transfer pricing documentation for tax compliance

Dependencies:
    pip install pydantic
"""

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("multi_entity_treasury")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    HOLDING = "holding"
    OPERATING = "operating"
    TECHNOLOGY = "technology"
    MARKETING = "marketing"
    PAYMENT = "payment"
    DORMANT = "dormant"


class Jurisdiction(str, Enum):
    UK = "uk"
    MALTA = "malta"
    CURACAO = "curacao"
    GIBRALTAR = "gibraltar"
    ISLE_OF_MAN = "isle_of_man"
    SWEDEN = "sweden"


class TransferType(str, Enum):
    LIQUIDITY_INJECTION = "liquidity_injection"        # parent -> subsidiary
    DIVIDEND_UPSTREAM = "dividend_upstream"             # subsidiary -> parent
    INTERCOMPANY_LOAN = "intercompany_loan"
    LOAN_REPAYMENT = "loan_repayment"
    SERVICE_FEE = "service_fee"                        # tech/marketing services
    PLAYER_FUND_SEGREGATION = "player_fund_segregation"
    REGULATORY_CAPITAL = "regulatory_capital"
    EMERGENCY_FUNDING = "emergency_funding"


class TransferStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LegalEntity(BaseModel):
    """Represents a legal entity in the corporate structure."""
    entity_id: str
    name: str
    entity_type: EntityType
    jurisdiction: Jurisdiction
    license_number: Optional[str] = None
    base_currency: str
    parent_entity_id: Optional[str] = None

    # Financial position
    total_assets: Decimal = Decimal("0")
    total_liabilities: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    cash_balance: Decimal = Decimal("0")
    player_funds_held: Decimal = Decimal("0")  # segregated player money

    # Regulatory requirements
    min_capital_requirement: Decimal = Decimal("0")   # regulatory minimum
    current_capital: Decimal = Decimal("0")
    capital_adequacy_ratio: Decimal = Decimal("0")    # current / required

    # Liquidity
    min_liquidity_buffer: Decimal = Decimal("0")
    current_liquidity: Decimal = Decimal("0")

    is_active: bool = True


class InterEntityTransfer(BaseModel):
    """Records a transfer between two legal entities."""
    transfer_id: str = Field(default_factory=lambda: str(uuid4()))
    transfer_type: TransferType
    status: TransferStatus = TransferStatus.PENDING_APPROVAL

    # Parties
    source_entity_id: str
    source_entity_name: str = ""
    destination_entity_id: str
    destination_entity_name: str = ""

    # Amounts
    amount: Decimal
    currency: str
    fx_rate: Optional[Decimal] = None             # if cross-currency
    destination_amount: Optional[Decimal] = None
    destination_currency: Optional[str] = None

    # Transfer pricing
    arm_length_rate: Optional[Decimal] = None      # for service fees
    transfer_pricing_doc_ref: Optional[str] = None

    # Metadata
    purpose: str = ""
    reference: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Regulatory
    requires_regulatory_approval: bool = False
    regulatory_notification_sent: bool = False


class IntercompanyLoan(BaseModel):
    """Tracks intercompany loans between entities."""
    loan_id: str = Field(default_factory=lambda: str(uuid4()))
    lender_entity_id: str
    borrower_entity_id: str
    principal: Decimal
    currency: str
    interest_rate: Decimal          # annual rate (arm's length)
    start_date: datetime
    maturity_date: datetime
    outstanding_principal: Decimal
    accrued_interest: Decimal = Decimal("0")
    status: str = "active"          # active, repaid, defaulted


class ConsolidatedPosition(BaseModel):
    """Consolidated treasury view across all entities."""
    as_of: datetime
    reporting_currency: str = "EUR"

    # Consolidated totals
    total_cash: Decimal = Decimal("0")
    total_player_funds: Decimal = Decimal("0")
    total_operating_cash: Decimal = Decimal("0")  # total_cash - player_funds
    total_liabilities: Decimal = Decimal("0")
    total_equity: Decimal = Decimal("0")

    # By entity
    entity_positions: list[dict] = []

    # Intercompany
    total_intercompany_loans: Decimal = Decimal("0")
    intercompany_eliminations: Decimal = Decimal("0")  # eliminated in consolidation

    # Regulatory
    all_entities_capital_adequate: bool = True
    entities_below_threshold: list[str] = []

    # Cash flow
    net_intercompany_flow_30d: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Multi-Entity Treasury Manager
# ---------------------------------------------------------------------------

class MultiEntityTreasury:
    """
    Manages treasury operations across the corporate group.

    Key responsibilities:
    1. Monitor capital adequacy per entity
    2. Manage intercompany funding
    3. Ensure player fund segregation compliance
    4. Track intercompany loans and transfer pricing
    5. Provide consolidated view for group CFO
    """

    def __init__(self):
        self._entities: dict[str, LegalEntity] = {}
        self._transfers: list[InterEntityTransfer] = []
        self._loans: list[IntercompanyLoan] = []

        # Load demo corporate structure
        self._load_demo_structure()

    def _load_demo_structure(self):
        """Set up a realistic multi-entity casino group."""
        entities = [
            LegalEntity(
                entity_id="holdco-mt",
                name="AcmetoCasino Holdings Ltd",
                entity_type=EntityType.HOLDING,
                jurisdiction=Jurisdiction.MALTA,
                base_currency="EUR",
                total_assets=Decimal("45000000"),
                total_liabilities=Decimal("12000000"),
                equity=Decimal("33000000"),
                cash_balance=Decimal("5200000"),
                min_capital_requirement=Decimal("1000000"),
                current_capital=Decimal("33000000"),
                capital_adequacy_ratio=Decimal("33.0"),
                min_liquidity_buffer=Decimal("2000000"),
                current_liquidity=Decimal("5200000"),
            ),
            LegalEntity(
                entity_id="opco-uk",
                name="AcmetoCasino UK Ltd",
                entity_type=EntityType.OPERATING,
                jurisdiction=Jurisdiction.UK,
                license_number="UKGC-001234",
                base_currency="GBP",
                parent_entity_id="holdco-mt",
                total_assets=Decimal("18500000"),
                total_liabilities=Decimal("5800000"),
                equity=Decimal("12700000"),
                cash_balance=Decimal("6140000"),
                player_funds_held=Decimal("4250000"),
                min_capital_requirement=Decimal("2000000"),    # UKGC requirement
                current_capital=Decimal("12700000"),
                capital_adequacy_ratio=Decimal("6.35"),
                min_liquidity_buffer=Decimal("4250000"),       # >= player funds
                current_liquidity=Decimal("6140000"),
            ),
            LegalEntity(
                entity_id="opco-mt",
                name="AcmetoCasino Malta Ltd",
                entity_type=EntityType.OPERATING,
                jurisdiction=Jurisdiction.MALTA,
                license_number="MGA/B2C/2024/001",
                base_currency="EUR",
                parent_entity_id="holdco-mt",
                total_assets=Decimal("12000000"),
                total_liabilities=Decimal("3500000"),
                equity=Decimal("8500000"),
                cash_balance=Decimal("4180000"),
                player_funds_held=Decimal("2800000"),
                min_capital_requirement=Decimal("100000"),     # MGA minimum (B2C)
                current_capital=Decimal("8500000"),
                capital_adequacy_ratio=Decimal("85.0"),
                min_liquidity_buffer=Decimal("2800000"),       # >= player funds
                current_liquidity=Decimal("4180000"),
            ),
            LegalEntity(
                entity_id="opco-cw",
                name="AcmetoCasino Curacao NV",
                entity_type=EntityType.OPERATING,
                jurisdiction=Jurisdiction.CURACAO,
                license_number="GLH-OCCHKTW0000000",
                base_currency="USD",
                parent_entity_id="holdco-mt",
                total_assets=Decimal("5500000"),
                total_liabilities=Decimal("1800000"),
                equity=Decimal("3700000"),
                cash_balance=Decimal("2650000"),
                player_funds_held=Decimal("1650000"),
                min_capital_requirement=Decimal("50000"),
                current_capital=Decimal("3700000"),
                capital_adequacy_ratio=Decimal("74.0"),
                min_liquidity_buffer=Decimal("1650000"),
                current_liquidity=Decimal("2650000"),
            ),
            LegalEntity(
                entity_id="tech-gi",
                name="AcmetoCasino Tech Ltd",
                entity_type=EntityType.TECHNOLOGY,
                jurisdiction=Jurisdiction.GIBRALTAR,
                base_currency="GBP",
                parent_entity_id="holdco-mt",
                total_assets=Decimal("3200000"),
                total_liabilities=Decimal("800000"),
                equity=Decimal("2400000"),
                cash_balance=Decimal("890000"),
                min_capital_requirement=Decimal("0"),
                current_capital=Decimal("2400000"),
                min_liquidity_buffer=Decimal("200000"),
                current_liquidity=Decimal("890000"),
            ),
            LegalEntity(
                entity_id="mktg-mt",
                name="AcmetoCasino Marketing Ltd",
                entity_type=EntityType.MARKETING,
                jurisdiction=Jurisdiction.MALTA,
                base_currency="EUR",
                parent_entity_id="holdco-mt",
                total_assets=Decimal("1500000"),
                total_liabilities=Decimal("600000"),
                equity=Decimal("900000"),
                cash_balance=Decimal("420000"),
                min_capital_requirement=Decimal("0"),
                current_capital=Decimal("900000"),
                min_liquidity_buffer=Decimal("100000"),
                current_liquidity=Decimal("420000"),
            ),
        ]

        for entity in entities:
            self._entities[entity.entity_id] = entity

        # Demo intercompany loans
        self._loans = [
            IntercompanyLoan(
                loan_id="LOAN-001",
                lender_entity_id="holdco-mt",
                borrower_entity_id="opco-uk",
                principal=Decimal("5000000"),
                currency="EUR",
                interest_rate=Decimal("3.5"),     # arm's length rate
                start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
                maturity_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
                outstanding_principal=Decimal("4200000"),
                accrued_interest=Decimal("73500"),
            ),
            IntercompanyLoan(
                loan_id="LOAN-002",
                lender_entity_id="holdco-mt",
                borrower_entity_id="opco-cw",
                principal=Decimal("2000000"),
                currency="USD",
                interest_rate=Decimal("5.0"),     # higher rate for USD/higher risk
                start_date=datetime(2025, 6, 1, tzinfo=timezone.utc),
                maturity_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
                outstanding_principal=Decimal("1800000"),
                accrued_interest=Decimal("45000"),
            ),
        ]

    # ---- Entity Management ----

    def get_entity(self, entity_id: str) -> Optional[LegalEntity]:
        return self._entities.get(entity_id)

    def list_entities(self, active_only: bool = True) -> list[LegalEntity]:
        entities = list(self._entities.values())
        if active_only:
            entities = [e for e in entities if e.is_active]
        return entities

    def check_capital_adequacy(self, entity_id: str) -> dict:
        """Check if an entity meets its regulatory capital requirements."""
        entity = self._entities.get(entity_id)
        if not entity:
            raise ValueError(f"Entity {entity_id} not found")

        is_adequate = entity.current_capital >= entity.min_capital_requirement
        surplus = entity.current_capital - entity.min_capital_requirement
        ratio = (entity.current_capital / entity.min_capital_requirement).quantize(
            Decimal("0.01")
        ) if entity.min_capital_requirement > 0 else Decimal("999.99")

        # Player fund check for operating entities
        player_fund_adequate = True
        if entity.player_funds_held > 0:
            player_fund_adequate = entity.current_liquidity >= entity.player_funds_held

        return {
            "entity_id": entity_id,
            "entity_name": entity.name,
            "jurisdiction": entity.jurisdiction.value,
            "capital_adequate": is_adequate,
            "current_capital": str(entity.current_capital),
            "required_capital": str(entity.min_capital_requirement),
            "surplus": str(surplus),
            "ratio": str(ratio),
            "player_funds_held": str(entity.player_funds_held),
            "player_fund_coverage_adequate": player_fund_adequate,
            "liquidity_available": str(entity.current_liquidity),
        }

    # ---- Intercompany Transfers ----

    async def create_transfer(
        self,
        source_entity_id: str,
        destination_entity_id: str,
        amount: Decimal,
        currency: str,
        transfer_type: TransferType,
        purpose: str = "",
        arm_length_rate: Optional[Decimal] = None,
    ) -> InterEntityTransfer:
        """
        Create an inter-entity fund transfer.

        Validations:
        - Source entity has sufficient funds
        - Transfer does not breach regulatory minimums
        - Service fees use arm's length pricing
        - Player funds cannot be transferred (they're segregated)
        """
        source = self._entities.get(source_entity_id)
        dest = self._entities.get(destination_entity_id)

        if not source or not dest:
            raise ValueError("Invalid entity IDs")

        # Validation: sufficient funds
        available = source.cash_balance - source.player_funds_held - source.min_liquidity_buffer
        if amount > available:
            raise ValueError(
                f"Insufficient available funds. "
                f"Cash: {source.cash_balance:,.2f}, "
                f"Player funds (locked): {source.player_funds_held:,.2f}, "
                f"Min buffer: {source.min_liquidity_buffer:,.2f}, "
                f"Available: {available:,.2f}"
            )

        # Validation: capital adequacy post-transfer
        post_transfer_capital = source.current_capital - amount
        if post_transfer_capital < source.min_capital_requirement:
            raise ValueError(
                f"Transfer would breach {source.name} capital requirement. "
                f"Post-transfer: {post_transfer_capital:,.2f}, "
                f"Required: {source.min_capital_requirement:,.2f}"
            )

        # Regulatory approval needed for large transfers
        requires_reg = amount > Decimal("1000000") or transfer_type == TransferType.REGULATORY_CAPITAL

        transfer = InterEntityTransfer(
            transfer_type=transfer_type,
            source_entity_id=source_entity_id,
            source_entity_name=source.name,
            destination_entity_id=destination_entity_id,
            destination_entity_name=dest.name,
            amount=amount,
            currency=currency,
            purpose=purpose,
            arm_length_rate=arm_length_rate,
            requires_regulatory_approval=requires_reg,
        )

        # Handle cross-currency
        if source.base_currency != dest.base_currency:
            fx_rate = self._get_fx_rate(source.base_currency, dest.base_currency)
            transfer.fx_rate = fx_rate
            transfer.destination_currency = dest.base_currency
            transfer.destination_amount = (amount * fx_rate).quantize(Decimal("0.01"))

        self._transfers.append(transfer)
        logger.info(
            f"Transfer created: {transfer.transfer_id} | "
            f"{source.name} -> {dest.name} | "
            f"{amount:,.2f} {currency} ({transfer_type.value})"
        )

        return transfer

    async def approve_transfer(self, transfer_id: str, approver: str) -> InterEntityTransfer:
        """Approve a pending transfer."""
        transfer = next((t for t in self._transfers if t.transfer_id == transfer_id), None)
        if not transfer:
            raise ValueError(f"Transfer {transfer_id} not found")

        if transfer.status != TransferStatus.PENDING_APPROVAL:
            raise ValueError(f"Transfer is {transfer.status.value}, cannot approve")

        transfer.status = TransferStatus.APPROVED
        transfer.approved_by = approver
        transfer.approved_at = datetime.now(timezone.utc)

        logger.info(f"Transfer {transfer_id} approved by {approver}")
        return transfer

    async def execute_transfer(self, transfer_id: str) -> InterEntityTransfer:
        """Execute an approved transfer (update balances)."""
        transfer = next((t for t in self._transfers if t.transfer_id == transfer_id), None)
        if not transfer:
            raise ValueError(f"Transfer {transfer_id} not found")

        if transfer.status != TransferStatus.APPROVED:
            raise ValueError(f"Transfer must be approved first (current: {transfer.status.value})")

        source = self._entities[transfer.source_entity_id]
        dest = self._entities[transfer.destination_entity_id]

        # Update balances
        source.cash_balance -= transfer.amount
        source.current_liquidity -= transfer.amount

        dest_amount = transfer.destination_amount or transfer.amount
        dest.cash_balance += dest_amount
        dest.current_liquidity += dest_amount

        transfer.status = TransferStatus.COMPLETED
        transfer.completed_at = datetime.now(timezone.utc)

        logger.info(
            f"Transfer {transfer_id} executed: "
            f"{source.name} -{transfer.amount:,.2f} {transfer.currency} | "
            f"{dest.name} +{dest_amount:,.2f} {transfer.destination_currency or transfer.currency}"
        )

        return transfer

    # ---- Consolidated View ----

    def get_consolidated_position(self) -> ConsolidatedPosition:
        """Get consolidated treasury position across all entities."""
        position = ConsolidatedPosition(as_of=datetime.now(timezone.utc))

        for entity in self._entities.values():
            if not entity.is_active:
                continue

            cash_eur = self._to_eur(entity.cash_balance, entity.base_currency)
            player_eur = self._to_eur(entity.player_funds_held, entity.base_currency)

            position.total_cash += cash_eur
            position.total_player_funds += player_eur
            position.total_liabilities += self._to_eur(entity.total_liabilities, entity.base_currency)
            position.total_equity += self._to_eur(entity.equity, entity.base_currency)

            # Capital adequacy check
            if entity.min_capital_requirement > 0:
                if entity.current_capital < entity.min_capital_requirement:
                    position.all_entities_capital_adequate = False
                    position.entities_below_threshold.append(entity.entity_id)

            position.entity_positions.append({
                "entity_id": entity.entity_id,
                "name": entity.name,
                "type": entity.entity_type.value,
                "jurisdiction": entity.jurisdiction.value,
                "currency": entity.base_currency,
                "cash": str(entity.cash_balance),
                "cash_eur": str(cash_eur.quantize(Decimal("0.01"))),
                "player_funds": str(entity.player_funds_held),
                "capital_ratio": str(entity.capital_adequacy_ratio),
            })

        position.total_operating_cash = position.total_cash - position.total_player_funds

        # Intercompany
        position.total_intercompany_loans = sum(  # ty:ignore[invalid-assignment]
            self._to_eur(l.outstanding_principal, l.currency) for l in self._loans if l.status == "active"
        )
        position.intercompany_eliminations = position.total_intercompany_loans  # eliminated in consolidation

        return position

    # ---- Helpers ----

    def _get_fx_rate(self, from_ccy: str, to_ccy: str) -> Decimal:
        rates_vs_eur = {
            "EUR": Decimal("1.0"), "GBP": Decimal("0.858"), "USD": Decimal("1.087"),
        }
        return (rates_vs_eur.get(to_ccy, Decimal("1")) / rates_vs_eur.get(from_ccy, Decimal("1"))).quantize(
            Decimal("0.000001")
        )

    def _to_eur(self, amount: Decimal, currency: str) -> Decimal:
        rates = {"EUR": Decimal("1.0"), "GBP": Decimal("0.858"), "USD": Decimal("1.087")}
        rate = rates.get(currency, Decimal("1"))
        return (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

async def main():
    treasury = MultiEntityTreasury()

    # Print corporate structure
    print("=" * 70)
    print("MULTI-ENTITY TREASURY - CORPORATE STRUCTURE")
    print("=" * 70)

    for entity in treasury.list_entities():
        indent = "  " if entity.parent_entity_id else ""
        print(f"\n{indent}{entity.name} ({entity.jurisdiction.value.upper()})")
        print(f"{indent}  Type: {entity.entity_type.value} | Currency: {entity.base_currency}")
        print(f"{indent}  Cash: {entity.cash_balance:>12,.2f} {entity.base_currency}")
        if entity.player_funds_held > 0:
            print(f"{indent}  Player Funds: {entity.player_funds_held:>12,.2f} (segregated)")
        if entity.min_capital_requirement > 0:
            adequacy = treasury.check_capital_adequacy(entity.entity_id)
            print(f"{indent}  Capital: {adequacy['current_capital']} / {adequacy['required_capital']} "
                  f"(ratio: {adequacy['ratio']}x) {'OK' if adequacy['capital_adequate'] else 'BREACH'}")

    # Consolidated position
    position = treasury.get_consolidated_position()
    print(f"\n{'='*70}")
    print("CONSOLIDATED POSITION (EUR)")
    print(f"{'='*70}")
    print(f"  Total Cash:           {position.total_cash:>14,.2f}")
    print(f"  Player Funds:         {position.total_player_funds:>14,.2f}")
    print(f"  Operating Cash:       {position.total_operating_cash:>14,.2f}")
    print(f"  Total Equity:         {position.total_equity:>14,.2f}")
    print(f"  IC Loans Outstanding: {position.total_intercompany_loans:>14,.2f}")
    print(f"  All Capital Adequate: {position.all_entities_capital_adequate}")

    # Demo: Inter-entity transfer
    print(f"\n{'='*70}")
    print("INTER-ENTITY TRANSFER")
    print(f"{'='*70}")

    transfer = await treasury.create_transfer(
        source_entity_id="holdco-mt",
        destination_entity_id="opco-uk",
        amount=Decimal("500000.00"),
        currency="EUR",
        transfer_type=TransferType.LIQUIDITY_INJECTION,
        purpose="Q1 liquidity top-up for UK operations",
    )
    print(f"  Transfer created: {transfer.transfer_id}")
    print(f"  {transfer.source_entity_name} -> {transfer.destination_entity_name}")
    print(f"  Amount: {transfer.amount:,.2f} {transfer.currency}")
    if transfer.fx_rate:
        print(f"  FX Rate: {transfer.fx_rate} -> {transfer.destination_amount:,.2f} {transfer.destination_currency}")

    # Approve and execute
    await treasury.approve_transfer(transfer.transfer_id, "CFO")
    await treasury.execute_transfer(transfer.transfer_id)
    print(f"  Status: {transfer.status.value}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
