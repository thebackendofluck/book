# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Multi-jurisdiction configuration for the Acme Import registration-time
self-exclusion matcher.

Python port of Config.scala referenced in chapter 26, section
"Multi-Jurisdiction Registration-Time Matching (Acme Import Import)". The
configuration object carries everything the RegistrationConsumerService
needs to run end-to-end:

- Per-source SFTP settings (one entry per exclusion list provider, e.g.
  NJ DGE, PA DAP, MGA, CRUKS, OASIS)
- Levenshtein distance threshold for the fuzzy PA DAP matcher
- Kafka consumer backoff and max-restart parameters
- OpsGenie / alerting webhook for match-found notifications
- Operational mode flag (`AsyncKafka` vs `SyncHttp`) so the same matcher
  code can serve both the streaming pipeline and manual compliance
  lookups

Environment variables are the source of truth in production. This
module builds a `AcmeImportConfig` dataclass from the environment, with
clearly-named defaults and explicit validation so that a misconfigured
deployment fails loudly at startup rather than silently at matching
time.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from typing import Any


class MatchingMode(enum.Enum):
    """How the matcher is being invoked.

    - `ASYNC_KAFKA` -- the long-running consumer reads from Kafka and
      processes every registration event automatically.
    - `SYNC_HTTP`   -- a compliance operator hit the manual lookup
      endpoint; the same matching logic runs but results are returned
      directly to the caller instead of creating lock tasks.
    """

    ASYNC_KAFKA = "AsyncKafka"
    SYNC_HTTP = "SyncHttp"


@dataclass(frozen=True)
class SftpSourceConfig:
    """SFTP settings for a single exclusion list provider."""

    source_id: str             # e.g. "nj-dge", "pa-dap"
    host: str
    port: int
    username: str
    # Either password or private_key_path must be set. Password is for
    # legacy sources that only support password auth; key-based is
    # preferred.
    password: str | None
    private_key_path: str | None
    remote_path: str           # directory or file pattern to pull
    poll_interval_seconds: int  # how often to check for updates

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError(f"source {self.source_id}: host is required")
        if self.port <= 0 or self.port > 65535:
            raise ValueError(
                f"source {self.source_id}: port {self.port} out of range"
            )
        if not self.username:
            raise ValueError(f"source {self.source_id}: username is required")
        if self.password is None and self.private_key_path is None:
            raise ValueError(
                f"source {self.source_id}: either password or private_key_path "
                "must be set"
            )
        if self.poll_interval_seconds <= 0:
            raise ValueError(
                f"source {self.source_id}: poll_interval_seconds must be positive"
            )


@dataclass(frozen=True)
class KafkaConfig:
    """Kafka consumer configuration for the registration event stream."""

    bootstrap_servers: str
    topic: str
    group_id: str
    min_backoff_seconds: float
    max_backoff_seconds: float
    max_restarts: int

    def __post_init__(self) -> None:
        if self.min_backoff_seconds <= 0:
            raise ValueError("min_backoff_seconds must be positive")
        if self.max_backoff_seconds < self.min_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= min_backoff_seconds")
        if self.max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")


@dataclass(frozen=True)
class AlertingConfig:
    """OpsGenie / webhook configuration for match-found notifications."""

    webhook_url: str | None
    api_key: str | None
    enabled: bool = True
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.enabled and not self.webhook_url:
            raise ValueError("alerting enabled but no webhook_url provided")


@dataclass(frozen=True)
class MatchingConfig:
    """Fuzzy matching thresholds and rules.

    `pa_dap_levenshtein_max_distance` controls how many edits are
    tolerated in a PA DAP name match. In production this has been
    observed to work well at 2; setting it higher produces too many
    false positives, lower fails to catch common typos.
    """

    pa_dap_levenshtein_max_distance: int = 2
    # Fraction of DOB bits that must match for a DOB-based waterfall
    # step to count. 1.0 = exact match.
    dob_match_required: float = 1.0
    nj_dge_ssn_partial_digits: int = 4

    def __post_init__(self) -> None:
        if self.pa_dap_levenshtein_max_distance < 0:
            raise ValueError("pa_dap_levenshtein_max_distance must be non-negative")
        if not (0.0 < self.dob_match_required <= 1.0):
            raise ValueError("dob_match_required must be in (0, 1]")
        if not (1 <= self.nj_dge_ssn_partial_digits <= 9):
            raise ValueError("nj_dge_ssn_partial_digits must be in [1, 9]")


