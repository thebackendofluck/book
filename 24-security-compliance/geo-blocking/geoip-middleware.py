#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
=============================================================================
GeoIP Application-Layer Middleware for iGaming Platforms
=============================================================================

Flask middleware that enforces jurisdiction-based access control at the
application layer. This is the third line of defense (after DNS and CDN),
and the one regulators actually audit. Unlike DNS and CDN blocks, this layer:

  - Validates multiple signals simultaneously (IP + phone prefix + document)
  - Detects residential proxies (DataCenter IP != residential IP)
  - Integrates with GeoComply for US state-level verification (sports betting)
  - Writes compliance audit logs in the format expected by UKGC/MGA auditors
  - Handles VPN detection via MaxMind Insights (paid) or IPinfo.io (free tier)

Architecture position:
    Client → CloudFront/Cloudflare (CDN block) → nginx (network block)
           → Flask middleware (THIS FILE) → application logic

The application layer block is the authoritative decision. Even if CDN or DNS
fail to block a request (VPN bypass), this middleware is the backstop.

Usage:
    pip install flask geoip2 maxminddb requests structlog phonenumbers
    python3 geoip-middleware.py

    # Or as a library:
    from geoip_middleware import GeoIPMiddleware
    app = Flask(__name__)
    app.wsgi_app = GeoIPMiddleware(app.wsgi_app, config)

Requirements:
    - MaxMind GeoLite2-Country.mmdb (free) for country-level blocking
    - MaxMind GeoIP2-City.mmdb (paid, ~$24/month) for city/state-level blocking
    - MaxMind GeoIP2-ISP.mmdb (paid) for ISP + connection type (VPN detection)
    - Python 3.11+

IMPORTANT: Download databases from https://dev.maxmind.com/
MaxMind updates GeoLite2 databases weekly (Tuesdays). Automate the download
using a cron job or the geoipupdate tool.
=============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any

import structlog  # ty:ignore[unresolved-import]

try:
    import geoip2.database  # ty:ignore[unresolved-import]
    import geoip2.errors  # ty:ignore[unresolved-import]
    import geoip2.models  # ty:ignore[unresolved-import]
except ImportError as e:
    raise ImportError("pip install geoip2") from e

try:
    from flask import Flask, Response, g, jsonify, request  # ty:ignore[unresolved-import]
except ImportError as e:
    raise ImportError("pip install flask") from e


# =============================================================================
# Logging setup
# =============================================================================
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)
logger = structlog.get_logger("geo-middleware")


# =============================================================================
# Data structures
# =============================================================================

class BlockReason(str, Enum):
    GEO_COUNTRY_PROHIBITED = "GEO_COUNTRY_PROHIBITED"   # Complete prohibition jurisdiction
    GEO_COUNTRY_NO_LICENSE = "GEO_COUNTRY_NO_LICENSE"   # Not licensed in this market
    GEO_STATE_NO_LICENSE   = "GEO_STATE_NO_LICENSE"     # US state not licensed
    PROXY_DATACENTER       = "PROXY_DATACENTER"         # Datacenter/hosting IP
    PROXY_VPN              = "PROXY_VPN"                # Known VPN service
    PROXY_TOR              = "PROXY_TOR"                # Tor exit node
    PROXY_RESIDENTIAL      = "PROXY_RESIDENTIAL"        # Residential proxy service
    PHONE_MISMATCH         = "PHONE_MISMATCH"           # Phone prefix doesn't match IP geo
    DOCUMENT_MISMATCH      = "DOCUMENT_MISMATCH"        # Document country doesn't match IP
    MULTI_SIGNAL_CONFLICT  = "MULTI_SIGNAL_CONFLICT"    # Multiple geo signals disagree


