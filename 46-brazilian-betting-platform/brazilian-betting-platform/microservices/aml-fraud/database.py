# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AML/Fraud Detection Service — Database Layer
=============================================
SQLAlchemy async session factory for PostgreSQL and Neo4j async driver wrapper.

Environment variables:
  DATABASE_URL   — PostgreSQL async URL (postgresql+asyncpg://...)
  NEO4J_URI      — bolt://host:7687
  NEO4J_USER     — Neo4j username
  NEO4J_PASSWORD — Neo4j password
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://aml:aml_secret@localhost:5432/aml",
)

NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "changeme")


# ── ORM Base ──────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


# ── ORM Models ────────────────────────────────────────────────────────────────


class TransactionRecord(Base):
    """Persisted transaction analysis record."""

    __tablename__ = "aml_transactions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    transaction_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")
    flags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_aml_txn_cpf_analyzed", "cpf_hash", "analyzed_at"),
    )


class COAFReportRecord(Base):
    """Persisted COAF SAR record."""

    __tablename__ = "coaf_reports"

    report_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    report_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True
    )
    urgency: Mapped[str] = mapped_column(String(10), nullable=False, default="NORMAL")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    coaf_protocol: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


# ── PostgreSQL Engine and Session Factory ─────────────────────────────────────

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autobegin=True,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables() -> None:
    """Create all tables (dev/test convenience)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database.tables_created")


async def dispose_engine() -> None:
    """Cleanly dispose the connection pool on shutdown."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        log.info("database.engine_disposed")


# ── Neo4j Connection Manager ──────────────────────────────────────────────────


class Neo4jManager:
    """Thin async wrapper around the neo4j async driver.

    Usage:
        async with Neo4jManager() as neo4j:
            async with neo4j.session() as session:
                await session.run(...)
    """

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: str = NEO4J_PASSWORD,
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: Any = None

    async def connect(self) -> None:
        try:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            await self._driver.verify_connectivity()
            log.info("neo4j.connected", uri=self._uri)
        except Exception as exc:
            log.warning("neo4j.connect_failed", error=str(exc))
            self._driver = None

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None
            log.info("neo4j.disconnected")

    def session(self) -> Any:
        """Return a Neo4j async session context manager."""
        if self._driver is None:
            raise RuntimeError("Neo4j driver not connected")
        return self._driver.session()

    @property
    def is_healthy(self) -> bool:
        return self._driver is not None

    async def __aenter__(self) -> "Neo4jManager":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


# Singleton instance shared across the app lifecycle
_neo4j: Neo4jManager | None = None


def get_neo4j() -> Neo4jManager:
    global _neo4j
    if _neo4j is None:
        _neo4j = Neo4jManager()
    return _neo4j
