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
Casino Money Monitor - Real-Time Cash Position Dashboard
=========================================================
Chapter 5 Implementation: Checklist Item #1

Multi-currency real-time cash position dashboard using FastAPI + WebSocket.
Aggregates balances across bank accounts, payment providers, and crypto wallets.

PCI DSS Compliance Notes:
- Requirement 3.4: Render PAN unreadable (no card data stored here)
- Requirement 10.2: Audit trail for all balance queries
- Requirement 7.1: Role-based access to financial dashboards
- All amounts transmitted via TLS 1.3 (Requirement 4.1)

Dependencies:
    pip install fastapi uvicorn websockets sqlalchemy asyncpg redis pydantic
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Numeric, DateTime, Integer, create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = "postgresql+asyncpg://casino_treasury:${DB_PASSWORD}@postgres:5432/casino_money_monitor"
REDIS_URL = "redis://redis:6379/0"

# Exchange rate source refresh interval (seconds)
FX_REFRESH_INTERVAL = 30

# Dashboard push interval (seconds)
DASHBOARD_PUSH_INTERVAL = 2

# Supported currencies for a typical multi-jurisdiction operator
SUPPORTED_CURRENCIES = [
    "EUR", "GBP", "USD", "SEK", "NOK", "DKK", "CAD", "AUD",
    "BRL", "MXN", "JPY", "CHF", "BTC", "ETH", "USDT",
]

REPORTING_CURRENCY = "EUR"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cash_dashboard")

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

Base = declarative_base()


class AccountType(str, Enum):
    BANK = "bank"
    PAYMENT_PROVIDER = "payment_provider"
    CRYPTO_WALLET = "crypto_wallet"
    ESCROW = "escrow"
    PLAYER_FUNDS = "player_funds"
    OPERATING = "operating"
    RESERVE = "reserve"


class CashAccount(Base):
    """Tracks every financial account the casino operator holds."""
    __tablename__ = "cash_accounts"

    id = Column(String(36), primary_key=True)
    entity_id = Column(String(36), nullable=False, index=True)       # legal entity
    account_name = Column(String(200), nullable=False)
    account_type = Column(String(30), nullable=False)
    currency = Column(String(5), nullable=False)
    provider = Column(String(100), nullable=False)                    # bank or PSP name
    balance = Column(Numeric(18, 4), nullable=False, default=0)
    available_balance = Column(Numeric(18, 4), nullable=False, default=0)
    pending_in = Column(Numeric(18, 4), nullable=False, default=0)
    pending_out = Column(Numeric(18, 4), nullable=False, default=0)
    last_synced = Column(DateTime(timezone=True))
    is_active = Column(Integer, nullable=False, default=1)


class BalanceSnapshot(Base):
    """Point-in-time snapshot for audit and reconciliation."""
    __tablename__ = "balance_snapshots"

    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), nullable=False, index=True)
    balance = Column(Numeric(18, 4), nullable=False)
    available_balance = Column(Numeric(18, 4), nullable=False)
    snapshot_time = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(50), nullable=False)  # api_sync, manual, reconciliation


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class AccountBalance(BaseModel):
    account_id: str
    entity_id: str
    account_name: str
    account_type: AccountType
    currency: str
    provider: str
    balance: Decimal
    available_balance: Decimal
    pending_in: Decimal
    pending_out: Decimal
    balance_reporting_ccy: Decimal = Decimal("0")
    last_synced: Optional[datetime] = None


class CurrencyPosition(BaseModel):
    currency: str
    total_balance: Decimal = Decimal("0")
    total_available: Decimal = Decimal("0")
    total_pending_in: Decimal = Decimal("0")
    total_pending_out: Decimal = Decimal("0")
    net_position: Decimal = Decimal("0")
    reporting_ccy_equivalent: Decimal = Decimal("0")
    account_count: int = 0