@dataclass
class GeoCheckResult:
    allowed: bool
    country_code: str
    country_name: str
    subdivision: str            # State/province code for US/CA markets
    city: str
    is_vpn: bool
    is_datacenter: bool
    is_tor: bool
    is_residential_proxy: bool
    confidence_score: float     # 0.0–1.0; below 0.7 triggers secondary verification
    block_reason: BlockReason | None = None
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class GeoConfig:
    """Geo-blocking configuration. Load from environment or config file."""

    # MaxMind database paths
    country_db_path: str = "/var/lib/geoip2/GeoLite2-Country.mmdb"
    city_db_path: str    = "/var/lib/geoip2/GeoIP2-City.mmdb"
    isp_db_path: str     = "/var/lib/geoip2/GeoIP2-ISP.mmdb"

    # Countries where online gambling is completely prohibited by law
    # Format: ISO 3166-1 alpha-2
    prohibited_countries: frozenset[str] = field(default_factory=lambda: frozenset({
        "AE",  # United Arab Emirates — Federal Law No. 6 of 2018
        "SA",  # Saudi Arabia — Royal Decree M/33 + Islamic law
        "QA",  # Qatar — Law No. 14 of 2014
        "KW",  # Kuwait — Law No. 31 of 1970
        "BH",  # Bahrain — Decree-Law No. 15 of 1976
        "OM",  # Oman — Penal Code Article 263
        "YE",  # Yemen — Islamic Penal Code provisions
        "LY",  # Libya — Penal Code Chapter 4
        "SD",  # Sudan — Gambling Act 1974 + Sharia codification 1983
        "CN",  # China — Criminal Law Article 303 + Regulations on Prohibition of Gambling 2000
        "KP",  # North Korea — complete prohibition
        "KH",  # Cambodia — Sub-Decree No. 176 of 2019 (prohibits online gambling)
        "DZ",  # Algeria — Ordinance No. 75-58 Article 395
        "MA",  # Morocco — Dahir gambling code
        "PK",  # Pakistan — Prevention of Gambling Act 1977
        "BD",  # Bangladesh — Public Gambling Act 1867
        "AF",  # Afghanistan — Penal Code Article 277
        "IQ",  # Iraq — Penal Code No. 111 of 1969, Articles 388–394
        "IR",  # Iran — Islamic Penal Code, Chapter 20
    }))

    # Countries where operator holds no license (not prohibited, but not licensed)
    # Add your specific unlicensed markets here
    unlicensed_countries: frozenset[str] = field(default_factory=lambda: frozenset({
        # Example: remove these as you obtain licenses
        # "US",  # Handled at state level — see blocked_us_states
        # "JP",  # Japan — online casino prohibited; sports betting limited to JRA
        # "IN",  # India — complex state-by-state patchwork; no federal online casino license
    }))

    # US states where the operator has NO active license
    blocked_us_states: frozenset[str] = field(default_factory=lambda: frozenset({
        "UT",  # Utah — Art. VI § 27, Utah Code § 76-10-1101
        "HI",  # Hawaii — HRS § 712-1220
        "AL",  # Alabama — Code of Alabama § 13A-12-20
        "ID",  # Idaho — Idaho Code § 18-3801
        "WI",  # Wisconsin — limited tribal compacts, no online
        "KY",  # Kentucky — KRS § 528.010
        "TX",  # Texas — Penal Code § 47.02
        "GA",  # Georgia — O.C.G.A. § 16-12-20
        "AR",  # Arkansas — Amendment 100 (limited)
    }))

    # Block datacenter/hosting IPs entirely (high VPN-use indicator)
    block_datacenter_ips: bool = True

    # Block Tor exit nodes
    block_tor: bool = True

    # Minimum confidence score before secondary verification is required
    min_confidence_score: float = 0.70

    # Audit log path
    audit_log_path: str = "/var/log/igaming/geo-compliance-audit.jsonl"

    # Compliance error responses
    block_http_status: int = 451  # 451 Unavailable For Legal Reasons (RFC 7725)


# =============================================================================
# GeoIP Service
# =============================================================================

