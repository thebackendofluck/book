# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
platform.config — Hierarchical Configuration
=============================================

Configuration is read from three layered sources (lowest → highest priority):

1. **Hardcoded defaults** — sensible out-of-the-box values that work for
   local development.
2. **Environment variables** — overrides via ``ACME_*`` prefixed variables,
   loaded automatically via :func:`_from_env`.
3. **Brand-level overrides** — per-brand settings that override the platform
   defaults for white-label deployments.

Design decisions
----------------
* **Pydantic BaseModel** is used for all config objects so that environment
  variable parsing, type coercion, and validation are handled automatically.
* **No global singleton** — callers receive a ``PlatformConfig`` instance
  which may be injected via dependency injection rather than imported as a
  module-level global.  This makes tests hermetic.
* **Decimal for monetary defaults** — consistent with the rest of the domain.
* **Secrets are not stored here** — database passwords, API keys, and signing
  secrets should live in environment variables or a secrets manager (e.g.
  AWS Secrets Manager, HashiCorp Vault).  This module only stores metadata
  about *where* to find secrets, not the secrets themselves.

Example::

    import os
    os.environ["ACME_DEBUG"] = "true"
    os.environ["ACME_DEFAULT_CURRENCY"] = "GBP"

    config = PlatformConfig.from_env()
    assert config.debug is True
    assert config.default_currency == "GBP"

    brand_cfg = config.brand_config("acme_uk")
    assert brand_cfg.currency == "GBP"  # inherits
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Brand-level configuration
# ---------------------------------------------------------------------------


class BrandConfig(BaseModel):
    """Configuration for a single white-label brand.

    Brand configs inherit from the platform defaults and override specific
    fields.  This allows ``acme_uk`` and ``acme_de`` to share 90% of their
    configuration while each having their own wallet endpoint, currency, and
    jurisdiction.

    Attributes
    ----------
    brand_id:
        Unique brand identifier (e.g. ``"acme_uk"``, ``"acme_mt"``).
    display_name:
        Human-readable brand name shown in logs and admin UIs.
    jurisdiction:
        Primary regulatory jurisdiction for this brand.
    currency:
        Default ISO-4217 currency code.
    wallet_api_url:
        Base URL of the brand's wallet API back-end.
    wallet_api_timeout_seconds:
        HTTP timeout for wallet API calls.
    max_session_duration_minutes:
        Maximum session duration enforced by this brand, regardless of
        jurisdiction settings.  ``0`` means no platform-level cap.
    supports_demo_mode:
        Whether this brand's player-facing frontend offers demo play.
    extra:
        Arbitrary key-value pairs for brand-specific extensions not covered
        by the standard fields.
    """

    brand_id: str
    display_name: str = ""
    jurisdiction: str = "MGA"
    currency: str = "EUR"
    wallet_api_url: str = "http://localhost:8080/wallet"
    wallet_api_timeout_seconds: int = 5
    max_session_duration_minutes: int = 0
    supports_demo_mode: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Platform-wide configuration
# ---------------------------------------------------------------------------


class PlatformConfig(BaseModel):
    """Top-level platform configuration.

    Attributes
    ----------
    environment:
        Deployment environment name (``"development"``, ``"staging"``,
        ``"production"``).
    debug:
        Enable verbose debug logging.  Should be ``False`` in production.
    default_currency:
        Platform default currency used when a brand does not specify one.
    default_jurisdiction:
        Platform default jurisdiction code.
    supplier_callback_secret:
        HMAC secret for verifying supplier webhook callbacks.  Load from
        an environment variable — do not hardcode.
    db_url:
        Database connection URL.  Loaded from ``ACME_DB_URL``.
    redis_url:
        Redis connection URL for caching and idempotency storage.
    kafka_bootstrap_servers:
        Comma-separated Kafka broker addresses.
    session_ttl_seconds:
        How long a player session remains valid after the last activity.
    idempotency_ttl_seconds:
        How long supplier_ref idempotency keys are retained.
    brands:
        Registry of per-brand configurations, keyed by ``brand_id``.
    """

    environment: str = Field(default="development", description="Deployment environment.")
    debug: bool = Field(default=False, description="Enable debug logging.")
    default_currency: str = Field(default="EUR", description="Platform default currency.")
    default_jurisdiction: str = Field(default="MGA", description="Default jurisdiction code.")
    supplier_callback_secret: str = Field(
        default="",
        description="HMAC secret for supplier callback verification.",
    )
    db_url: str = Field(
        default="sqlite+aiosqlite:///./acmetocasino_dev.db",
        description="Async database connection URL.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Comma-separated Kafka broker addresses.",
    )
    session_ttl_seconds: int = Field(
        default=3600,
        description="Session validity window after last activity.",
    )
    idempotency_ttl_seconds: int = Field(
        default=86400,
        description="Retention window for idempotency keys.",
    )
    brands: dict[str, BrandConfig] = Field(
        default_factory=dict,
        description="Per-brand configuration registry.",
    )

    @field_validator("default_currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, prefix: str = "ACME_") -> PlatformConfig:
        """Construct a :class:`PlatformConfig` from environment variables.

        Each ``ACME_`` prefixed variable maps to the corresponding field
        (case-insensitive, prefix stripped).  Unknown variables are ignored.

        Parameters
        ----------
        prefix:
            Environment variable prefix.  Defaults to ``"ACME_"``.

        Returns
        -------
        PlatformConfig
            Populated from the current environment.

        Example
        -------
        ::

            export ACME_DEBUG=true
            export ACME_DEFAULT_CURRENCY=GBP
            export ACME_SESSION_TTL_SECONDS=7200

            config = PlatformConfig.from_env()
        """
        field_names = set(cls.model_fields.keys())
        overrides: dict[str, Any] = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                field_name = key[len(prefix):].lower()
                if field_name in field_names:
                    overrides[field_name] = value
        return cls(**overrides)

    # ------------------------------------------------------------------
    # Brand resolution
    # ------------------------------------------------------------------

    def brand_config(self, brand_id: str) -> BrandConfig:
        """Return the configuration for *brand_id*, with platform defaults.

        If *brand_id* is not registered, a default :class:`BrandConfig` is
        returned inheriting platform-level currency and jurisdiction.

        Parameters
        ----------
        brand_id:
            The brand to look up.

        Returns
        -------
        BrandConfig
            Registered brand config, or a freshly-constructed default.
        """
        if brand_id in self.brands:
            return self.brands[brand_id]
        return BrandConfig(
            brand_id=brand_id,
            currency=self.default_currency,
            jurisdiction=self.default_jurisdiction,
        )

    def register_brand(self, brand: BrandConfig) -> None:
        """Add or replace a brand in the config registry.

        Parameters
        ----------
        brand:
            The brand configuration to register.
        """
        self.brands[brand.brand_id] = brand

    # ------------------------------------------------------------------
    # Convenience checks
    # ------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """Return True when running in the production environment."""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Return True when running locally."""
        return self.environment.lower() == "development"


__all__ = ["BrandConfig", "PlatformConfig"]
