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
Platform-Grade Location Verification Service for Online Casino Compliance.

Multi-signal geolocation with:
  - IP geolocation (MaxMind GeoIP2)
  - GPS validation for mobile clients
  - VPN/proxy/datacenter detection
  - State-level precision for US jurisdictions (NJ, PA, MI borders)
  - Cloudflare edge geo vs server-side geo comparison
  - Periodic re-verification during active sessions
  - Full audit trail for regulatory evidence

GLI-19 Section 2.5.1 requires location verification within 500m of
jurisdiction boundaries. US state regulations (NJ NJAC 13:69O, PA
PGCB Technical Standards) require continuous re-verification.

Script reference for Chapter 24e.
"""

import hashlib
import json
import logging
import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------

class GeoVerdict(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED_JURISDICTION = "DENIED_JURISDICTION"
    DENIED_VPN = "DENIED_VPN"
    DENIED_PROXY = "DENIED_PROXY"
    DENIED_DATACENTER = "DENIED_DATACENTER"
    DENIED_SPOOFING = "DENIED_SPOOFING"
    DENIED_MISMATCH = "DENIED_MISMATCH"
    DENIED_INSUFFICIENT = "DENIED_INSUFFICIENT_DATA"
    DENIED_EXCLUSION_ZONE = "DENIED_EXCLUSION_ZONE"
    REQUIRES_REVERIFICATION = "REQUIRES_REVERIFICATION"


class SignalSource(str, Enum):
    GPS = "GPS"
    IP = "IP"
    WIFI = "WIFI"
    CELL = "CELL"
    CF_EDGE = "CF_EDGE"  # Cloudflare edge header


# Earth radius in km for haversine
EARTH_RADIUS_KM = 6371.0

# Maximum acceptable discrepancy between IP and GPS (km)
MAX_IP_GPS_DISCREPANCY_KM = 100.0

# GLI-19 boundary precision requirement (km)
GLI19_BOUNDARY_BUFFER_KM = 0.5


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GeoSignal:
    """A single geolocation signal from any source."""
    source: str = SignalSource.IP.value
    latitude: float = 0.0
    longitude: float = 0.0
    accuracy_meters: float = 0.0
    country_code: str = ""
    region_code: str = ""  # state/province
    city: str = ""
    isp: str = ""
    is_vpn: bool = False
    is_proxy: bool = False
    is_datacenter: bool = False
    is_tor: bool = False
    timestamp: float = field(default_factory=time.time)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Result of a geolocation verification check."""
    check_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    player_id: str = ""
    session_id: str = ""
    verdict: str = GeoVerdict.DENIED_INSUFFICIENT.value
    jurisdiction: str = ""
    confidence: float = 0.0
    signals_used: list[str] = field(default_factory=list)
    ip_country: str = ""
    ip_region: str = ""
    gps_country: str = ""
    gps_region: str = ""
    cf_country: str = ""
    vpn_detected: bool = False
    proxy_detected: bool = False
    spoofing_score: float = 0.0
    distance_ip_gps_km: float = 0.0
    message: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class JurisdictionConfig:
    """Configuration for a licensed jurisdiction."""
    code: str = ""
    name: str = ""
    country: str = ""
    region: str = ""  # empty for country-level
    boundary_buffer_km: float = GLI19_BOUNDARY_BUFFER_KM
    reverify_interval_seconds: int = 1800  # 30 minutes default
    min_age: int = 18
    currency: str = "USD"
    allowed_game_types: list[str] = field(default_factory=list)
    blocked: bool = False
    requires_gps: bool = False  # US states require GPS on mobile


# ---------------------------------------------------------------------------
# Jurisdiction matrix
# ---------------------------------------------------------------------------