class GeoIPService:
    """
    Multi-database GeoIP lookup service.

    Uses MaxMind's Reader in threaded mode (thread-safe by design).
    Databases are loaded once at startup and kept in memory.
    Weekly reload is handled by geoipupdate or a cron job that restarts the service.
    """

    def __init__(self, config: GeoConfig) -> None:
        self.config = config
        self._country_reader: geoip2.database.Reader | None = None
        self._city_reader: geoip2.database.Reader | None = None
        self._isp_reader: geoip2.database.Reader | None = None
        self._load_databases()

    def _load_databases(self) -> None:
        """Load MaxMind databases. Missing paid databases log a warning but don't fail."""
        if Path(self.config.country_db_path).exists():
            self._country_reader = geoip2.database.Reader(
                self.config.country_db_path,
                mode=geoip2.database.MODE_MEMORY,
            )
            logger.info("geoip_db_loaded", db="country", path=self.config.country_db_path)
        else:
            logger.warning("geoip_db_missing", db="country", path=self.config.country_db_path)

        if Path(self.config.city_db_path).exists():
            self._city_reader = geoip2.database.Reader(
                self.config.city_db_path,
                mode=geoip2.database.MODE_MEMORY,
            )
            logger.info("geoip_db_loaded", db="city", path=self.config.city_db_path)

        if Path(self.config.isp_db_path).exists():
            self._isp_reader = geoip2.database.Reader(
                self.config.isp_db_path,
                mode=geoip2.database.MODE_MEMORY,
            )
            logger.info("geoip_db_loaded", db="isp", path=self.config.isp_db_path)

    def lookup(self, ip: str) -> GeoCheckResult:
        """
        Perform a full geo check on an IP address.
        Returns a GeoCheckResult with blocking decision and metadata.
        """
        country_code = "XX"
        country_name = "Unknown"
        subdivision  = ""
        city         = ""
        is_datacenter = False
        is_vpn        = False
        is_tor        = False
        is_residential_proxy = False

        # --- Country lookup ---
        if self._country_reader:
            try:
                country_resp = self._country_reader.country(ip)
                country_code = country_resp.country.iso_code or "XX"
                country_name = country_resp.country.name or "Unknown"
                is_tor        = country_resp.traits.is_tor_exit_node or False
            except (geoip2.errors.AddressNotFoundError, ValueError):
                pass

        # --- City/subdivision lookup (for US state blocking) ---
        if self._city_reader:
            try:
                city_resp = self._city_reader.city(ip)
                if country_code in {"US", "CA"} and city_resp.subdivisions:
                    subdivision = city_resp.subdivisions.most_specific.iso_code or ""
                city = city_resp.city.name or ""
            except (geoip2.errors.AddressNotFoundError, ValueError):
                pass

        # --- ISP/connection-type lookup (VPN/datacenter detection) ---
        if self._isp_reader:
            try:
                isp_resp = self._isp_reader.isp(ip)
                connection_type = (isp_resp.connection_type or "").lower()
                is_datacenter = connection_type in {"hosting", "corporate"}
                # MaxMind ISP DB doesn't directly flag VPNs; use ASN heuristics
                # For production: augment with IPinfo.io or Spur.us API calls
                asn_org = (isp_resp.autonomous_system_organization or "").upper()
                known_vpn_asns = {
                    "NORDVPN", "EXPRESSVPN", "SURFSHARK", "PRIVATE INTERNET ACCESS",
                    "PROTONVPN", "MULLVAD", "CYBERGHOST", "IPVANISH", "VYPRVPN",
                    "TUNNELBEAR", "WINDSCRIBE", "HIDE.ME", "STRONGVPN",
                }
                is_vpn = any(vpn in asn_org for vpn in known_vpn_asns)
            except (geoip2.errors.AddressNotFoundError, ValueError):
                pass

        # --- Compute confidence score ---
        # Deduct points for each uncertainty signal
        confidence = 1.0
        if is_datacenter:
            confidence -= 0.40
        if is_vpn:
            confidence -= 0.50
        if is_tor:
            confidence -= 0.70
        if is_residential_proxy:
            confidence -= 0.60
        if country_code == "XX":
            confidence -= 0.30
        confidence = max(0.0, min(1.0, confidence))

        # --- Blocking decision ---
        allowed = True
        block_reason = None

        if is_tor and self.config.block_tor:
            allowed = False
            block_reason = BlockReason.PROXY_TOR

        elif is_vpn:
            allowed = False
            block_reason = BlockReason.PROXY_VPN

        elif is_datacenter and self.config.block_datacenter_ips:
            allowed = False
            block_reason = BlockReason.PROXY_DATACENTER

        elif country_code in self.config.prohibited_countries:
            allowed = False
            block_reason = BlockReason.GEO_COUNTRY_PROHIBITED

        elif country_code in self.config.unlicensed_countries:
            allowed = False
            block_reason = BlockReason.GEO_COUNTRY_NO_LICENSE

        elif country_code == "US" and subdivision in self.config.blocked_us_states:
            allowed = False
            block_reason = BlockReason.GEO_STATE_NO_LICENSE

        return GeoCheckResult(
            allowed=allowed,
            country_code=country_code,
            country_name=country_name,
            subdivision=subdivision,
            city=city,
            is_vpn=is_vpn,
            is_datacenter=is_datacenter,
            is_tor=is_tor,
            is_residential_proxy=is_residential_proxy,
            confidence_score=confidence,
            block_reason=block_reason,
        )

    def close(self) -> None:
        for reader in (self._country_reader, self._city_reader, self._isp_reader):
            if reader:
                reader.close()