class DashboardState(BaseModel):
    """Full dashboard payload pushed to connected WebSocket clients."""
    timestamp: datetime
    reporting_currency: str = REPORTING_CURRENCY
    total_balance_reporting_ccy: Decimal = Decimal("0")
    total_available_reporting_ccy: Decimal = Decimal("0")
    total_exposure: Decimal = Decimal("0")
    net_liquidity: Decimal = Decimal("0")
    currency_positions: list[CurrencyPosition] = []
    account_balances: list[AccountBalance] = []
    alerts: list[dict] = []
    fx_rates: dict[str, Decimal] = {}


# ---------------------------------------------------------------------------
# Exchange Rate Service
# ---------------------------------------------------------------------------

class ExchangeRateService:
    """
    Manages FX rates for multi-currency consolidation.
    In production, connect to ECB, XE, or Bloomberg FX feed.
    """

    def __init__(self):
        # Rates vs EUR (static fallback; real impl fetches live rates)
        self._rates: dict[str, Decimal] = {
            "EUR": Decimal("1.0000"),
            "GBP": Decimal("0.8580"),
            "USD": Decimal("1.0870"),
            "SEK": Decimal("11.2500"),
            "NOK": Decimal("11.5800"),
            "DKK": Decimal("7.4600"),
            "CAD": Decimal("1.4750"),
            "AUD": Decimal("1.6520"),
            "BRL": Decimal("5.3200"),
            "MXN": Decimal("18.6500"),
            "JPY": Decimal("163.5000"),
            "CHF": Decimal("0.9420"),
            "BTC": Decimal("0.0000155"),   # ~1 BTC = 64,500 EUR
            "ETH": Decimal("0.000285"),    # ~1 ETH = 3,500 EUR
            "USDT": Decimal("1.0870"),     # pegged to USD
        }
        self._last_update = datetime.now(timezone.utc)

    def convert(self, amount: Decimal, from_ccy: str, to_ccy: str = REPORTING_CURRENCY) -> Decimal:
        """Convert amount between currencies using mid-market rates."""
        if from_ccy == to_ccy:
            return amount

        # Convert to EUR first, then to target
        rate_from = self._rates.get(from_ccy, Decimal("1"))
        rate_to = self._rates.get(to_ccy, Decimal("1"))

        eur_amount = amount / rate_from
        result = eur_amount * rate_to
        return result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def get_rate(self, from_ccy: str, to_ccy: str = REPORTING_CURRENCY) -> Decimal:
        """Get exchange rate between two currencies."""
        return self.convert(Decimal("1"), from_ccy, to_ccy)

    async def refresh_rates(self):
        """
        Refresh FX rates from external provider.
        Production: call ECB XML feed or Bloomberg B-PIPE.
        """
        logger.info("Refreshing FX rates from external provider")
        # In production:
        # async with httpx.AsyncClient() as client:
        #     resp = await client.get("https://api.ecb.europa.eu/...")
        #     self._rates = parse_ecb_response(resp)
        self._last_update = datetime.now(timezone.utc)

    @property
    def rates(self) -> dict[str, Decimal]:
        return dict(self._rates)


# ---------------------------------------------------------------------------
# Dashboard Aggregation Engine
# ---------------------------------------------------------------------------

