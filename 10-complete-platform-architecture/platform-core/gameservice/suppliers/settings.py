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
suppliers/settings.py
---------------------
Per-supplier, per-brand, per-jurisdiction configuration.

Settings are resolved with a four-level hierarchy (most specific wins):

    1. supplier + brand + jurisdiction
    2. supplier + brand
    3. supplier + jurisdiction
    4. supplier only

This matches the Scala SupplierSettings ChainedSegmentedSettings pattern.

In production, settings are loaded from the database (SUPPLIER_SETTINGS
table) and cached in-process. They are reloadable without a restart via
the /admin/reload endpoint.

For this reference implementation, settings are loaded from environment
variables with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Setting descriptor
# ---------------------------------------------------------------------------


@dataclass
class SettingDescriptor:
    """Describes a single configurable setting and its default value."""

    key: str
    description: str
    default: Any
    type_: type = str


# ---------------------------------------------------------------------------
# Well-known supplier settings
# ---------------------------------------------------------------------------


class Settings:
    """Namespace for well-known supplier setting keys."""

    # Token lifetime in seconds (default 12 hours)
    LAUNCH_TOKEN_LIFETIME = SettingDescriptor(
        key="launch-token-lifetime",
        description="Seconds before a game-launch token expires",
        default=60 * 60 * 12,
        type_=int,
    )

    # When True, the launch token is single-use
    ONE_OFF_LAUNCH_TOKEN = SettingDescriptor(
        key="one-off-launch-token",
        description="Invalidate launch token after first use",
        default=False,
        type_=bool,
    )

    # Whether the supplier supports the generic iframe reality-check flow
    GENERIC_IFRAME_RC = SettingDescriptor(
        key="generic-iframe-rc",
        description="Supplier supports generic iframe reality-check solution",
        default=False,
        type_=bool,
    )

    # Risk-free bet support
    RISK_FREE_BETS = SettingDescriptor(
        key="risk-free-bet-support",
        description="Supplier supports risk-free bet promotions",
        default=False,
        type_=bool,
    )

    # Free spins: apply immediately via back-office
    FREE_SPIN_IMMEDIATE_APPLY = SettingDescriptor(
        key="award-free-spin-immed-apply",
        description="Allow free-spin award to be applied immediately via BO",
        default=False,
        type_=bool,
    )

    # SMUX parameter separator
    SMUX_SEPARATOR = SettingDescriptor(
        key="smux-separator",
        description="Separator used for SMUX parameter wrapper",
        default=".",
        type_=str,
    )

    # Base URL for supplier API calls
    API_BASE_URL = SettingDescriptor(
        key="api-base-url",
        description="Base URL for outbound API calls to the supplier",
        default="",
        type_=str,
    )

    # Shared secret / HMAC key for request signing
    API_SECRET = SettingDescriptor(
        key="api-secret",
        description="Shared HMAC secret for request authentication",
        default="",
        type_=str,
    )

    # Operator ID as assigned by the supplier
    OPERATOR_ID = SettingDescriptor(
        key="operator-id",
        description="Operator identifier registered with the supplier",
        default="",
        type_=str,
    )

    # Whether to send balance in major or minor units
    BALANCE_IN_MAJOR_UNITS = SettingDescriptor(
        key="balance-in-major-units",
        description="Send balance as major units (e.g. GBP) rather than pence",
        default=True,
        type_=bool,
    )

    # Timeout in milliseconds for outbound HTTP calls
    HTTP_TIMEOUT_MS = SettingDescriptor(
        key="http-timeout-ms",
        description="Timeout in milliseconds for outbound HTTP calls",
        default=5000,
        type_=int,
    )


# ---------------------------------------------------------------------------
# SupplierSettings dataclass
# ---------------------------------------------------------------------------