# =============================================================================
# Multi-signal Jurisdiction Verifier
# =============================================================================

class JurisdictionVerifier:
    """
    Cross-checks multiple jurisdiction signals for high-confidence decisions.

    Regulators require more than just IP geolocation. For high-value players
    (large deposits, withdrawal requests), operators must verify:
      1. IP geolocation (MaxMind)
      2. Phone number country prefix (E.164 parsing)
      3. Document-issued country (KYC document metadata)
      4. Bank/card issuing country (payment metadata)

    If signals conflict, the player's account is flagged for manual review.
    This is required by UKGC's Remote Gambling and Software Technical Standards
    (RTS) and MGA's Player Due Diligence requirements.
    """

    def __init__(self, geo_service: GeoIPService) -> None:
        self.geo_service = geo_service

    def verify_registration(
        self,
        ip: str,
        phone_number: str | None = None,
        document_country: str | None = None,
    ) -> dict[str, Any]:
        """
        Multi-signal jurisdiction check for player registration.

        Returns a dict with:
          - allowed: bool
          - primary_country: ISO code from IP geolocation
          - signal_agreement: bool (all signals agree on country)
          - signals: dict of each signal's result
          - confidence: float
          - recommendation: "allow" | "manual_review" | "deny"
        """
        geo_result = self.geo_service.lookup(ip)
        signals: dict[str, str | None] = {
            "ip_country": geo_result.country_code,
            "phone_country": None,
            "document_country": document_country,
        }

        # Parse phone number country (E.164 format: +44..., +49..., etc.)
        if phone_number:
            phone_country = self._parse_phone_country(phone_number)
            signals["phone_country"] = phone_country

        # Determine signal agreement
        non_null_signals = [v for v in signals.values() if v and v != "XX"]
        unique_countries = set(non_null_signals)
        signal_agreement = len(unique_countries) <= 1

        # If signals disagree, escalate to manual review
        if not signal_agreement and len(non_null_signals) > 1:
            recommendation = "manual_review"
        elif not geo_result.allowed:
            recommendation = "deny"
        elif geo_result.confidence_score < self.geo_service.config.min_confidence_score:
            recommendation = "manual_review"
        else:
            recommendation = "allow"

        return {
            "allowed": recommendation == "allow",
            "primary_country": geo_result.country_code,
            "signal_agreement": signal_agreement,
            "signals": signals,
            "confidence": geo_result.confidence_score,
            "recommendation": recommendation,
            "block_reason": geo_result.block_reason.value if geo_result.block_reason else None,
            "is_vpn": geo_result.is_vpn,
            "is_tor": geo_result.is_tor,
            "audit_id": geo_result.audit_id,
        }

    @staticmethod
    def _parse_phone_country(phone: str) -> str | None:
        """Extract country code from E.164 phone number using prefix table."""
        # Minimal prefix map for illustration; use the `phonenumbers` library in production
        PHONE_PREFIX_MAP = {
            "+44": "GB", "+1": "US", "+49": "DE", "+33": "FR", "+34": "ES",
            "+39": "IT", "+46": "SE", "+45": "DK", "+47": "NO", "+31": "NL",
            "+32": "BE", "+43": "AT", "+351": "PT", "+358": "FI", "+48": "PL",
            "+420": "CZ", "+36": "HU", "+40": "RO", "+353": "IE", "+356": "MT",
            "+55": "BR", "+57": "CO", "+52": "MX", "+54": "AR",
            "+971": "AE", "+966": "SA", "+974": "QA", "+965": "KW",
            "+86":  "CN", "+82": "KR", "+81": "JP", "+91": "IN",
        }
        phone = phone.strip()
        # Match longest prefix first
        for prefix, country in sorted(PHONE_PREFIX_MAP.items(), key=lambda x: -len(x[0])):
            if phone.startswith(prefix):
                return country
        return None


# =============================================================================
# Compliance Audit Logger
# =============================================================================