@dataclass(frozen=True)
class AcmeImportConfig:
    """Top-level configuration for the Acme Import matcher."""

    mode: MatchingMode
    sources: dict[str, SftpSourceConfig]
    kafka: KafkaConfig
    alerting: AlertingConfig
    matching: MatchingConfig
    # PII lookup endpoint: where the matcher fetches player data by user id
    pii_lookup_url: str
    pii_lookup_timeout_seconds: float = 3.0

    def source(self, source_id: str) -> SftpSourceConfig:
        """Return the SftpSourceConfig for a source_id or raise KeyError."""
        try:
            return self.sources[source_id]
        except KeyError as err:
            raise KeyError(
                f"unknown exclusion source: {source_id!r} "
                f"(configured sources: {sorted(self.sources)})"
            ) from err


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None) -> str:
    """Read an environment variable with a useful error on missing."""
    value = os.environ.get(name, default)
    if value is None:
        raise KeyError(
            f"environment variable {name!r} is required for Acme Import configuration"
        )
    return value


def _env_int(name: str, default: int | None = None) -> int:
    raw = _env(name, str(default) if default is not None else None)
    try:
        return int(raw)
    except ValueError as err:
        raise ValueError(f"{name}={raw!r} is not a valid integer") from err


def _env_float(name: str, default: float | None = None) -> float:
    raw = _env(name, str(default) if default is not None else None)
    try:
        return float(raw)
    except ValueError as err:
        raise ValueError(f"{name}={raw!r} is not a valid float") from err


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def load_acme_import_config_from_env() -> AcmeImportConfig:
    """Build a AcmeImportConfig from the current environment.

    Required variables:
        ACME_IMPORT_MODE                 -- "AsyncKafka" or "SyncHttp"
        ACME_IMPORT_KAFKA_BOOTSTRAP      -- e.g. "kafka-1:9092,kafka-2:9092"
        ACME_IMPORT_KAFKA_TOPIC          -- e.g. "player.registration"
        ACME_IMPORT_KAFKA_GROUP_ID       -- e.g. "acme-import-matcher"
        ACME_IMPORT_PII_LOOKUP_URL       -- e.g. "http://pii-service.internal/lookup"
        ACME_IMPORT_NJ_DGE_HOST          -- SFTP host for NJ DGE
        ACME_IMPORT_NJ_DGE_USER          -- SFTP user for NJ DGE
        ACME_IMPORT_NJ_DGE_KEY           -- path to private key for NJ DGE
        ACME_IMPORT_PA_DAP_HOST          -- SFTP host for PA DAP
        ACME_IMPORT_PA_DAP_USER          -- SFTP user for PA DAP
        ACME_IMPORT_PA_DAP_PASSWORD      -- PA DAP password (legacy auth)

    Optional variables:
        ACME_IMPORT_KAFKA_MIN_BACKOFF    -- default 2.0 seconds
        ACME_IMPORT_KAFKA_MAX_BACKOFF    -- default 60.0 seconds
        ACME_IMPORT_KAFKA_MAX_RESTARTS   -- default 5
        ACME_IMPORT_LEVENSHTEIN_MAX      -- default 2
        ACME_IMPORT_ALERT_WEBHOOK        -- OpsGenie webhook URL (alerts enabled when set)
        ACME_IMPORT_ALERT_API_KEY        -- OpsGenie API key
        ACME_IMPORT_ALERT_ENABLED        -- default True when webhook is set

    Validation errors raise before the service accepts traffic, which
    is the desired behaviour -- a misconfigured matcher must not quietly
    approve registrations that should have been blocked.
    """
    mode_raw = _env("ACME_IMPORT_MODE", MatchingMode.ASYNC_KAFKA.value)
    try:
        mode = MatchingMode(mode_raw)
    except ValueError as err:
        raise ValueError(
            f"ACME_IMPORT_MODE={mode_raw!r} is not valid; use "
            f"{[m.value for m in MatchingMode]}"
        ) from err

    sources: dict[str, SftpSourceConfig] = {}
    sources["nj-dge"] = SftpSourceConfig(
        source_id="nj-dge",
        host=_env("ACME_IMPORT_NJ_DGE_HOST"),
        port=_env_int("ACME_IMPORT_NJ_DGE_PORT", 22),
        username=_env("ACME_IMPORT_NJ_DGE_USER"),
        password=os.environ.get("ACME_IMPORT_NJ_DGE_PASSWORD"),
        private_key_path=os.environ.get("ACME_IMPORT_NJ_DGE_KEY"),
        remote_path=_env("ACME_IMPORT_NJ_DGE_PATH", "/exclusion-list/"),
        poll_interval_seconds=_env_int("ACME_IMPORT_NJ_DGE_POLL_SECONDS", 3600),
    )
    sources["pa-dap"] = SftpSourceConfig(
        source_id="pa-dap",
        host=_env("ACME_IMPORT_PA_DAP_HOST"),
        port=_env_int("ACME_IMPORT_PA_DAP_PORT", 22),
        username=_env("ACME_IMPORT_PA_DAP_USER"),
        password=os.environ.get("ACME_IMPORT_PA_DAP_PASSWORD"),
        private_key_path=os.environ.get("ACME_IMPORT_PA_DAP_KEY"),
        remote_path=_env("ACME_IMPORT_PA_DAP_PATH", "/DAP/"),
        poll_interval_seconds=_env_int("ACME_IMPORT_PA_DAP_POLL_SECONDS", 3600),
    )

    kafka = KafkaConfig(
        bootstrap_servers=_env("ACME_IMPORT_KAFKA_BOOTSTRAP"),
        topic=_env("ACME_IMPORT_KAFKA_TOPIC"),
        group_id=_env("ACME_IMPORT_KAFKA_GROUP_ID"),
        min_backoff_seconds=_env_float("ACME_IMPORT_KAFKA_MIN_BACKOFF", 2.0),
        max_backoff_seconds=_env_float("ACME_IMPORT_KAFKA_MAX_BACKOFF", 60.0),
        max_restarts=_env_int("ACME_IMPORT_KAFKA_MAX_RESTARTS", 5),
    )

    alerting = AlertingConfig(
        webhook_url=os.environ.get("ACME_IMPORT_ALERT_WEBHOOK"),
        api_key=os.environ.get("ACME_IMPORT_ALERT_API_KEY"),
        enabled=_env_bool(
            "ACME_IMPORT_ALERT_ENABLED", os.environ.get("ACME_IMPORT_ALERT_WEBHOOK") is not None
        ),
    )

    matching = MatchingConfig(
        pa_dap_levenshtein_max_distance=_env_int("ACME_IMPORT_LEVENSHTEIN_MAX", 2),
        dob_match_required=_env_float("ACME_IMPORT_DOB_MATCH_REQUIRED", 1.0),
        nj_dge_ssn_partial_digits=_env_int("ACME_IMPORT_NJ_SSN_PARTIAL", 4),
    )

    return AcmeImportConfig(
        mode=mode,
        sources=sources,
        kafka=kafka,
        alerting=alerting,
        matching=matching,
        pii_lookup_url=_env("ACME_IMPORT_PII_LOOKUP_URL"),
        pii_lookup_timeout_seconds=_env_float("ACME_IMPORT_PII_LOOKUP_TIMEOUT", 3.0),
    )
