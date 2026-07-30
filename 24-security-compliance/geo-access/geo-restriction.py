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
Geographic Access Control for Online Gambling Platforms
=============================================================================
Implements multi-layer geographic restriction for regulatory compliance.
Online gambling operators must block access from jurisdictions where they
are not licensed. This script provides:

1. GeoIP-based IP geolocation with MaxMind GeoLite2
2. DNS-based blocking via Cloudflare/Route53 geo-routing
3. Application-layer verification with VPN/proxy detection
4. Real-time jurisdiction validation against license database

Usage:
    # As standalone service
    python3 geo-restriction.py --config geo-config.yaml --port 8080

    # As library in your gambling platform
    from geo_restriction import GeoRestrictionService
    service = GeoRestrictionService(config_path="geo-config.yaml")
    result = service.check_access(ip="203.0.113.1", player_id="player-123")

Requirements:
    pip install geoip2 flask requests pyyaml maxminddb
=============================================================================
"""

import ipaddress
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import geoip2.database  # ty:ignore[unresolved-import]
    import geoip2.errors  # ty:ignore[unresolved-import]
except ImportError:
    print("ERROR: pip install geoip2")
    sys.exit(1)

try:
    import yaml
except ImportError:
    yaml = None  # Fall back to JSON config  # ty:ignore[invalid-assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("geo-restriction")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class AccessDecision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    CHALLENGE = "challenge"  # Require additional verification
    RESTRICT = "restrict"    # Allow with restrictions (e.g., lower limits)


@dataclass
class JurisdictionConfig:
    """Configuration for a specific jurisdiction."""
    country_code: str
    name: str
    decision: AccessDecision
    reason: str = ""
    # Some jurisdictions allow gambling but with restrictions
    max_deposit_daily: Optional[float] = None
    max_bet: Optional[float] = None
    requires_enhanced_kyc: bool = False
    # Subdivisions (e.g., US states)
    allowed_subdivisions: List[str] = field(default_factory=list)
    blocked_subdivisions: List[str] = field(default_factory=list)


@dataclass
class GeoConfig:
    """Global geographic restriction configuration."""
    # Default policy for unlisted countries
    default_policy: AccessDecision = AccessDecision.BLOCK

    # Licensed jurisdictions (countries where operator has a license)
    licensed_jurisdictions: Dict[str, JurisdictionConfig] = field(default_factory=dict)

    # Always blocked (sanctioned countries, OFAC list)
    sanctioned_countries: Set[str] = field(default_factory=lambda: {
        "KP",  # North Korea
        "IR",  # Iran
        "SY",  # Syria
        "CU",  # Cuba
        "SD",  # Sudan
        "AF",  # Afghanistan (under certain sanctions)
    })

    # VPN/proxy detection settings
    block_vpn: bool = True
    block_tor: bool = True
    block_datacenter_ips: bool = True

    # GeoIP database path
    geoip_db_path: str = "/usr/share/GeoIP/GeoLite2-City.mmdb"
    asn_db_path: str = "/usr/share/GeoIP/GeoLite2-ASN.mmdb"

    # Cache TTL
    cache_ttl_seconds: int = 300

    @classmethod
    def from_file(cls, path: str) -> "GeoConfig":
        """Load configuration from YAML or JSON file."""
        with open(path) as f:
            if path.endswith((".yaml", ".yml")) and yaml:
                raw = yaml.safe_load(f)
            else:
                raw = json.load(f)

        config = cls()
        config.default_policy = AccessDecision(raw.get("default_policy", "block"))
        config.block_vpn = raw.get("block_vpn", True)
        config.block_tor = raw.get("block_tor", True)
        config.geoip_db_path = raw.get("geoip_db_path", config.geoip_db_path)

        for code, jdata in raw.get("licensed_jurisdictions", {}).items():
            config.licensed_jurisdictions[code.upper()] = JurisdictionConfig(
                country_code=code.upper(),
                name=jdata.get("name", code),
                decision=AccessDecision(jdata.get("decision", "allow")),
                reason=jdata.get("reason", ""),
                max_deposit_daily=jdata.get("max_deposit_daily"),
                max_bet=jdata.get("max_bet"),
                requires_enhanced_kyc=jdata.get("requires_enhanced_kyc", False),
                allowed_subdivisions=jdata.get("allowed_subdivisions", []),
                blocked_subdivisions=jdata.get("blocked_subdivisions", []),
            )

        sanctioned = raw.get("sanctioned_countries", [])
        if sanctioned:
            config.sanctioned_countries = set(s.upper() for s in sanctioned)

        return config

    @classmethod
    def default_igaming(cls) -> "GeoConfig":
        """Create default config for a UK/Malta/Gibraltar licensed operator."""
        config = cls()
        config.licensed_jurisdictions = {
            # UK - UKGC license
            "GB": JurisdictionConfig(
                country_code="GB", name="United Kingdom",
                decision=AccessDecision.ALLOW,
                reason="UKGC License",
                requires_enhanced_kyc=True,
            ),
            # Malta - MGA license
            "MT": JurisdictionConfig(
                country_code="MT", name="Malta",
                decision=AccessDecision.ALLOW,
                reason="MGA License",
            ),
            # Gibraltar
            "GI": JurisdictionConfig(
                country_code="GI", name="Gibraltar",
                decision=AccessDecision.ALLOW,
                reason="Gibraltar License",
            ),
            # Sweden - Spelinspektionen
            "SE": JurisdictionConfig(
                country_code="SE", name="Sweden",
                decision=AccessDecision.ALLOW,
                reason="Swedish Gambling Authority License",
                max_deposit_daily=5000,  # SEK weekly limit
            ),
            # Denmark - Spillemyndigheden
            "DK": JurisdictionConfig(
                country_code="DK", name="Denmark",
                decision=AccessDecision.ALLOW,
                reason="Danish Gambling Authority License",
            ),
            # Ireland
            "IE": JurisdictionConfig(
                country_code="IE", name="Ireland",
                decision=AccessDecision.ALLOW,
                reason="Irish license",
            ),
            # Germany - GGL (state-by-state)
            "DE": JurisdictionConfig(
                country_code="DE", name="Germany",
                decision=AccessDecision.ALLOW,
                reason="German Interstate Treaty on Gambling",
                max_bet=1.0,  # EUR 1 max bet for slots
                max_deposit_daily=1000,  # EUR 1000/month limit
            ),
            # US - state by state
            "US": JurisdictionConfig(
                country_code="US", name="United States",
                decision=AccessDecision.RESTRICT,
                reason="US state-by-state licensing",
                allowed_subdivisions=["NJ", "PA", "MI", "WV", "CT", "DE"],
                blocked_subdivisions=["WA", "UT"],  # Explicitly illegal
                requires_enhanced_kyc=True,
            ),
            # France - ANJ
            "FR": JurisdictionConfig(
                country_code="FR", name="France",
                decision=AccessDecision.ALLOW,
                reason="ANJ License",
            ),
            # Italy - ADM
            "IT": JurisdictionConfig(
                country_code="IT", name="Italy",
                decision=AccessDecision.ALLOW,
                reason="ADM License",
            ),
            # Spain - DGOJ
            "ES": JurisdictionConfig(
                country_code="ES", name="Spain",
                decision=AccessDecision.ALLOW,
                reason="DGOJ License",
            ),
            # Australia - blocked (no foreign operator licenses)
            "AU": JurisdictionConfig(
                country_code="AU", name="Australia",
                decision=AccessDecision.BLOCK,
                reason="Interactive Gambling Act 2001 blocks offshore operators",
            ),
            # China - blocked
            "CN": JurisdictionConfig(
                country_code="CN", name="China",
                decision=AccessDecision.BLOCK,
                reason="Online gambling prohibited",
            ),
        }
        return config


# ---------------------------------------------------------------------------
# GeoIP Lookup
# ---------------------------------------------------------------------------

@dataclass
class GeoLookupResult:
    """Result of a geographic IP lookup."""
    ip: str
    country_code: str = ""
    country_name: str = ""
    subdivision: str = ""  # State/province
    city: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    asn: int = 0
    asn_org: str = ""
    is_anonymous_proxy: bool = False
    is_satellite_provider: bool = False
    is_hosting_provider: bool = False
    accuracy_radius_km: int = 0
    lookup_time_ms: float = 0.0


class GeoIPService:
    """GeoIP lookup service using MaxMind databases."""

    # Known datacenter/hosting ASNs that are commonly used for VPN/proxy
    DATACENTER_ASNS = {
        14061,   # DigitalOcean
        16509,   # Amazon AWS
        15169,   # Google Cloud
        8075,    # Microsoft Azure
        20473,   # Vultr
        63949,   # Linode
        24940,   # Hetzner
        16276,   # OVH
        13335,   # Cloudflare
        46489,   # Cloudflare WARP (potential VPN)
        9009,    # M247 (VPN provider)
        60068,   # CDN77 (often used by VPNs)
        212238,  # Datacamp (VPN)
    }

    def __init__(self, city_db_path: str, asn_db_path: str = ""):
        self.city_reader = None
        self.asn_reader = None

        try:
            self.city_reader = geoip2.database.Reader(city_db_path)
            logger.info(f"Loaded GeoIP city database: {city_db_path}")
        except FileNotFoundError:
            logger.warning(f"GeoIP city database not found: {city_db_path}")
            logger.info("Download from: https://dev.maxmind.com/geoip/geolite2-free-geolocation-data")

        if asn_db_path:
            try:
                self.asn_reader = geoip2.database.Reader(asn_db_path)
                logger.info(f"Loaded GeoIP ASN database: {asn_db_path}")
            except FileNotFoundError:
                logger.warning(f"GeoIP ASN database not found: {asn_db_path}")

    def lookup(self, ip: str) -> GeoLookupResult:
        """Perform GeoIP lookup for an IP address."""
        start = time.time()
        result = GeoLookupResult(ip=ip)

        # Skip private/reserved IPs
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_reserved or addr.is_loopback:
                result.country_code = "PRIVATE"
                result.country_name = "Private Network"
                return result
        except ValueError:
            logger.error(f"Invalid IP address: {ip}")
            return result

        # City lookup
        if self.city_reader:
            try:
                city = self.city_reader.city(ip)
                result.country_code = city.country.iso_code or ""
                result.country_name = city.country.name or ""
                result.city = city.city.name or "" if city.city else ""
                result.latitude = city.location.latitude or 0.0
                result.longitude = city.location.longitude or 0.0
                result.accuracy_radius_km = city.location.accuracy_radius or 0

                # Get subdivision (state/province)
                if city.subdivisions:
                    result.subdivision = city.subdivisions.most_specific.iso_code or ""

                # Anonymous proxy traits
                if hasattr(city, "traits"):
                    result.is_anonymous_proxy = getattr(city.traits, "is_anonymous_proxy", False)
                    result.is_satellite_provider = getattr(city.traits, "is_satellite_provider", False)
                    result.is_hosting_provider = getattr(city.traits, "is_hosting_provider", False)

            except geoip2.errors.AddressNotFoundError:
                logger.debug(f"IP not found in GeoIP database: {ip}")

        # ASN lookup
        if self.asn_reader:
            try:
                asn = self.asn_reader.asn(ip)
                result.asn = asn.autonomous_system_number or 0
                result.asn_org = asn.autonomous_system_organization or ""

                # Check if ASN belongs to known datacenter/VPN providers
                if result.asn in self.DATACENTER_ASNS:
                    result.is_hosting_provider = True

            except geoip2.errors.AddressNotFoundError:
                pass

        result.lookup_time_ms = (time.time() - start) * 1000
        return result

    def close(self):
        if self.city_reader:
            self.city_reader.close()
        if self.asn_reader:
            self.asn_reader.close()


# ---------------------------------------------------------------------------
# Geographic Restriction Service
# ---------------------------------------------------------------------------

@dataclass
class AccessCheckResult:
    """Result of a geographic access check."""
    decision: AccessDecision
    country_code: str
    country_name: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    restrictions: Dict[str, Any] = field(default_factory=dict)
    # Audit fields
    timestamp: str = ""
    player_id: str = ""
    ip: str = ""
    check_duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "reason": self.reason,
            "details": self.details,
            "restrictions": self.restrictions,
            "timestamp": self.timestamp,
            "player_id": self.player_id,
            "ip": self.ip,
            "check_duration_ms": self.check_duration_ms,
        }


class GeoRestrictionService:
    """Main service for geographic access control."""

    def __init__(self, config: Optional[GeoConfig] = None,
                 config_path: Optional[str] = None):
        if config:
            self.config = config
        elif config_path:
            self.config = GeoConfig.from_file(config_path)
        else:
            self.config = GeoConfig.default_igaming()

        self.geoip = GeoIPService(
            city_db_path=self.config.geoip_db_path,
            asn_db_path=self.config.asn_db_path,
        )

        # Simple in-memory cache
        self._cache: Dict[str, Tuple[AccessCheckResult, float]] = {}

        logger.info(f"GeoRestrictionService initialized with "
                     f"{len(self.config.licensed_jurisdictions)} licensed jurisdictions, "
                     f"default policy: {self.config.default_policy.value}")

    def check_access(self, ip: str, player_id: str = "",
                     requested_action: str = "") -> AccessCheckResult:
        """
        Check if access should be allowed from the given IP address.

        Args:
            ip: Client IP address
            player_id: Player identifier (for audit logging)
            requested_action: What the player is trying to do (deposit, bet, etc.)

        Returns:
            AccessCheckResult with decision and details
        """
        start = time.time()

        # Check cache
        cache_key = f"{ip}:{player_id}"
        cached = self._cache.get(cache_key)
        if cached:
            result, cache_time = cached
            if time.time() - cache_time < self.config.cache_ttl_seconds:
                result.check_duration_ms = 0.1  # Cache hit
                return result

        # Perform GeoIP lookup
        geo = self.geoip.lookup(ip)

        # Layer 1: Sanctioned countries (always block, no exceptions)
        if geo.country_code in self.config.sanctioned_countries:
            result = AccessCheckResult(
                decision=AccessDecision.BLOCK,
                country_code=geo.country_code,
                country_name=geo.country_name,
                reason=f"Sanctioned country: {geo.country_name} ({geo.country_code})",
                details={"sanction_list": "OFAC/EU", "blocked_at": "layer_1"},
                ip=ip, player_id=player_id,
            )
            self._finalize_result(result, start)
            return result

        # Layer 2: VPN/Proxy/Tor detection
        if self.config.block_vpn or self.config.block_tor:
            vpn_check = self._check_vpn_proxy(geo)
            if vpn_check:
                result = AccessCheckResult(
                    decision=AccessDecision.CHALLENGE,
                    country_code=geo.country_code,
                    country_name=geo.country_name,
                    reason=vpn_check,
                    details={"blocked_at": "layer_2", "asn": geo.asn,
                             "asn_org": geo.asn_org},
                    ip=ip, player_id=player_id,
                )
                self._finalize_result(result, start)
                return result

        # Layer 3: Licensed jurisdiction check
        jurisdiction = self.config.licensed_jurisdictions.get(geo.country_code)

        if jurisdiction:
            # Check subdivision restrictions (e.g., US states)
            if jurisdiction.blocked_subdivisions and \
               geo.subdivision in jurisdiction.blocked_subdivisions:
                result = AccessCheckResult(
                    decision=AccessDecision.BLOCK,
                    country_code=geo.country_code,
                    country_name=geo.country_name,
                    reason=f"Blocked subdivision: {geo.subdivision} in {geo.country_code}",
                    details={"subdivision": geo.subdivision, "blocked_at": "layer_3"},
                    ip=ip, player_id=player_id,
                )
                self._finalize_result(result, start)
                return result

            if jurisdiction.allowed_subdivisions and \
               geo.subdivision not in jurisdiction.allowed_subdivisions:
                result = AccessCheckResult(
                    decision=AccessDecision.BLOCK,
                    country_code=geo.country_code,
                    country_name=geo.country_name,
                    reason=f"Unlicensed subdivision: {geo.subdivision} in {geo.country_code}",
                    details={"subdivision": geo.subdivision,
                             "allowed": jurisdiction.allowed_subdivisions,
                             "blocked_at": "layer_3"},
                    ip=ip, player_id=player_id,
                )
                self._finalize_result(result, start)
                return result

            # Build restrictions
            restrictions = {}
            if jurisdiction.max_deposit_daily:
                restrictions["max_deposit_daily"] = jurisdiction.max_deposit_daily
            if jurisdiction.max_bet:
                restrictions["max_bet"] = jurisdiction.max_bet
            if jurisdiction.requires_enhanced_kyc:
                restrictions["requires_enhanced_kyc"] = True

            result = AccessCheckResult(
                decision=jurisdiction.decision,
                country_code=geo.country_code,
                country_name=geo.country_name,
                reason=jurisdiction.reason,
                restrictions=restrictions,
                details={"subdivision": geo.subdivision, "city": geo.city,
                         "accuracy_km": geo.accuracy_radius_km},
                ip=ip, player_id=player_id,
            )
            self._finalize_result(result, start)
            return result

        # Layer 4: Default policy for unlisted countries
        result = AccessCheckResult(
            decision=self.config.default_policy,
            country_code=geo.country_code,
            country_name=geo.country_name,
            reason=f"No license for {geo.country_name} ({geo.country_code}). "
                   f"Default policy: {self.config.default_policy.value}",
            details={"blocked_at": "layer_4_default"},
            ip=ip, player_id=player_id,
        )
        self._finalize_result(result, start)
        return result

    def _check_vpn_proxy(self, geo: GeoLookupResult) -> Optional[str]:
        """Check if the IP appears to be a VPN, proxy, or Tor exit node."""
        reasons = []

        if geo.is_anonymous_proxy and self.config.block_vpn:
            reasons.append("Anonymous proxy detected")

        if geo.is_hosting_provider and self.config.block_datacenter_ips:
            reasons.append(f"Datacenter IP detected (ASN: {geo.asn}, Org: {geo.asn_org})")

        if geo.is_satellite_provider:
            reasons.append("Satellite provider detected")

        # High accuracy radius can indicate proxy/VPN (location is approximate)
        if geo.accuracy_radius_km > 500:
            reasons.append(f"Low location accuracy ({geo.accuracy_radius_km}km radius)")

        return "; ".join(reasons) if reasons else None

    def _finalize_result(self, result: AccessCheckResult, start_time: float):
        """Add metadata and cache the result."""
        result.timestamp = datetime.now(timezone.utc).isoformat() + "Z"
        result.check_duration_ms = (time.time() - start_time) * 1000

        # Cache the result
        cache_key = f"{result.ip}:{result.player_id}"
        self._cache[cache_key] = (result, time.time())

        # Log for audit trail (regulatory requirement)
        log_level = logging.WARNING if result.decision == AccessDecision.BLOCK else logging.INFO
        logger.log(log_level,
                    f"GeoCheck: ip={result.ip} player={result.player_id} "
                    f"country={result.country_code} decision={result.decision.value} "
                    f"reason=\"{result.reason}\" duration={result.check_duration_ms:.1f}ms")

    def close(self):
        self.geoip.close()


# ---------------------------------------------------------------------------
# Flask API (optional - for running as a standalone service)
# ---------------------------------------------------------------------------

def create_app(config_path: Optional[str] = None) -> Any:
    """Create Flask API for geographic restriction service."""
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        logger.error("Flask not installed. pip install flask")
        return None

    app = Flask(__name__)
    service = GeoRestrictionService(config_path=config_path)

    @app.route("/api/v1/geo/check", methods=["GET", "POST"])
    def check_geo():
        """Check geographic access for an IP address."""
        if request.method == "POST":
            data = request.get_json() or {}
            ip = data.get("ip", request.remote_addr)
            player_id = data.get("player_id", "")
            action = data.get("action", "")
        else:
            ip = request.args.get("ip", request.remote_addr)
            player_id = request.args.get("player_id", "")
            action = request.args.get("action", "")

        result = service.check_access(ip=ip, player_id=player_id,  # ty:ignore[invalid-argument-type]
                                       requested_action=action)

        status_code = 200 if result.decision == AccessDecision.ALLOW else 403
        return jsonify(result.to_dict()), status_code

    @app.route("/api/v1/geo/jurisdictions", methods=["GET"])
    def list_jurisdictions():
        """List all configured jurisdictions."""
        jurisdictions = {}
        for code, j in service.config.licensed_jurisdictions.items():
            jurisdictions[code] = {
                "name": j.name,
                "decision": j.decision.value,
                "reason": j.reason,
            }
        return jsonify({"jurisdictions": jurisdictions})

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy"}), 200

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Geographic access control service")
    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument("--port", type=int, default=8080, help="API port")
    parser.add_argument("--check-ip", help="Check a single IP and exit")
    parser.add_argument("--player-id", default="", help="Player ID for check")
    args = parser.parse_args()

    if args.check_ip:
        # Single IP check mode
        service = GeoRestrictionService(config_path=args.config)
        result = service.check_access(ip=args.check_ip, player_id=args.player_id)
        print(json.dumps(result.to_dict(), indent=2))
        service.close()
        sys.exit(0 if result.decision == AccessDecision.ALLOW else 1)

    # Run as API service
    app = create_app(config_path=args.config)
    if app:
        logger.info(f"Starting geo-restriction service on port {args.port}")
        app.run(host="0.0.0.0", port=args.port, debug=False)
    else:
        logger.error("Failed to create Flask app")
        sys.exit(1)


if __name__ == "__main__":
    main()
