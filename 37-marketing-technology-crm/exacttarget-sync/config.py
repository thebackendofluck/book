# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Multi-brand configuration for the exacttarget-sync pipeline.

Python port of Config.scala referenced in chapter 37. The production
deployment runs one ExactTarget tenant per casino brand -- a
white-label operator with five brands carries five separate OAuth
client IDs/secrets and five separate SFTP upload paths. This module
builds a `SyncConfig` dataclass tree from HOCON-style environment
variables so that adding a new brand is a single `EXACTTARGET_BRAND_
<ID>_*` set of variables rather than a code change.

All validation happens in `__post_init__` so a misconfigured deployment
fails before the first task runs instead of halfway through an import.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrandConfig:
    """Per-brand OAuth and SFTP configuration for one ExactTarget tenant."""

    brand_id: str
    #: ExactTarget SOAP endpoint for the brand, e.g. `https://webservice.s11.exacttarget.com/`
    soap_endpoint: str
    #: OAuth2 authorisation endpoint
    auth_endpoint: str
    oauth_client_id: str
    oauth_client_secret: str
    sftp_host: str
    sftp_port: int
    sftp_username: str
    sftp_private_key_path: str
    sftp_upload_dir: str
    sftp_download_dir: str

    def __post_init__(self) -> None:
        if not self.brand_id:
            raise ValueError("brand_id is required")
        if not self.soap_endpoint.startswith(("http://", "https://")):
            raise ValueError(f"brand {self.brand_id}: soap_endpoint must be an HTTP URL")
        if not self.oauth_client_id or not self.oauth_client_secret:
            raise ValueError(f"brand {self.brand_id}: OAuth credentials are required")
        if not (0 < self.sftp_port <= 65535):
            raise ValueError(f"brand {self.brand_id}: sftp_port {self.sftp_port} out of range")
        if not self.sftp_host:
            raise ValueError(f"brand {self.brand_id}: sftp_host is required")


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL connection configuration."""

    host: str
    port: int
    database: str
    username: str
    password: str
    schema: str = "public"
    statement_timeout_ms: int = 30_000
    connection_pool_size: int = 5

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("database host is required")
        if not (0 < self.port <= 65535):
            raise ValueError(f"database port {self.port} out of range")
        if self.connection_pool_size < 1:
            raise ValueError("connection_pool_size must be at least 1")


@dataclass(frozen=True)
class ExportConfig:
    """PlayersExportTask tunables."""

    local_export_dir: str
    csv_split_line_count: int = 500_000
    safety_overlap_hours: int = 4
    retention_days: int = 3
    full_range_flag_name: str = "--fullRange"
    tokenized_flag_name: str = "--tokenized"
    last_export_timestamp_file: str = "last_export.ts"

    def __post_init__(self) -> None:
        if self.csv_split_line_count <= 0:
            raise ValueError("csv_split_line_count must be positive")
        if self.retention_days < 0:
            raise ValueError("retention_days must be non-negative")


@dataclass(frozen=True)
class AlertingConfig:
    """OpsGenie configuration for the task lifecycle alerter."""

    opsgenie_api_key: str | None
    opsgenie_team: str | None
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled and not self.opsgenie_api_key:
            raise ValueError("alerting enabled but no opsgenie_api_key provided")


@dataclass(frozen=True)
class SyncConfig:
    """Top-level configuration aggregating every subsection."""

    brands: dict[str, BrandConfig]
    database: DatabaseConfig
    export: ExportConfig
    alerting: AlertingConfig

    def brand(self, brand_id: str) -> BrandConfig:
        try:
            return self.brands[brand_id]
        except KeyError as err:
            known = ", ".join(sorted(self.brands)) or "(none)"
            raise KeyError(
                f"unknown brand {brand_id!r}; configured: {known}"
            ) from err


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise KeyError(f"environment variable {name!r} is required")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as err:
        raise ValueError(f"{name}={raw!r} is not an integer") from err


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def load_config_from_env() -> SyncConfig:
    """Build a SyncConfig from the current environment.

    The caller supplies brand IDs via `EXACTTARGET_BRANDS` (comma
    separated). For each brand `X`, the loader reads:

        EXACTTARGET_BRAND_X_SOAP_ENDPOINT
        EXACTTARGET_BRAND_X_AUTH_ENDPOINT
        EXACTTARGET_BRAND_X_OAUTH_CLIENT_ID
        EXACTTARGET_BRAND_X_OAUTH_CLIENT_SECRET
        EXACTTARGET_BRAND_X_SFTP_HOST
        EXACTTARGET_BRAND_X_SFTP_USER
        EXACTTARGET_BRAND_X_SFTP_KEY
    """
    brand_ids = [b.strip() for b in _require("EXACTTARGET_BRANDS").split(",") if b.strip()]
    if not brand_ids:
        raise ValueError("EXACTTARGET_BRANDS is empty")

    brands: dict[str, BrandConfig] = {}
    for bid in brand_ids:
        prefix = f"EXACTTARGET_BRAND_{bid.upper()}_"
        brands[bid] = BrandConfig(
            brand_id=bid,
            soap_endpoint=_require(prefix + "SOAP_ENDPOINT"),
            auth_endpoint=_require(prefix + "AUTH_ENDPOINT"),
            oauth_client_id=_require(prefix + "OAUTH_CLIENT_ID"),
            oauth_client_secret=_require(prefix + "OAUTH_CLIENT_SECRET"),
            sftp_host=_require(prefix + "SFTP_HOST"),
            sftp_port=_int(prefix + "SFTP_PORT", 22),
            sftp_username=_require(prefix + "SFTP_USER"),
            sftp_private_key_path=_require(prefix + "SFTP_KEY"),
            sftp_upload_dir=os.environ.get(prefix + "SFTP_UPLOAD", "/upload"),
            sftp_download_dir=os.environ.get(prefix + "SFTP_DOWNLOAD", "/download"),
        )

    database = DatabaseConfig(
        host=_require("EXACTTARGET_DB_HOST"),
        port=_int("EXACTTARGET_DB_PORT", 5432),
        database=_require("EXACTTARGET_DB_NAME"),
        username=_require("EXACTTARGET_DB_USER"),
        password=_require("EXACTTARGET_DB_PASSWORD"),
        schema=os.environ.get("EXACTTARGET_DB_SCHEMA", "public"),
        statement_timeout_ms=_int("EXACTTARGET_DB_STATEMENT_TIMEOUT_MS", 30_000),
        connection_pool_size=_int("EXACTTARGET_DB_POOL_SIZE", 5),
    )

    export = ExportConfig(
        local_export_dir=os.environ.get("EXACTTARGET_EXPORT_DIR", "/var/lib/exacttarget-sync"),
        csv_split_line_count=_int("EXACTTARGET_CSV_SPLIT_LINES", 500_000),
        safety_overlap_hours=_int("EXACTTARGET_SAFETY_OVERLAP_HOURS", 4),
        retention_days=_int("EXACTTARGET_RETENTION_DAYS", 3),
    )

    alerting = AlertingConfig(
        opsgenie_api_key=os.environ.get("EXACTTARGET_OPSGENIE_KEY"),
        opsgenie_team=os.environ.get("EXACTTARGET_OPSGENIE_TEAM"),
        enabled=_bool("EXACTTARGET_ALERTING_ENABLED", True),
    )

    return SyncConfig(brands=brands, database=database, export=export, alerting=alerting)
