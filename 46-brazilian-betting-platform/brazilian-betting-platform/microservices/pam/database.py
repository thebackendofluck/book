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
PAM Service — Database Layer
=============================
SQLAlchemy async models and session factory for PostgreSQL.

Table layout:
  players          — core account record (PII-minimal; hashed where possible)
  player_sessions  — active / historical session log

Connection is configured via environment variables:
  DATABASE_URL     — e.g. postgresql+asyncpg://user:pass@host:5432/pam
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
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pam:pam_secret@localhost:5432/pam",
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class PlayerRecord(Base):
    """Core player account row.

    PII policy:
      - full_name, email stored as-is (operator must encrypt at rest)
      - cpf, phone, document_number stored as SHA-256 hashes only
    """

    __tablename__ = "players"

    player_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cpf_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    date_of_birth: Mapped[str] = mapped_column(String(10), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    address_cep: Mapped[str] = mapped_column(String(9), nullable=False)
    address_street: Mapped[str] = mapped_column(String(200), nullable=False)
    address_number: Mapped[str] = mapped_column(String(20), nullable=False)
    address_city: Mapped[str] = mapped_column(String(100), nullable=False)
    address_state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    document_number_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gender: Mapped[str] = mapped_column(String(1), nullable=False, default="N")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    lgpd_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lgpd_consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    biometric_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_verification_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audit_trail: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_players_status_next_verif", "status", "next_verification_due"),
    )


class PlayerSessionRecord(Base):
    """Tracks active and historical player sessions."""

    __tablename__ = "player_sessions"

    session_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    player_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("players.player_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


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
    """Create all tables if they don't exist (dev / testing convenience)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Cleanly dispose the connection pool on shutdown."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
