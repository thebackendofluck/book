# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Data enrichment for the Fraud Detection Data Ingestion Service
"""

import asyncio
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import structlog
import httpx

from .config import settings  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)


class DataEnricher:
    """Data enrichment service for fraud detection events"""

    def __init__(self):
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )

    def _ingestion_timestamp(self) -> str:
        """Return a consistent ingestion timestamp for enriched payloads."""

        return datetime.now(timezone.utc).isoformat()

    async def enrich_transaction(self, data: Dict[str, Any],
                                request: Any = None) -> Dict[str, Any]:
        """
        Enrich transaction data with additional information

        Args:
            data: Transaction data dictionary
            request: FastAPI request object

        Returns:
            Enriched transaction data
        """

        enriched = data.copy()
        enriched["ingested_at"] = self._ingestion_timestamp()

        # Extract request metadata if available
        if request:
            enriched.update(self._extract_request_metadata(request))

        # IP geolocation enrichment
        if settings.enable_ip_geolocation and data.get("ip_address"):
            geo_data = await self._enrich_ip_geolocation(data["ip_address"])
            if geo_data:
                enriched["location_data"] = geo_data

        # Device fingerprinting enrichment
        if settings.enable_device_fingerprinting and data.get("device_fingerprint"):
            device_data = await self._enrich_device_fingerprint(data["device_fingerprint"])
            if device_data:
                enriched["device_data"] = device_data

        # Risk indicators
        enriched["risk_indicators"] = self._calculate_transaction_risk_indicators(enriched)

        return enriched

    async def enrich_user_event(self, data: Dict[str, Any],
                               request: Any = None) -> Dict[str, Any]:
        """
        Enrich user event data

        Args:
            data: User event data dictionary
            request: FastAPI request object

        Returns:
            Enriched user event data
        """

        enriched = data.copy()
        enriched["ingested_at"] = self._ingestion_timestamp()

        # Extract request metadata
        if request:
            enriched.update(self._extract_request_metadata(request))

        # IP geolocation enrichment
        if settings.enable_ip_geolocation and data.get("ip_address"):
            geo_data = await self._enrich_ip_geolocation(data["ip_address"])
            if geo_data:
                enriched["location_data"] = geo_data

        # Session analysis
        if data.get("session_id"):
            session_data = await self._enrich_session_data(data["session_id"])
            if session_data:
                enriched["session_data"] = session_data

        # Behavioral patterns
        enriched["behavioral_indicators"] = self._calculate_behavioral_indicators(enriched)

        return enriched

    async def enrich_game_event(self, data: Dict[str, Any],
                               request: Any = None) -> Dict[str, Any]:
        """
        Enrich game event data

        Args:
            data: Game event data dictionary
            request: FastAPI request object

        Returns:
            Enriched game event data
        """

        enriched = data.copy()
        enriched["ingested_at"] = self._ingestion_timestamp()

        # Extract request metadata
        if request:
            enriched.update(self._extract_request_metadata(request))

        # IP geolocation enrichment
        if settings.enable_ip_geolocation and data.get("ip_address"):
            geo_data = await self._enrich_ip_geolocation(data["ip_address"])
            if geo_data:
                enriched["location_data"] = geo_data

        # Game session enrichment
        if data.get("game_session_id"):
            game_session_data = await self._enrich_game_session_data(data["game_session_id"])
            if game_session_data:
                enriched["game_session_data"] = game_session_data

        # Game-specific risk indicators
        enriched["game_risk_indicators"] = self._calculate_game_risk_indicators(enriched)

        return enriched

    def _extract_request_metadata(self, request: Any) -> Dict[str, Any]:
        """Extract metadata from HTTP request"""

        metadata = {
            "request_timestamp": datetime.now(timezone.utc).isoformat(),
            "user_agent": request.headers.get("user-agent", ""),
            "accept_language": request.headers.get("accept-language", ""),
            "referer": request.headers.get("referer", ""),
            "x_forwarded_for": request.headers.get("x-forwarded-for", ""),
            "x_real_ip": request.headers.get("x-real-ip", ""),
            "content_type": request.headers.get("content-type", ""),
            "request_method": request.method,
            "request_url": str(request.url),
            "client_ip": self._get_client_ip(request)
        }

        return metadata

    def _get_client_ip(self, request: Any) -> str:
        """Extract client IP from request"""

        # Check X-Forwarded-For header first
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            # Take the first IP in case of multiple proxies
            return x_forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip:
            return x_real_ip

        # Fallback to request client
        if hasattr(request, "client") and request.client:
            return request.client.host

        return ""

    async def _enrich_ip_geolocation(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """
        Enrich IP address with geolocation data

        Args:
            ip_address: IP address string

        Returns:
            Geolocation data dictionary or None
        """

        if not settings.maxmind_api_key:
            # Return mock data for development
            return self._mock_geolocation_data(ip_address)

        try:
            # Use MaxMind API for geolocation
            url = f"https://geolite.info/geoip/v2.1/city/{ip_address}"
            headers = {"Authorization": f"Bearer {settings.maxmind_api_key}"}

            response = await self.http_client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()

            return {
                "country": data.get("country", {}).get("iso_code"),
                "country_name": data.get("country", {}).get("names", {}).get("en"),
                "city": data.get("city", {}).get("names", {}).get("en"),
                "region": data.get("subdivisions", [{}])[0].get("iso_code"),
                "region_name": data.get("subdivisions", [{}])[0].get("names", {}).get("en"),
                "postal_code": data.get("postal", {}).get("code"),
                "latitude": data.get("location", {}).get("latitude"),
                "longitude": data.get("location", {}).get("longitude"),
                "timezone": data.get("location", {}).get("time_zone"),
                "accuracy_radius": data.get("location", {}).get("accuracy_radius"),
                "enrichment_source": "maxmind",
                "enriched_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.warning("Failed to enrich IP geolocation",
                         ip_address=ip_address, error=str(e))
            return None

    def _mock_geolocation_data(self, ip_address: str) -> Dict[str, Any]:
        """Return mock geolocation data for development"""

        # Simple mock based on IP patterns
        if ip_address.startswith("192.168.") or ip_address.startswith("10."):
            return {
                "country": "US",
                "country_name": "United States",
                "city": "Local Network",
                "region": "CA",
                "region_name": "California",
                "latitude": 37.7749,
                "longitude": -122.4194,
                "timezone": "America/Los_Angeles",
                "enrichment_source": "mock",
                "enriched_at": datetime.now(timezone.utc).isoformat()
            }

        return {
            "country": "US",
            "country_name": "United States",
            "city": "Unknown",
            "region": "CA",
            "region_name": "California",
            "latitude": 37.7749,
            "longitude": -122.4194,
            "timezone": "America/Los_Angeles",
            "enrichment_source": "mock",
            "enriched_at": datetime.now(timezone.utc).isoformat()
        }

    async def _enrich_device_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """
        Enrich device fingerprint with additional data

        Args:
            fingerprint: Device fingerprint string

        Returns:
            Device data dictionary or None
        """

        if not settings.fingerprintjs_api_key:
            # Return mock data for development
            return self._mock_device_data(fingerprint)

        try:
            # Use FingerprintJS API for device intelligence
            url = f"https://api.fpjs.io/visitors/{fingerprint}"
            headers = {"Auth-API-Key": settings.fingerprintjs_api_key}

            response = await self.http_client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()

            # Extract relevant device information
            visitor = data.get("visits", [{}])[0] if data.get("visits") else {}

            return {
                "browser_name": visitor.get("browserName"),
                "browser_version": visitor.get("browserVersion"),
                "os": visitor.get("os"),
                "os_version": visitor.get("osVersion"),
                "device": visitor.get("device"),
                "bot_probability": visitor.get("bot", {}).get("probability"),
                "incognito": visitor.get("incognito"),
                "ip_location": visitor.get("ipLocation"),
                "enrichment_source": "fingerprintjs",
                "enriched_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.warning("Failed to enrich device fingerprint",
                         fingerprint=fingerprint, error=str(e))
            return None

    def _mock_device_data(self, fingerprint: str) -> Dict[str, Any]:
        """Return mock device data for development"""

        return {
            "browser_name": "Chrome",
            "browser_version": "119.0.0.0",
            "os": "Windows",
            "os_version": "10",
            "device": "Desktop",
            "bot_probability": 0.0,
            "incognito": False,
            "enrichment_source": "mock",
            "enriched_at": datetime.now(timezone.utc).isoformat()
        }

    async def _enrich_session_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Enrich session data (would typically query database/cache)

        Args:
            session_id: Session identifier

        Returns:
            Session data dictionary or None
        """

        # In a real implementation, this would query Redis/PostgreSQL
        # For now, return mock data
        return {
            "session_start": datetime.now(timezone.utc).isoformat(),
            "session_duration_minutes": 45,
            "page_views": 12,
            "events_count": 25,
            "enrichment_source": "mock",
            "enriched_at": datetime.now(timezone.utc).isoformat()
        }

    async def _enrich_game_session_data(self, game_session_id: str) -> Optional[Dict[str, Any]]:
        """
        Enrich game session data

        Args:
            game_session_id: Game session identifier

        Returns:
            Game session data dictionary or None
        """

        # In a real implementation, this would query the database
        # For now, return mock data
        return {
            "game_start": datetime.now(timezone.utc).isoformat(),
            "total_bets": 150.00,
            "total_wins": 120.00,
            "net_result": -30.00,
            "spins_count": 25,
            "max_bet": 10.00,
            "enrichment_source": "mock",
            "enriched_at": datetime.now(timezone.utc).isoformat()
        }

    def _calculate_transaction_risk_indicators(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate risk indicators for transaction

        Args:
            data: Transaction data

        Returns:
            Risk indicators dictionary
        """

        indicators = {
            "amount_outlier": False,
            "unusual_time": False,
            "new_payment_method": False,
            "location_mismatch": False,
            "velocity_high": False
        }

        # Simple rule-based indicators (in production, these would use ML models)
        amount = data.get("amount", 0)

        # Amount outlier detection (simple threshold)
        if amount > 10000:
            indicators["amount_outlier"] = True

        # Unusual time detection (simple rule)
        timestamp = data.get("timestamp")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                hour = dt.hour
                if hour < 6 or hour > 22:  # Outside normal hours
                    indicators["unusual_time"] = True
            except Exception: 
                pass

        # Location mismatch (compare IP location with declared location)
        location_data = data.get("location_data") or {}
        metadata = data.get("metadata") or {}
        ip_location = location_data.get("country")
        declared_location = metadata.get("declared_country")
        if ip_location and declared_location and ip_location != declared_location:
            indicators["location_mismatch"] = True

        return indicators

    def _calculate_behavioral_indicators(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate behavioral indicators for user events

        Args:
            data: User event data

        Returns:
            Behavioral indicators dictionary
        """

        indicators = {
            "rapid_clicking": False,
            "unusual_navigation": False,
            "session_anomaly": False
        }

        # Simple behavioral analysis
        event_type = data.get("event_type")

        # Rapid clicking detection
        if event_type == "button_click":
            # In production, this would analyze click frequency
            indicators["rapid_clicking"] = False  # Placeholder

        # Unusual navigation patterns
        if event_type == "page_view":
            page_url = data.get("page_url", "")
            if "admin" in page_url or "config" in page_url:
                indicators["unusual_navigation"] = True

        return indicators

    def _calculate_game_risk_indicators(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate risk indicators for game events

        Args:
            data: Game event data

        Returns:
            Game risk indicators dictionary
        """

        indicators = {
            "unusual_bet_pattern": False,
            "rapid_gambling": False,
            "bonus_abuse": False
        }

        event_type = data.get("event_type")
        bet_amount = data.get("bet_amount", 0)

        # Unusual bet pattern detection
        if event_type == "bet" and bet_amount > 0:
            # Simple threshold-based detection
            if bet_amount > 1000:
                indicators["unusual_bet_pattern"] = True

        # Rapid gambling detection
        if event_type in ["spin", "bet"]:
            # In production, this would analyze frequency
            indicators["rapid_gambling"] = False  # Placeholder

        # Bonus abuse detection
        if event_type == "bonus":
            indicators["bonus_abuse"] = False  # Placeholder

        return indicators

    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()