@dataclass
class SupplierSettings:
    """
    Resolved settings for a (supplier, brand, jurisdiction) combination.

    Loaded once at startup and refreshed on demand. The `raw` dict holds
    any supplier-specific settings not covered by the well-known keys above.
    """

    supplier_id: str
    brand_id: Optional[str] = None
    jurisdiction_id: Optional[str] = None

    # Well-known settings with their resolved values
    launch_token_lifetime: int = Settings.LAUNCH_TOKEN_LIFETIME.default
    one_off_launch_token: bool = Settings.ONE_OFF_LAUNCH_TOKEN.default
    generic_iframe_rc: bool = Settings.GENERIC_IFRAME_RC.default
    risk_free_bets: bool = Settings.RISK_FREE_BETS.default
    free_spin_immediate_apply: bool = Settings.FREE_SPIN_IMMEDIATE_APPLY.default
    smux_separator: str = Settings.SMUX_SEPARATOR.default
    api_base_url: str = Settings.API_BASE_URL.default
    api_secret: str = Settings.API_SECRET.default
    operator_id: str = Settings.OPERATOR_ID.default
    balance_in_major_units: bool = Settings.BALANCE_IN_MAJOR_UNITS.default
    http_timeout_ms: int = Settings.HTTP_TIMEOUT_MS.default

    # Arbitrary supplier-specific settings
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, descriptor: SettingDescriptor, default: Any = None) -> Any:
        """Retrieve a setting by its descriptor, falling back to the descriptor's default."""
        value = self.raw.get(descriptor.key, default if default is not None else descriptor.default)
        try:
            return descriptor.type_(value)
        except (ValueError, TypeError):
            return descriptor.default


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_from_env(supplier_id: str, brand_id: Optional[str] = None) -> SupplierSettings:
    """
    Load supplier settings from environment variables.

    Env var naming convention::

        SUPPLIER_<SUPPLIER_ID>_<KEY>
        SUPPLIER_<SUPPLIER_ID>_BRAND_<BRAND_ID>_<KEY>

    Examples::

        SUPPLIER_EVOLUTION_API_BASE_URL=https://evolution.example.com
        SUPPLIER_EVOLUTION_API_SECRET=s3cr3t
        SUPPLIER_EVOLUTION_BRAND_ACME_OPERATOR_ID=op123

    This is the default loader for development and CI. Production uses a
    database-backed loader with caching and TTL-based reload.
    """
    prefix = f"SUPPLIER_{supplier_id.upper()}_"
    brand_prefix = f"SUPPLIER_{supplier_id.upper()}_BRAND_{(brand_id or '').upper()}_"

    def env(key: str, descriptor: SettingDescriptor) -> Any:
        """Resolve env var with brand override taking precedence."""
        env_key = key.upper().replace("-", "_")
        value = (
            os.environ.get(f"{brand_prefix}{env_key}")
            or os.environ.get(f"{prefix}{env_key}")
        )
        if value is None:
            return descriptor.default
        try:
            if descriptor.type_ is bool:
                return value.lower() in ("1", "true", "yes")
            return descriptor.type_(value)
        except (ValueError, TypeError):
            return descriptor.default

    return SupplierSettings(
        supplier_id=supplier_id,
        brand_id=brand_id,
        launch_token_lifetime=env("launch_token_lifetime", Settings.LAUNCH_TOKEN_LIFETIME),
        one_off_launch_token=env("one_off_launch_token", Settings.ONE_OFF_LAUNCH_TOKEN),
        generic_iframe_rc=env("generic_iframe_rc", Settings.GENERIC_IFRAME_RC),
        risk_free_bets=env("risk_free_bets", Settings.RISK_FREE_BETS),
        free_spin_immediate_apply=env("free_spin_immediate_apply", Settings.FREE_SPIN_IMMEDIATE_APPLY),
        smux_separator=env("smux_separator", Settings.SMUX_SEPARATOR),
        api_base_url=env("api_base_url", Settings.API_BASE_URL),
        api_secret=env("api_secret", Settings.API_SECRET),
        operator_id=env("operator_id", Settings.OPERATOR_ID),
        balance_in_major_units=env("balance_in_major_units", Settings.BALANCE_IN_MAJOR_UNITS),
        http_timeout_ms=env("http_timeout_ms", Settings.HTTP_TIMEOUT_MS),
    )
