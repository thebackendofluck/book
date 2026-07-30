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
Responsible Gaming Service — Database Layer
============================================
SQLAlchemy async models and session factory for PostgreSQL.

Tables:
  rg_limits            — player deposit / loss / session limits
  rg_self_exclusions   — self-exclusion records
  rg_behavioral_alerts — behavioral risk alert log

Environment variables:
  DATABASE_URL  — e.g. postgresql+asyncpg://user:pass@host:5432/rg
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
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

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://rg:rg_secret@localhost:5432/rg",
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class LimitRecord(Base):
    """Stores deposit / loss / session limits per player."""

    __tablename__ = "rg_limits"

    limit_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    limit_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    amount_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resets_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_rg_limits_cpf_type_period", "cpf_hash", "limit_type", "period", "is_active"),
    )


class SelfExclusionRecord(Base):
    """Records self-exclusion decisions."""

    __tablename__ = "rg_self_exclusions"

    exclusion_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exclusion_type: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    national_registry_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cooling_off_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_rg_exclusions_cpf_active", "cpf_hash", "is_active"),
    )


class BehavioralAlertRecord(Base):
    """Behavioral risk alert log."""

    __tablename__ = "rg_behavioral_alerts"

    alert_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    context_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    triggered_by: Mapped[str] = mapped_column(String(100), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        Index("ix_rg_alerts_cpf_created", "cpf_hash", "created_at"),
    )


# ---------------------------------------------------------------------------
# Engine and Session Factory
# ---------------------------------------------------------------------------


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
    """FastAPI dependency — yields a managed async session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