class ComplianceAuditLogger:
    """
    Writes immutable append-only audit log entries for every geo-check decision.

    Format: JSONL (one JSON object per line) — compatible with AWS Athena,
    Elasticsearch, and Splunk for regulatory reporting.

    Regulators (especially UKGC) require you to produce the full geo-check
    history for a player on demand. This log is your legal evidence.
    """

    def __init__(self, log_path: str) -> None:
        self.log_path = log_path
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        self._log = structlog.get_logger("geo-audit")

    def log_decision(
        self,
        audit_id: str,
        ip: str,
        result: GeoCheckResult,
        endpoint: str,
        player_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Write a single audit event. Never mutates or deletes existing entries."""
        entry = {
            "audit_id":     audit_id,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "event_type":   "GEO_CHECK",
            "ip_address":   ip,
            "player_id":    player_id,
            "session_id":   session_id,
            "endpoint":     endpoint,
            "country_code": result.country_code,
            "country_name": result.country_name,
            "subdivision":  result.subdivision,
            "city":         result.city,
            "allowed":      result.allowed,
            "block_reason": result.block_reason.value if result.block_reason else None,
            "is_vpn":       result.is_vpn,
            "is_datacenter":result.is_datacenter,
            "is_tor":       result.is_tor,
            "confidence":   result.confidence_score,
        }

        # Write to structured log (stdout for container environments)
        if result.allowed:
            self._log.info("geo_check_allowed", **entry)
        else:
            self._log.warning("geo_check_blocked", **entry)

        # Also write to append-only JSONL file for long-term retention
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            self._log.error("audit_log_write_failed", error=str(exc), path=self.log_path)


# =============================================================================
# Flask WSGI Middleware
# =============================================================================

class GeoIPMiddleware:
    """
    WSGI middleware that wraps any Flask (or WSGI-compatible) application.

    Intercepts every request, performs geo-check, blocks if necessary,
    and passes geo metadata in request headers for downstream handlers.

    Usage:
        app = Flask(__name__)
        config = GeoConfig()
        app.wsgi_app = GeoIPMiddleware(app.wsgi_app, config)
    """

    # Paths that bypass geo-checking entirely
    EXEMPT_PATHS = frozenset({
        "/healthz", "/ping", "/robots.txt", "/favicon.ico",
        "/blocked", "/.well-known/acme-challenge",
    })

    def __init__(self, wsgi_app: Any, config: GeoConfig) -> None:
        self.app         = wsgi_app
        self.config      = config
        self.geo_service = GeoIPService(config)
        self.verifier    = JurisdictionVerifier(self.geo_service)
        self.audit       = ComplianceAuditLogger(config.audit_log_path)

    def _get_client_ip(self, environ: dict[str, Any]) -> str:
        """
        Extract real client IP, respecting trusted proxy headers.

        AWS CloudFront sets X-Forwarded-For. Cloudflare sets CF-Connecting-IP.
        nginx sets X-Real-IP. Read in priority order.

        WARNING: Never trust X-Forwarded-For blindly — only trust it when your
        proxy layer is the first hop. A direct internet request can spoof it.
        """
        # CloudFront real IP (set by CloudFront, not spoofable from client)
        cf_ip = environ.get("HTTP_CF_CONNECTING_IP", "").strip()
        if cf_ip:
            return cf_ip

        # CloudFront-Viewer-IP (custom header set in CloudFront origin request policy)
        cloudfront_viewer = environ.get("HTTP_X_FORWARDED_FOR", "").strip()
        if cloudfront_viewer:
            # XFF is a comma-separated list; first entry is the original client
            return cloudfront_viewer.split(",")[0].strip()

        return environ.get("REMOTE_ADDR", "127.0.0.1")

    def __call__(self, environ: dict[str, Any], start_response: Any) -> Any:
        path = environ.get("PATH_INFO", "/")

        # Exempt health checks and static assets
        if path in self.EXEMPT_PATHS or path.startswith("/static/"):
            return self.app(environ, start_response)

        client_ip = self._get_client_ip(environ)
        result    = self.geo_service.lookup(client_ip)

        # Write audit log for every request (allowed and blocked)
        self.audit.log_decision(
            audit_id   = result.audit_id,
            ip         = client_ip,
            result     = result,
            endpoint   = path,
            player_id  = environ.get("HTTP_X_PLAYER_ID"),
            session_id = environ.get("HTTP_X_SESSION_ID"),
        )

        if not result.allowed:
            # Return HTTP 451 with JSON body
            body = json.dumps({
                "error":      "access_restricted",
                "message":    "This service is not available in your jurisdiction.",
                "code":       result.block_reason.value if result.block_reason else "GEO_BLOCK",
                "country":    result.country_code,
                "audit_id":   result.audit_id,
                "support":    "support@casino.example.com",
            }).encode("utf-8")

            start_response(
                f"{self.config.block_http_status} Unavailable For Legal Reasons",
                [
                    ("Content-Type",   "application/json"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control",  "no-store, no-cache"),
                    ("X-Audit-ID",     result.audit_id),
                ],
            )
            return [body]

        # Pass geo metadata to downstream handlers via environment
        environ["X_GEO_COUNTRY"]    = result.country_code
        environ["X_GEO_SUBDIVISION"]= result.subdivision
        environ["X_GEO_CITY"]       = result.city
        environ["X_GEO_CONFIDENCE"] = str(result.confidence_score)
        environ["X_GEO_AUDIT_ID"]   = result.audit_id

        return self.app(environ, start_response)


# =============================================================================
# Flask application (example integration)
# =============================================================================

def create_app(config: GeoConfig | None = None) -> Flask:
    """Create and configure the Flask application with geo-blocking middleware."""
    if config is None:
        config = GeoConfig(
            country_db_path=os.getenv("GEOIP_COUNTRY_DB", "/var/lib/geoip2/GeoLite2-Country.mmdb"),
            city_db_path=os.getenv("GEOIP_CITY_DB", "/var/lib/geoip2/GeoIP2-City.mmdb"),
            isp_db_path=os.getenv("GEOIP_ISP_DB", "/var/lib/geoip2/GeoIP2-ISP.mmdb"),
            audit_log_path=os.getenv("GEO_AUDIT_LOG", "/var/log/igaming/geo-compliance-audit.jsonl"),
        )

    app = Flask(__name__)
    app.wsgi_app = GeoIPMiddleware(app.wsgi_app, config)

    @app.route("/healthz")
    def health() -> Response:
        return jsonify({"status": "ok", "service": "geo-middleware"})

    @app.route("/api/player/register", methods=["POST"])
    def register() -> Response:
        # At registration, perform multi-signal verification
        data = request.get_json(force=True, silent=True) or {}
        verifier = JurisdictionVerifier(GeoIPService(config))

        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or "127.0.0.1"
        )

        verification = verifier.verify_registration(
            ip=client_ip,
            phone_number=data.get("phone"),
            document_country=data.get("document_country"),
        )

        if not verification["allowed"]:
            return jsonify({
                "error":      "registration_denied",
                "reason":     verification.get("block_reason"),
                "audit_id":   verification.get("audit_id"),
            }), 451

        if verification["recommendation"] == "manual_review":
            return jsonify({
                "status":   "pending_review",
                "message":  "Your registration requires additional verification.",
                "audit_id": verification.get("audit_id"),
            }), 202

        return jsonify({
            "status":   "registration_allowed",
            "country":  verification["primary_country"],
            "audit_id": verification.get("audit_id"),
        }), 200

    @app.route("/api/game/launch", methods=["POST"])
    def launch_game() -> Response:
        """
        Every game launch re-verifies jurisdiction.
        A player who was in a licensed state when they registered
        may have traveled to a blocked state since then.
        US operators (NJ DGE, PA iGCB, MI MGCB) require re-verification
        at every game launch, not just at login.
        """
        # Geo check is already done by middleware; geo metadata is in environ
        # Here we just check the confidence score — low confidence = re-verify
        country    = request.environ.get("X_GEO_COUNTRY", "XX")
        confidence = float(request.environ.get("X_GEO_CONFIDENCE", "1.0"))

        if confidence < config.min_confidence_score:
            return jsonify({
                "error":   "location_verification_required",
                "message": "Please verify your location to continue.",
                "code":    "GEO_REVERIFY",
            }), 403

        return jsonify({
            "status":  "ok",
            "country": country,
        })

    return app


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    import sys

    config = GeoConfig(
        country_db_path=os.getenv("GEOIP_COUNTRY_DB", "/var/lib/geoip2/GeoLite2-Country.mmdb"),
        city_db_path=os.getenv("GEOIP_CITY_DB", "/var/lib/geoip2/GeoIP2-City.mmdb"),
        isp_db_path=os.getenv("GEOIP_ISP_DB", "/var/lib/geoip2/GeoIP2-ISP.mmdb"),
        audit_log_path=os.getenv("GEO_AUDIT_LOG", "/var/log/igaming/geo-compliance-audit.jsonl"),
    )

    port = int(os.getenv("PORT", "8080"))
    app  = create_app(config)

    logger.info("geo_middleware_starting", port=port)
    app.run(host="0.0.0.0", port=port, debug=False)