class CashPositionAggregator:
    """
    Core engine that aggregates balances across all accounts, converts
    to reporting currency, and computes net liquidity positions.
    """

    def __init__(self, fx_service: ExchangeRateService):
        self.fx = fx_service

    async def build_dashboard(self, accounts: list[CashAccount], exposure: Decimal = Decimal("0")) -> DashboardState:
        """Build a complete dashboard state from current account data."""

        account_balances: list[AccountBalance] = []
        currency_map: dict[str, CurrencyPosition] = {}
        total_balance_rc = Decimal("0")
        total_available_rc = Decimal("0")

        for acct in accounts:
            if not acct.is_active:
                continue

            balance_rc = self.fx.convert(acct.balance, acct.currency)  # ty:ignore[invalid-argument-type]
            avail_rc = self.fx.convert(acct.available_balance, acct.currency)  # ty:ignore[invalid-argument-type]

            ab = AccountBalance(
                account_id=acct.id,  # ty:ignore[invalid-argument-type]
                entity_id=acct.entity_id,  # ty:ignore[invalid-argument-type]
                account_name=acct.account_name,  # ty:ignore[invalid-argument-type]
                account_type=AccountType(acct.account_type),
                currency=acct.currency,  # ty:ignore[invalid-argument-type]
                provider=acct.provider,  # ty:ignore[invalid-argument-type]
                balance=acct.balance,  # ty:ignore[invalid-argument-type]
                available_balance=acct.available_balance,  # ty:ignore[invalid-argument-type]
                pending_in=acct.pending_in,  # ty:ignore[invalid-argument-type]
                pending_out=acct.pending_out,  # ty:ignore[invalid-argument-type]
                balance_reporting_ccy=balance_rc,
                last_synced=acct.last_synced,  # ty:ignore[invalid-argument-type]
            )
            account_balances.append(ab)

            total_balance_rc += balance_rc
            total_available_rc += avail_rc

            # Aggregate by currency
            if acct.currency not in currency_map:
                currency_map[acct.currency] = CurrencyPosition(currency=acct.currency)  # ty:ignore[invalid-argument-type, invalid-assignment]
            cp = currency_map[acct.currency]  # ty:ignore[invalid-argument-type]
            cp.total_balance += acct.balance
            cp.total_available += acct.available_balance
            cp.total_pending_in += acct.pending_in
            cp.total_pending_out += acct.pending_out
            cp.net_position = cp.total_balance - cp.total_pending_out + cp.total_pending_in  # ty:ignore[invalid-assignment]
            cp.reporting_ccy_equivalent = self.fx.convert(cp.total_balance, acct.currency)  # ty:ignore[invalid-argument-type]
            cp.account_count += 1

        net_liquidity = total_available_rc - exposure

        # Generate alerts based on positions
        alerts = self._evaluate_alerts(total_available_rc, exposure, net_liquidity)

        return DashboardState(
            timestamp=datetime.now(timezone.utc),
            total_balance_reporting_ccy=total_balance_rc.quantize(Decimal("0.01")),
            total_available_reporting_ccy=total_available_rc.quantize(Decimal("0.01")),
            total_exposure=exposure.quantize(Decimal("0.01")),
            net_liquidity=net_liquidity.quantize(Decimal("0.01")),
            currency_positions=sorted(currency_map.values(), key=lambda c: c.reporting_ccy_equivalent, reverse=True),
            account_balances=account_balances,
            alerts=alerts,
            fx_rates={k: v for k, v in self.fx.rates.items()},
        )

    def _evaluate_alerts(
        self,
        total_available: Decimal,
        exposure: Decimal,
        net_liquidity: Decimal,
    ) -> list[dict]:
        """
        Evaluate financial position against thresholds.
        Thresholds are based on typical operator requirements:
        - Liquidity coverage ratio (LCR) > 1.5x for regulated markets
        - Minimum reserve: player liability + 20% buffer
        """
        alerts = []

        if exposure > Decimal("0"):
            lcr = total_available / exposure if exposure else Decimal("999")

            if lcr < Decimal("1.0"):
                alerts.append({
                    "level": "emergency",
                    "code": "LCR_CRITICAL",
                    "message": f"Liquidity coverage ratio {lcr:.2f}x below 1.0x - IMMEDIATE ACTION REQUIRED",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            elif lcr < Decimal("1.5"):
                alerts.append({
                    "level": "critical",
                    "code": "LCR_LOW",
                    "message": f"Liquidity coverage ratio {lcr:.2f}x below regulatory minimum 1.5x",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            elif lcr < Decimal("2.0"):
                alerts.append({
                    "level": "warning",
                    "code": "LCR_WATCH",
                    "message": f"Liquidity coverage ratio {lcr:.2f}x approaching warning threshold",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        if net_liquidity < Decimal("0"):
            alerts.append({
                "level": "emergency",
                "code": "NEGATIVE_LIQUIDITY",
                "message": f"Net liquidity is negative: {net_liquidity:,.2f} {REPORTING_CURRENCY}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        return alerts


# ---------------------------------------------------------------------------
# WebSocket Connection Manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages WebSocket connections for real-time dashboard updates."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client {client_id} connected. Total: {len(self.active_connections)}")

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
        logger.info(f"Client {client_id} disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, data: dict):
        """Push dashboard state to all connected clients."""
        disconnected = []
        message = json.dumps(data, default=str)
        for client_id, ws in self.active_connections.items():
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(client_id)

        for cid in disconnected:
            self.disconnect(cid)


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start background FX rate refresh loop on startup."""
    task = asyncio.create_task(_fx_refresh_loop())
    logger.info("Cash Position Dashboard started")
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Casino Money Monitor - Cash Position Dashboard",
    version="1.0.0",
    description="Real-time multi-currency cash position monitoring for casino operators",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["https://backoffice.casino.internal"],  # restrict in production
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)

manager = ConnectionManager()
fx_service = ExchangeRateService()
aggregator = CashPositionAggregator(fx_service)


def _demo_accounts() -> list[CashAccount]:
    """
    Generate realistic demo account data for a multi-jurisdiction operator.
    In production, these come from the database synced via bank APIs.
    """
    now = datetime.now(timezone.utc)
    accounts_data = [
        # --- Player Funds (segregated per UKGC/MGA requirements) ---
        ("entity-uk", "UK Player Funds - Barclays", "player_funds", "GBP",
         "Barclays", "4250000.00", "4250000.00", "185000.00", "92000.00"),
        ("entity-malta", "Malta Player Funds - BOV", "player_funds", "EUR",
         "Bank of Valletta", "2800000.00", "2800000.00", "95000.00", "67000.00"),
        ("entity-curacao", "Curacao Player Funds - MCB", "player_funds", "USD",
         "MCB Curacao", "1650000.00", "1650000.00", "42000.00", "31000.00"),

        # --- Operating Accounts ---
        ("entity-uk", "UK Operating - HSBC", "operating", "GBP",
         "HSBC", "890000.00", "750000.00", "0.00", "140000.00"),
        ("entity-malta", "Malta Operating - BOV", "operating", "EUR",
         "Bank of Valletta", "1200000.00", "1100000.00", "0.00", "100000.00"),

        # --- Payment Providers ---
        ("entity-uk", "Stripe UK Settlement", "payment_provider", "GBP",
         "Stripe", "320000.00", "0.00", "320000.00", "0.00"),
        ("entity-malta", "Trustly EU Settlement", "payment_provider", "EUR",
         "Trustly", "180000.00", "0.00", "180000.00", "0.00"),
        ("entity-uk", "PayPal UK Float", "payment_provider", "GBP",
         "PayPal", "75000.00", "75000.00", "12000.00", "8000.00"),

        # --- Crypto Wallets ---
        ("entity-curacao", "BTC Hot Wallet", "crypto_wallet", "BTC",
         "BitGo", "3.5000", "3.5000", "0.1200", "0.0800"),
        ("entity-curacao", "ETH Hot Wallet", "crypto_wallet", "ETH",
         "BitGo", "45.0000", "45.0000", "2.5000", "1.2000"),
        ("entity-curacao", "USDT Reserve", "crypto_wallet", "USDT",
         "Fireblocks", "500000.00", "500000.00", "0.00", "0.00"),

        # --- Reserve / Escrow ---
        ("entity-uk", "UK Regulatory Reserve", "reserve", "GBP",
         "Barclays", "1000000.00", "0.00", "0.00", "0.00"),
        ("entity-malta", "MGA Player Protection Fund", "escrow", "EUR",
         "Central Bank of Malta", "750000.00", "0.00", "0.00", "0.00"),
    ]

    accounts = []
    for i, (eid, name, atype, ccy, provider, bal, avail, pin, pout) in enumerate(accounts_data):
        acct = CashAccount()
        acct.id = f"acct-{i+1:03d}"
        acct.entity_id = eid
        acct.account_name = name
        acct.account_type = atype
        acct.currency = ccy
        acct.provider = provider
        acct.balance = Decimal(bal)
        acct.available_balance = Decimal(avail)
        acct.pending_in = Decimal(pin)
        acct.pending_out = Decimal(pout)
        acct.last_synced = now
        acct.is_active = 1
        accounts.append(acct)

    return accounts


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/dashboard", response_model=DashboardState)
async def get_dashboard(
    entity_id: Optional[str] = Query(None, description="Filter by legal entity"),
    currency: Optional[str] = Query(None, description="Filter by currency"),
):
    """
    Get current cash position dashboard snapshot.
    Requires: treasury_read or treasury_admin role.
    """
    accounts = _demo_accounts()

    if entity_id:
        accounts = [a for a in accounts if a.entity_id == entity_id]
    if currency:
        accounts = [a for a in accounts if a.currency == currency.upper()]

    # Exposure would come from the exposure calculator service
    total_exposure = Decimal("3200000.00")  # demo value in EUR

    dashboard = await aggregator.build_dashboard(accounts, total_exposure)
    return dashboard


@app.get("/api/v1/accounts")
async def list_accounts(
    account_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
):
    """List all cash accounts with current balances."""
    accounts = _demo_accounts()
    if account_type:
        accounts = [a for a in accounts if a.account_type == account_type]
    if entity_id:
        accounts = [a for a in accounts if a.entity_id == entity_id]

    return [
        {
            "id": a.id,
            "entity_id": a.entity_id,
            "name": a.account_name,
            "type": a.account_type,
            "currency": a.currency,
            "provider": a.provider,
            "balance": str(a.balance),
            "available": str(a.available_balance),
            "pending_in": str(a.pending_in),
            "pending_out": str(a.pending_out),
            "last_synced": a.last_synced.isoformat() if a.last_synced else None,
        }
        for a in accounts
    ]


@app.get("/api/v1/fx-rates")
async def get_fx_rates():
    """Current exchange rates used for consolidation."""
    return {
        "reporting_currency": REPORTING_CURRENCY,
        "rates": {k: str(v) for k, v in fx_service.rates.items()},
        "last_update": fx_service._last_update.isoformat(),
    }


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """
    Real-time dashboard feed.
    Pushes updated cash position every DASHBOARD_PUSH_INTERVAL seconds.

    Authentication: In production, validate JWT token from query param:
        ws://host/ws/dashboard?token=<jwt>
    """
    client_id = str(uuid4())[:8]
    await manager.connect(websocket, client_id)

    try:
        while True:
            accounts = _demo_accounts()
            exposure = Decimal("3200000.00")
            dashboard = await aggregator.build_dashboard(accounts, exposure)

            await websocket.send_text(
                json.dumps(dashboard.model_dump(), default=str)
            )
            await asyncio.sleep(DASHBOARD_PUSH_INTERVAL)

    except WebSocketDisconnect:
        manager.disconnect(client_id)


# ---------------------------------------------------------------------------
# Background Tasks
# ---------------------------------------------------------------------------

async def _fx_refresh_loop():
    """Periodically refresh FX rates."""
    while True:
        try:
            await fx_service.refresh_rates()
        except Exception as e:
            logger.error(f"FX rate refresh failed: {e}")
        await asyncio.sleep(FX_REFRESH_INTERVAL)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "realtime_dashboard:app",
        host="0.0.0.0",
        port=8100,
        reload=False,
        ssl_keyfile="/etc/ssl/private/dashboard.key",     # PCI DSS Req 4.1
        ssl_certfile="/etc/ssl/certs/dashboard.crt",
        log_level="info",
    )