JURISDICTION_MATRIX: dict[str, JurisdictionConfig] = {
    "US-NJ": JurisdictionConfig(
        code="US-NJ", name="New Jersey", country="US", region="NJ",
        boundary_buffer_km=0.5, reverify_interval_seconds=1800,
        min_age=21, currency="USD",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
        requires_gps=True,
    ),
    "US-PA": JurisdictionConfig(
        code="US-PA", name="Pennsylvania", country="US", region="PA",
        boundary_buffer_km=0.5, reverify_interval_seconds=1800,
        min_age=21, currency="USD",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
        requires_gps=True,
    ),
    "US-MI": JurisdictionConfig(
        code="US-MI", name="Michigan", country="US", region="MI",
        boundary_buffer_km=0.5, reverify_interval_seconds=1800,
        min_age=21, currency="USD",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
        requires_gps=True,
    ),
    "GB": JurisdictionConfig(
        code="GB", name="United Kingdom", country="GB", region="",
        boundary_buffer_km=0, reverify_interval_seconds=3600,
        min_age=18, currency="GBP",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
    ),
    "MT": JurisdictionConfig(
        code="MT", name="Malta", country="MT", region="",
        boundary_buffer_km=0, reverify_interval_seconds=7200,
        min_age=18, currency="EUR",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
    ),
    "SE": JurisdictionConfig(
        code="SE", name="Sweden", country="SE", region="",
        boundary_buffer_km=0, reverify_interval_seconds=3600,
        min_age=18, currency="SEK",
        allowed_game_types=["slots", "table_games", "live_casino"],
    ),
    "DK": JurisdictionConfig(
        code="DK", name="Denmark", country="DK", region="",
        boundary_buffer_km=0, reverify_interval_seconds=3600,
        min_age=18, currency="DKK",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
    ),
    "BR": JurisdictionConfig(
        code="BR", name="Brazil", country="BR", region="",
        boundary_buffer_km=0, reverify_interval_seconds=3600,
        min_age=18, currency="BRL",
        allowed_game_types=["slots", "table_games", "live_casino", "sports"],
    ),
}

# Countries blocked globally (sanctions, no-license)
BLOCKED_COUNTRIES: set[str] = {
    "KP", "IR", "SY", "CU", "AF", "IQ", "LY", "SD", "YE",
    "US",  # US blocked at country level; individual states whitelisted
}

# Exclusion zones (lat, lon, radius_km, jurisdiction, reason)
EXCLUSION_ZONES: list[dict[str, Any]] = [
    {
        "name": "Atlantic City Land Casino Zone",
        "lat": 39.3643, "lon": -74.4229,
        "radius_km": 0.1, "jurisdiction": "US-NJ",
        "reason": "Physical casino proximity restriction",
    },
]


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# IP intelligence adapter (ABC + implementations)
# ---------------------------------------------------------------------------

class IPIntelligenceAdapter(ABC):
    """
    Abstract interface for IP geolocation and threat-intelligence lookups.

    Implementations:
      - StubIPIntelligence: deterministic results from a fixture dict (tests)
      - MaxMindIPIntelligence: real lookups via GeoIP2 Insights API / local MMDB
    """

    @abstractmethod
    def lookup(self, ip_address: str) -> GeoSignal:
        """Return a GeoSignal for the given IPv4/IPv6 address."""


