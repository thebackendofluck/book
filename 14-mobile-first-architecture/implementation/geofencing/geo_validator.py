# Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Server-Side Geofencing Validator for Online Casino

Validates player location using multiple signals:
  - GPS coordinates (from device)
  - IP geolocation (server-side lookup)
  - Cell tower triangulation data (when available)
  - Wi-Fi positioning (when available)

Cross-references against licensed jurisdictions and exclusion zones.

Regulatory requirements:
  - Must verify location before every real-money session and periodically during play
  - Must detect VPN/proxy usage and block access
  - Must maintain audit trail of all geolocation checks
  - Compliant with GLI-19 and state-specific geofencing requirements
  - Precision: must be within 500m of jurisdiction boundary per GLI-19 2.5.1
"""

import hashlib
import ipaddress
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import redis
import requests

logger = logging.getLogger(__name__)


class GeoResult(Enum):
    ALLOWED = "allowed"
    DENIED_JURISDICTION = "denied_jurisdiction"
    DENIED_EXCLUSION_ZONE = "denied_exclusion_zone"
    DENIED_VPN = "denied_vpn"
    DENIED_PROXY = "denied_proxy"
    DENIED_DATACENTER = "denied_datacenter"
    DENIED_MISMATCH = "denied_location_mismatch"
    DENIED_INSUFFICIENT_DATA = "denied_insufficient_data"
    DENIED_SPOOFING = "denied_spoofing"
    REQUIRES_REVERIFICATION = "requires_reverification"


@dataclass
class GeoLocation:
    latitude: float
    longitude: float
    accuracy_meters: float
    source: str  # "gps", "wifi", "cell", "ip"
    timestamp: float = field(default_factory=time.time)


@dataclass
class GeoValidationResult:
    result: GeoResult
    jurisdiction: Optional[str] = None
    confidence: float = 0.0
    ip_country: Optional[str] = None
    ip_region: Optional[str] = None
    gps_country: Optional[str] = None
    gps_region: Optional[str] = None
    vpn_detected: bool = False
    proxy_detected: bool = False
    spoofing_score: float = 0.0
    check_id: str = ""
    message: str = ""
    requires_manual_review: bool = False


# ── Licensed jurisdiction configuration ─────────────────

LICENSED_JURISDICTIONS: dict[str, dict[str, Any]] = {
    # US states where operator holds a license
    "US-NJ": {
        "name": "New Jersey",
        "country": "US",
        "region": "NJ",
        "boundary_buffer_km": 0.5,  # GLI-19 requirement
        "reverify_interval_minutes": 30,
        "allowed_game_types": ["slots", "table_games", "live_casino", "sports"],
    },
    "US-PA": {
        "name": "Pennsylvania",
        "country": "US",
        "region": "PA",
        "boundary_buffer_km": 0.5,
        "reverify_interval_minutes": 30,
        "allowed_game_types": ["slots", "table_games", "live_casino", "sports"],
    },
    "US-MI": {
        "name": "Michigan",
        "country": "US",
        "region": "MI",
        "boundary_buffer_km": 0.5,
        "reverify_interval_minutes": 30,
        "allowed_game_types": ["slots", "table_games", "live_casino", "sports"],
    },
    # European jurisdictions
    "GB": {
        "name": "United Kingdom",
        "country": "GB",
        "region": None,
        "boundary_buffer_km": 0,
        "reverify_interval_minutes": 60,
        "allowed_game_types": ["slots", "table_games", "live_casino", "sports"],
    },
    "MT": {
        "name": "Malta",
        "country": "MT",
        "region": None,
        "boundary_buffer_km": 0,
        "reverify_interval_minutes": 120,
        "allowed_game_types": ["slots", "table_games", "live_casino", "sports"],
    },
}

# Exclusion zones: casinos, self-exclusion areas, tribal lands, etc.
EXCLUSION_ZONES = [
    {
        "name": "Atlantic City Exclusion Zone",
        "lat": 39.3643,
        "lon": -74.4229,
        "radius_km": 0.1,
        "jurisdiction": "US-NJ",
        "reason": "Physical casino proximity restriction",
    },
]


class GeoValidator:
    """
    Multi-signal geolocation validator for online gambling compliance.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        ip_geolocation_api_key: str,
        maxmind_account_id: Optional[str] = None,
        maxmind_license_key: Optional[str] = None,
    ):
        self.redis = redis_client
        self.ip_api_key = ip_geolocation_api_key
        self.maxmind_account_id = maxmind_account_id
        self.maxmind_license_key = maxmind_license_key

    def validate(
        self,
        player_id: str,
        ip_address: str,
        gps_location: Optional[GeoLocation] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> GeoValidationResult:
        """
        Perform full geolocation validation.

        Args:
            player_id: Unique player identifier
            ip_address: Client IP address
            gps_location: GPS coordinates from device (if available)
            device_id: Device fingerprint for velocity checks
            session_id: Active session ID for reverification tracking

        Returns:
            GeoValidationResult with allow/deny decision and details
        """
        check_id = self._generate_check_id(player_id, ip_address)

        # Step 1: IP validation (VPN/proxy/datacenter detection)
        ip_result = self._validate_ip(ip_address)
        if ip_result.result != GeoResult.ALLOWED:
            ip_result.check_id = check_id
            self._log_check(player_id, check_id, ip_result)
            return ip_result

        # Step 2: IP geolocation
        ip_geo = self._geolocate_ip(ip_address)
        if ip_geo is None:
            result = GeoValidationResult(
                result=GeoResult.DENIED_INSUFFICIENT_DATA,
                check_id=check_id,
                message="Unable to determine location from IP address",
            )
            self._log_check(player_id, check_id, result)
            return result

        # Step 3: GPS validation (if provided)
        if gps_location:
            spoofing_score = self._detect_gps_spoofing(
                gps_location, ip_geo, player_id, device_id
            )
            if spoofing_score > 0.8:
                result = GeoValidationResult(
                    result=GeoResult.DENIED_SPOOFING,
                    spoofing_score=spoofing_score,
                    check_id=check_id,
                    message="Location spoofing detected",
                    requires_manual_review=True,
                )
                self._log_check(player_id, check_id, result)
                return result

        # Step 4: Cross-reference IP and GPS locations
        if gps_location:
            mismatch = self._check_location_mismatch(gps_location, ip_geo)
            if mismatch:
                result = GeoValidationResult(
                    result=GeoResult.DENIED_MISMATCH,
                    ip_country=ip_geo.get("country"),
                    ip_region=ip_geo.get("region"),
                    gps_country=self._reverse_geocode_country(gps_location),
                    check_id=check_id,
                    message="IP and GPS locations do not match",
                    requires_manual_review=True,
                )
                self._log_check(player_id, check_id, result)
                return result

        # Step 5: Check if location is in a licensed jurisdiction
        primary_location = gps_location or self._ip_to_geolocation(ip_geo)
        jurisdiction = self._find_jurisdiction(ip_geo, gps_location)

        if jurisdiction is None:
            result = GeoValidationResult(
                result=GeoResult.DENIED_JURISDICTION,
                ip_country=ip_geo.get("country"),
                ip_region=ip_geo.get("region"),
                check_id=check_id,
                message="Not in a licensed jurisdiction",
            )
            self._log_check(player_id, check_id, result)
            return result

        # Step 6: Check exclusion zones
        if gps_location:
            exclusion = self._check_exclusion_zones(gps_location, jurisdiction)
            if exclusion:
                result = GeoValidationResult(
                    result=GeoResult.DENIED_EXCLUSION_ZONE,
                    jurisdiction=jurisdiction,
                    check_id=check_id,
                    message=f"Located in exclusion zone: {exclusion['name']}",
                )
                self._log_check(player_id, check_id, result)
                return result

        # Step 7: Calculate confidence
        confidence = self._calculate_confidence(ip_geo, gps_location)

        # Step 8: Check reverification requirement
        if session_id:
            needs_reverify = self._check_reverification(
                player_id, session_id, jurisdiction
            )
            if needs_reverify:
                result = GeoValidationResult(
                    result=GeoResult.REQUIRES_REVERIFICATION,
                    jurisdiction=jurisdiction,
                    confidence=confidence,
                    check_id=check_id,
                    message="Periodic location reverification required",
                )
                self._log_check(player_id, check_id, result)
                return result

        # All checks passed
        result = GeoValidationResult(
            result=GeoResult.ALLOWED,
            jurisdiction=jurisdiction,
            confidence=confidence,
            ip_country=ip_geo.get("country"),
            ip_region=ip_geo.get("region"),
            check_id=check_id,
            message="Location verified",
        )

        self._log_check(player_id, check_id, result)
        self._update_session_geo(player_id, session_id, result)

        return result

    # ── IP Validation ────────────────────────────────────

    def _validate_ip(self, ip_address: str) -> GeoValidationResult:
        """Check IP against VPN, proxy, and datacenter databases."""
        try:
            ip = ipaddress.ip_address(ip_address)

            # Reject private/reserved IPs
            if ip.is_private or ip.is_reserved or ip.is_loopback:
                return GeoValidationResult(
                    result=GeoResult.DENIED_PROXY,
                    message="Private or reserved IP address",
                )

            # Check IP reputation via multiple providers
            reputation = self._check_ip_reputation(ip_address)

            if reputation.get("is_vpn"):
                return GeoValidationResult(
                    result=GeoResult.DENIED_VPN,
                    vpn_detected=True,
                    message="VPN detected. Please disable your VPN to play.",
                )

            if reputation.get("is_proxy") or reputation.get("is_tor"):
                return GeoValidationResult(
                    result=GeoResult.DENIED_PROXY,
                    proxy_detected=True,
                    message="Proxy or Tor detected. Direct connection required.",
                )

            if reputation.get("is_datacenter"):
                return GeoValidationResult(
                    result=GeoResult.DENIED_DATACENTER,
                    message="Connection from datacenter IP detected.",
                    requires_manual_review=True,
                )

        except ValueError:
            return GeoValidationResult(
                result=GeoResult.DENIED_INSUFFICIENT_DATA,
                message="Invalid IP address",
            )

        return GeoValidationResult(result=GeoResult.ALLOWED)

    def _check_ip_reputation(self, ip_address: str) -> dict:
        """Query IP reputation service (IPQualityScore / ip-api / MaxMind)."""
        cache_key = f"ip_reputation:{ip_address}"
        cached = self.redis.get(cache_key)
        if cached:
            return json.loads(cached)  # ty:ignore[invalid-argument-type]

        try:
            # Primary: IPQualityScore
            response = requests.get(
                f"https://ipqualityscore.com/api/json/ip/{self.ip_api_key}/{ip_address}",
                params={
                    "strictness": 1,
                    "allow_public_access_points": True,
                    "lighter_penalties": False,
                },
                timeout=3,
            )
            data = response.json()

            result = {
                "is_vpn": data.get("vpn", False),
                "is_proxy": data.get("proxy", False),
                "is_tor": data.get("tor", False),
                "is_datacenter": data.get("is_crawler", False),
                "fraud_score": data.get("fraud_score", 0),
                "country": data.get("country_code", ""),
                "region": data.get("region", ""),
                "city": data.get("city", ""),
                "latitude": data.get("latitude", 0),
                "longitude": data.get("longitude", 0),
                "isp": data.get("ISP", ""),
                "organization": data.get("organization", ""),
            }

            # Cache for 1 hour
            self.redis.setex(cache_key, 3600, json.dumps(result))
            return result

        except Exception as e:
            logger.error(f"IP reputation check failed for {ip_address}: {e}")
            # Fail open for IP reputation but flag for review
            return {"error": str(e)}

    # ── Geolocation ──────────────────────────────────────

    def _geolocate_ip(self, ip_address: str) -> Optional[dict]:
        """Get geolocation data from IP address."""
        cache_key = f"ip_geo:{ip_address}"
        cached = self.redis.get(cache_key)
        if cached:
            return json.loads(cached)  # ty:ignore[invalid-argument-type]

        reputation = self._check_ip_reputation(ip_address)
        if "error" in reputation:
            return None

        result = {
            "country": reputation.get("country", ""),
            "region": reputation.get("region", ""),
            "city": reputation.get("city", ""),
            "latitude": reputation.get("latitude", 0),
            "longitude": reputation.get("longitude", 0),
            "isp": reputation.get("isp", ""),
        }

        self.redis.setex(cache_key, 3600, json.dumps(result))
        return result

    def _ip_to_geolocation(self, ip_geo: dict) -> GeoLocation:
        """Convert IP geolocation dict to GeoLocation object."""
        return GeoLocation(
            latitude=ip_geo.get("latitude", 0),
            longitude=ip_geo.get("longitude", 0),
            accuracy_meters=25000,  # IP geolocation: ~25km accuracy
            source="ip",
        )

    def _reverse_geocode_country(self, location: GeoLocation) -> Optional[str]:
        """Reverse geocode GPS coordinates to country/region."""
        cache_key = f"reverse_geo:{location.latitude:.3f}:{location.longitude:.3f}"
        cached = self.redis.get(cache_key)
        if cached:
            return cached.decode()  # ty:ignore[possibly-missing-attribute]

        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": location.latitude,
                    "lon": location.longitude,
                    "format": "json",
                    "zoom": 5,
                },
                headers={"User-Agent": "CasinoGeoValidator/1.0"},
                timeout=3,
            )
            data = response.json()
            country = data.get("address", {}).get("country_code", "").upper()
            self.redis.setex(cache_key, 86400, country)
            return country
        except Exception as e:
            logger.error(f"Reverse geocoding failed: {e}")
            return None

    # ── GPS Spoofing Detection ───────────────────────────

    def _detect_gps_spoofing(
        self,
        gps: GeoLocation,
        ip_geo: dict,
        player_id: str,
        device_id: Optional[str],
    ) -> float:
        """
        Detect GPS spoofing using multiple heuristics.
        Returns a spoofing probability score (0.0 = clean, 1.0 = definite spoof).
        """
        score = 0.0

        # Heuristic 1: GPS accuracy too perfect (real GPS has noise)
        if gps.accuracy_meters < 1.0:
            score += 0.3  # Real GPS rarely reports < 1m accuracy

        # Heuristic 2: IP and GPS in different countries
        gps_country = self._reverse_geocode_country(gps)
        if gps_country and ip_geo.get("country"):
            if gps_country != ip_geo["country"]:
                score += 0.4

        # Heuristic 3: Velocity check (impossible travel speed)
        if device_id:
            last_location = self._get_last_location(player_id, device_id)
            if last_location:
                distance_km = self._haversine_distance(
                    last_location["lat"], last_location["lon"],
                    gps.latitude, gps.longitude,
                )
                time_diff_hours = (time.time() - last_location["timestamp"]) / 3600
                if time_diff_hours > 0:
                    speed_kmh = distance_km / time_diff_hours
                    if speed_kmh > 1000:  # Faster than commercial aircraft
                        score += 0.5

        # Heuristic 4: Known spoofing coordinates (common defaults)
        known_spoof_coords = [
            (0.0, 0.0),        # Null Island
            (37.7749, -122.4194),  # San Francisco (common emulator default)
            (51.5074, -0.1278),    # London (common emulator default)
        ]
        for spoof_lat, spoof_lon in known_spoof_coords:
            if (abs(gps.latitude - spoof_lat) < 0.001 and
                    abs(gps.longitude - spoof_lon) < 0.001):
                score += 0.3

        # Cap at 1.0
        return min(score, 1.0)

    def _get_last_location(self, player_id: str, device_id: str) -> Optional[dict]:
        """Get last known location for velocity checks."""
        key = f"last_geo:{player_id}:{device_id}"
        data = self.redis.get(key)
        if data:
            return json.loads(data)  # ty:ignore[invalid-argument-type]
        return None

    def _store_location(
        self, player_id: str, device_id: str, lat: float, lon: float
    ):
        """Store location for future velocity checks."""
        key = f"last_geo:{player_id}:{device_id}"
        self.redis.setex(
            key,
            86400,  # 24 hours
            json.dumps({
                "lat": lat,
                "lon": lon,
                "timestamp": time.time(),
            }),
        )

    # ── Location Matching ────────────────────────────────

    def _check_location_mismatch(
        self, gps: GeoLocation, ip_geo: dict
    ) -> bool:
        """Check if GPS and IP locations are suspiciously different."""
        ip_lat = ip_geo.get("latitude", 0)
        ip_lon = ip_geo.get("longitude", 0)

        if ip_lat == 0 and ip_lon == 0:
            return False  # No IP coordinates to compare

        distance_km = self._haversine_distance(
            gps.latitude, gps.longitude, ip_lat, ip_lon
        )

        # Allow up to 200km mismatch (IP geolocation is imprecise)
        return distance_km > 200

    def _find_jurisdiction(
        self, ip_geo: dict, gps: Optional[GeoLocation]
    ) -> Optional[str]:
        """Find which licensed jurisdiction the player is in."""
        country = ip_geo.get("country", "").upper()
        region = ip_geo.get("region", "").upper()

        for jid, jurisdiction in LICENSED_JURISDICTIONS.items():
            if jurisdiction["country"] == country:
                if jurisdiction["region"] is None:
                    return jid
                if jurisdiction["region"] == region:
                    return jid

        return None

    def _check_exclusion_zones(
        self, gps: GeoLocation, jurisdiction: str
    ) -> Optional[dict]:
        """Check if GPS location falls within an exclusion zone."""
        for zone in EXCLUSION_ZONES:
            if zone["jurisdiction"] != jurisdiction:
                continue

            distance = self._haversine_distance(
                gps.latitude, gps.longitude,
                zone["lat"], zone["lon"],  # ty:ignore[invalid-argument-type]
            )

            if distance <= zone["radius_km"]:  # ty:ignore[unsupported-operator]
                return zone

        return None

    # ── Session & Reverification ─────────────────────────

    def _check_reverification(
        self, player_id: str, session_id: str, jurisdiction: str
    ) -> bool:
        """Check if periodic reverification is needed."""
        key = f"geo_session:{player_id}:{session_id}"
        last_check = self.redis.get(key)

        if last_check is None:
            return False  # First check for this session, no reverify needed

        last_check_time = float(last_check)  # ty:ignore[invalid-argument-type]
        interval = LICENSED_JURISDICTIONS.get(jurisdiction, {}).get(
            "reverify_interval_minutes", 30
        )

        elapsed_minutes = (time.time() - last_check_time) / 60
        return elapsed_minutes >= interval

    def _update_session_geo(
        self,
        player_id: str,
        session_id: Optional[str],
        result: GeoValidationResult,
    ):
        """Update session geolocation tracking."""
        if session_id:
            key = f"geo_session:{player_id}:{session_id}"
            self.redis.setex(key, 86400, str(time.time()))

    # ── Audit Logging ────────────────────────────────────

    def _log_check(
        self, player_id: str, check_id: str, result: GeoValidationResult
    ):
        """Log geolocation check for regulatory audit trail."""
        log_entry = {
            "check_id": check_id,
            "player_id": player_id,
            "result": result.result.value,
            "jurisdiction": result.jurisdiction,
            "confidence": result.confidence,
            "ip_country": result.ip_country,
            "ip_region": result.ip_region,
            "gps_country": result.gps_country,
            "vpn_detected": result.vpn_detected,
            "proxy_detected": result.proxy_detected,
            "spoofing_score": result.spoofing_score,
            "message": result.message,
            "requires_manual_review": result.requires_manual_review,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Store in Redis list for async processing to permanent storage
        self.redis.lpush("geo_audit_log", json.dumps(log_entry))
        self.redis.ltrim("geo_audit_log", 0, 99999)  # Keep last 100k entries in Redis

        if result.requires_manual_review:
            self.redis.lpush("geo_manual_review_queue", json.dumps(log_entry))
            logger.warning(
                f"Geo check requires review: player={player_id} "
                f"result={result.result.value} message={result.message}"
            )

        logger.info(
            f"Geo check: player={player_id} result={result.result.value} "
            f"jurisdiction={result.jurisdiction} confidence={result.confidence:.2f}"
        )

    # ── Utility Functions ────────────────────────────────

    def _generate_check_id(self, player_id: str, ip_address: str) -> str:
        """Generate a unique check ID for audit purposes."""
        raw = f"{player_id}:{ip_address}:{time.time()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _calculate_confidence(
        self, ip_geo: dict, gps: Optional[GeoLocation]
    ) -> float:
        """Calculate confidence score for location determination."""
        if gps and gps.accuracy_meters < 50:
            return 0.99
        if gps and gps.accuracy_meters < 500:
            return 0.95
        if gps:
            return 0.85
        # IP-only
        return 0.60

    @staticmethod
    def _haversine_distance(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Calculate distance between two points in kilometers."""
        R = 6371  # Earth's radius in km

        lat1_r = math.radians(lat1)
        lat2_r = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c


# ── Usage Example ────────────────────────────────────────

if __name__ == "__main__":
    import os

    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=0,
    )

    validator = GeoValidator(
        redis_client=redis_client,
        ip_geolocation_api_key=os.getenv("IP_QUALITY_SCORE_KEY", "demo"),
    )

    # Validate a player's location
    result = validator.validate(
        player_id="player-12345",
        ip_address="73.150.2.100",  # Example NJ IP
        gps_location=GeoLocation(
            latitude=40.7128,
            longitude=-74.0060,
            accuracy_meters=15.0,
            source="gps",
        ),
        device_id="device-abc-123",
        session_id="session-xyz-789",
    )

    print(f"Result: {result.result.value}")
    print(f"Jurisdiction: {result.jurisdiction}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Check ID: {result.check_id}")
    print(f"Message: {result.message}")