class StubIPIntelligence(IPIntelligenceAdapter):
    """
    Deterministic IP intelligence backed by an in-memory fixture map.
    Use ``set_override`` or load fixtures from JSON to populate responses.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, GeoSignal] = {}

    def set_override(self, ip: str, signal: GeoSignal) -> None:
        """Register a deterministic lookup result for *ip*."""
        self._overrides[ip] = signal

    def load_fixtures(self, fixtures: dict[str, dict[str, Any]]) -> None:
        """
        Bulk-load fixtures from a dict mapping IP -> GeoSignal fields.
        Typically loaded from a JSON file in the fixtures/ directory.
        """
        for ip, fields in fixtures.items():
            self._overrides[ip] = GeoSignal(**fields)

    def lookup(self, ip_address: str) -> GeoSignal:
        if ip_address in self._overrides:
            return self._overrides[ip_address]
        return GeoSignal(source=SignalSource.IP.value, country_code="", region_code="")


class MaxMindIPIntelligence(IPIntelligenceAdapter):
    """
    Production implementation using MaxMind GeoIP2 Insights API.

    Requires ``geoip2`` package and valid MaxMind credentials.
    Falls back to local MMDB when ``mmdb_path`` is provided.
    """

    def __init__(
        self,
        account_id: str = "",
        license_key: str = "",
        mmdb_path: str = "",
    ) -> None:
        self.account_id = account_id
        self.license_key = license_key
        self.mmdb_path = mmdb_path

    def lookup(self, ip_address: str) -> GeoSignal:
        """
        Call MaxMind Insights (or read local MMDB) and return a GeoSignal.

        Production usage::

            import geoip2.webservice
            client = geoip2.webservice.Client(self.account_id, self.license_key)
            resp = client.insights(ip_address)
            return GeoSignal(
                source=SignalSource.IP.value,
                latitude=resp.location.latitude or 0.0,
                longitude=resp.location.longitude or 0.0,
                accuracy_meters=(resp.location.accuracy_radius or 0) * 1000,
                country_code=resp.country.iso_code or "",
                region_code=(resp.subdivisions.most_specific.iso_code
                             if resp.subdivisions else ""),
                is_vpn=getattr(resp.traits, "is_anonymous_vpn", False),
                is_proxy=getattr(resp.traits, "is_anonymous_proxy", False),
                is_datacenter=getattr(resp.traits, "is_hosting_provider", False),
                is_tor=getattr(resp.traits, "is_tor_exit_node", False),
                isp=getattr(resp.traits, "isp", ""),
            )
        """
        raise NotImplementedError(
            "MaxMindIPIntelligence requires geoip2 package and valid credentials. "
            "Use StubIPIntelligence for tests or provide geoip2 at runtime."
        )


# Backward-compatible alias so existing code keeps working.
IPIntelligenceProvider = StubIPIntelligence


# ---------------------------------------------------------------------------
# Geofence Verification Service
# ---------------------------------------------------------------------------

class GeofenceService:
    """
    Multi-signal location verification for online casino compliance.

    Verification flow:
    1. Collect IP signal (always available)
    2. Collect GPS signal (mobile, if available)
    3. Collect Cloudflare edge signal (from CF-IPCountry header)
    4. Run VPN/proxy/datacenter detection
    5. Cross-reference signals for consistency
    6. Check against jurisdiction matrix
    7. Check exclusion zones
    8. Return verdict with confidence score
    """

    def __init__(
        self,
        ip_provider: Optional[IPIntelligenceAdapter] = None,
        redis_client: Any = None,
    ):
        self.ip_provider: IPIntelligenceAdapter = ip_provider or StubIPIntelligence()
        self.redis = redis_client
        self._audit_log: list[dict[str, Any]] = []

    # -- Main verification endpoint --

    def verify_location(
        self,
        player_id: str,
        session_id: str,
        ip_address: str,
        gps_signal: Optional[GeoSignal] = None,
        cf_country: str = "",
        user_agent: str = "",
    ) -> VerificationResult:
        """
        Perform full multi-signal location verification.
        Returns VerificationResult with verdict and audit details.
        """
        result = VerificationResult(
            player_id=player_id,
            session_id=session_id,
        )

        # Step 1: IP intelligence
        ip_signal = self.ip_provider.lookup(ip_address)
        result.ip_country = ip_signal.country_code
        result.ip_region = ip_signal.region_code
        result.signals_used.append("IP")

        # Step 2: VPN/proxy/datacenter detection
        if ip_signal.is_vpn:
            result.verdict = GeoVerdict.DENIED_VPN.value
            result.vpn_detected = True
            result.message = "VPN detected"
            self._log_check(result)
            return result

        if ip_signal.is_proxy:
            result.verdict = GeoVerdict.DENIED_PROXY.value
            result.proxy_detected = True
            result.message = "Anonymous proxy detected"
            self._log_check(result)
            return result

        if ip_signal.is_datacenter:
            result.verdict = GeoVerdict.DENIED_DATACENTER.value
            result.message = "Datacenter/hosting IP detected"
            self._log_check(result)
            return result

        # Step 3: Cloudflare edge signal
        if cf_country:
            result.cf_country = cf_country
            result.signals_used.append("CF_EDGE")

        # Step 4: GPS signal
        if gps_signal:
            result.gps_country = gps_signal.country_code
            result.gps_region = gps_signal.region_code
            result.signals_used.append("GPS")

        # Step 5: Cross-reference signals
        spoofing_score = self._compute_spoofing_score(
            ip_signal, gps_signal, cf_country,
        )
        result.spoofing_score = spoofing_score

        if spoofing_score > 0.7:
            result.verdict = GeoVerdict.DENIED_SPOOFING.value
            result.message = f"Location spoofing detected (score={spoofing_score:.2f})"
            self._log_check(result)
            return result

        # Step 6: Determine jurisdiction
        jurisdiction = self._resolve_jurisdiction(ip_signal, gps_signal, cf_country)

        if not jurisdiction:
            # Check if the country is explicitly blocked
            effective_country = (
                gps_signal.country_code if gps_signal and gps_signal.country_code
                else ip_signal.country_code
            )
            if effective_country in BLOCKED_COUNTRIES:
                result.verdict = GeoVerdict.DENIED_JURISDICTION.value
                result.message = f"Country {effective_country} is blocked"
            else:
                result.verdict = GeoVerdict.DENIED_JURISDICTION.value
                result.message = "No licensed jurisdiction matched"
            self._log_check(result)
            return result

        result.jurisdiction = jurisdiction.code

        # Step 7: US states require GPS on mobile
        if jurisdiction.requires_gps and not gps_signal:
            if self._is_mobile(user_agent):
                result.verdict = GeoVerdict.DENIED_INSUFFICIENT.value
                result.message = (
                    f"{jurisdiction.code} requires GPS verification on mobile"
                )
                self._log_check(result)
                return result

        # Step 8: IP-GPS distance check for US states
        if gps_signal and gps_signal.latitude and ip_signal.latitude:
            dist = haversine_km(
                ip_signal.latitude, ip_signal.longitude,
                gps_signal.latitude, gps_signal.longitude,
            )
            result.distance_ip_gps_km = dist
            if dist > MAX_IP_GPS_DISCREPANCY_KM:
                result.verdict = GeoVerdict.DENIED_MISMATCH.value
                result.message = f"IP-GPS discrepancy: {dist:.1f}km"
                self._log_check(result)
                return result

        # Step 9: Exclusion zone check
        exclusion = self._check_exclusion_zones(
            gps_signal if gps_signal else ip_signal, jurisdiction.code,
        )
        if exclusion:
            result.verdict = GeoVerdict.DENIED_EXCLUSION_ZONE.value
            result.message = f"Inside exclusion zone: {exclusion}"
            self._log_check(result)
            return result

        # Step 10: Compute confidence
        confidence = self._compute_confidence(
            ip_signal, gps_signal, cf_country, jurisdiction,
        )
        result.confidence = confidence

        if confidence < 0.5:
            result.verdict = GeoVerdict.DENIED_INSUFFICIENT.value
            result.message = f"Confidence too low: {confidence:.2f}"
        else:
            result.verdict = GeoVerdict.ALLOWED.value
            result.message = f"Verified in {jurisdiction.name}"

        self._log_check(result)
        return result

    # -- Session re-verification --

    def needs_reverification(
        self, session_id: str, jurisdiction_code: str, last_check_time: float,
    ) -> bool:
        """
        Check if a session needs re-verification based on the
        jurisdiction's reverify interval.
        """
        config = JURISDICTION_MATRIX.get(jurisdiction_code)
        if not config:
            return True
        elapsed = time.time() - last_check_time
        return elapsed >= config.reverify_interval_seconds

    # -- Jurisdiction matrix queries --

    def get_jurisdiction_config(
        self, code: str
    ) -> Optional[JurisdictionConfig]:
        return JURISDICTION_MATRIX.get(code)

    def list_allowed_jurisdictions(self) -> list[JurisdictionConfig]:
        return [j for j in JURISDICTION_MATRIX.values() if not j.blocked]

    def is_country_blocked(self, country_code: str) -> bool:
        return country_code in BLOCKED_COUNTRIES

    # -- Audit log --

    def get_audit_log(
        self, player_id: Optional[str] = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        results = []
        for entry in reversed(self._audit_log):
            if player_id and entry.get("player_id") != player_id:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    # -- Internal helpers --

    def _resolve_jurisdiction(
        self,
        ip_signal: GeoSignal,
        gps_signal: Optional[GeoSignal],
        cf_country: str,
    ) -> Optional[JurisdictionConfig]:
        """
        Determine which licensed jurisdiction the player is in.
        US states need region-level matching; other countries match at country level.
        """
        # Prioritize GPS for US states
        if gps_signal and gps_signal.country_code == "US" and gps_signal.region_code:
            key = f"US-{gps_signal.region_code}"
            config = JURISDICTION_MATRIX.get(key)
            if config and not config.blocked:
                return config

        # IP-based matching
        if ip_signal.country_code == "US" and ip_signal.region_code:
            key = f"US-{ip_signal.region_code}"
            config = JURISDICTION_MATRIX.get(key)
            if config and not config.blocked:
                return config

        # Country-level matching
        effective_country = ip_signal.country_code
        if cf_country and not effective_country:
            effective_country = cf_country

        config = JURISDICTION_MATRIX.get(effective_country)
        if config and not config.blocked:
            return config

        return None

    def _compute_spoofing_score(
        self,
        ip_signal: GeoSignal,
        gps_signal: Optional[GeoSignal],
        cf_country: str,
    ) -> float:
        """
        Compute a 0-1 spoofing probability based on signal consistency.
        Higher score = more likely spoofing.
        """
        score = 0.0
        checks = 0

        # IP vs CF country mismatch
        if cf_country and ip_signal.country_code:
            checks += 1
            if cf_country != ip_signal.country_code:
                score += 0.4

        # IP vs GPS country mismatch
        if gps_signal and gps_signal.country_code and ip_signal.country_code:
            checks += 1
            if gps_signal.country_code != ip_signal.country_code:
                score += 0.5

        # GPS accuracy too perfect (likely spoofed)
        if gps_signal and gps_signal.accuracy_meters > 0:
            checks += 1
            if gps_signal.accuracy_meters < 1.0:
                score += 0.3  # sub-meter accuracy is suspicious

        # Tor exit node
        if ip_signal.is_tor:
            score += 0.8

        if checks == 0:
            return 0.0
        return min(score, 1.0)

    def _compute_confidence(
        self,
        ip_signal: GeoSignal,
        gps_signal: Optional[GeoSignal],
        cf_country: str,
        jurisdiction: JurisdictionConfig,
    ) -> float:
        """
        Compute confidence in the jurisdiction match (0-1).
        More corroborating signals = higher confidence.
        """
        confidence = 0.0

        # IP matches jurisdiction
        if jurisdiction.region:
            if ip_signal.region_code == jurisdiction.region:
                confidence += 0.4
        elif ip_signal.country_code == jurisdiction.country:
            # Country-level match: IP alone is sufficient for non-US
            confidence += 0.5

        # GPS matches
        if gps_signal:
            if jurisdiction.region and gps_signal.region_code == jurisdiction.region:
                confidence += 0.4
            elif gps_signal.country_code == jurisdiction.country:
                confidence += 0.3

        # CF edge matches
        if cf_country == jurisdiction.country:
            confidence += 0.2

        return min(confidence, 1.0)

    def _check_exclusion_zones(
        self, signal: GeoSignal, jurisdiction: str,
    ) -> Optional[str]:
        """Check if location falls within an exclusion zone."""
        if not signal.latitude:
            return None

        for zone in EXCLUSION_ZONES:
            if zone["jurisdiction"] != jurisdiction:
                continue
            dist = haversine_km(
                signal.latitude, signal.longitude,
                zone["lat"], zone["lon"],
            )
            if dist <= zone["radius_km"]:
                return zone["name"]
        return None

    def _is_mobile(self, user_agent: str) -> bool:
        """Simple mobile detection from user agent."""
        ua = user_agent.lower()
        return any(
            kw in ua for kw in ["mobile", "android", "iphone", "ipad"]
        )

    def _log_check(self, result: VerificationResult) -> None:
        """Append verification result to audit log."""
        self._audit_log.append(asdict(result))
        logger.info(
            "Geo check: player=%s verdict=%s jurisdiction=%s confidence=%.2f",
            result.player_id, result.verdict,
            result.jurisdiction, result.confidence,
        )
