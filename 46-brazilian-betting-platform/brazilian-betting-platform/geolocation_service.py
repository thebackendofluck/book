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
Geolocation Verification Service -- Brazilian Betting Platform
==============================================================
Enforces the geographic restriction requirements of Lei 14.790/2023:
  - Only players physically located in Brazil may place bets
  - Re-verification every 30 minutes during active session
  - VPN / proxy / Tor detection
  - Fallback chain: GPS → WiFi → IP geolocation → block

Features:
  - 30-minute re-verification scheduler per active session
  - GPS / WiFi / IP-based location detection
  - Brazilian state and municipality identification
  - VPN / proxy / Tor exit node detection
  - Session management integration
  - Location accuracy validation
  - Structured logging and compliance audit trail

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class GeoError(Exception):
    """Base geolocation exception."""


class LocationOutsideBrazilError(GeoError):
    """Player is not in Brazil."""


class VPNDetectedError(GeoError):
    """VPN or proxy detected."""


class InsufficientAccuracyError(GeoError):
    """GPS/WiFi accuracy too low for confident determination."""


class GeoVerificationExpiredError(GeoError):
    """30-minute re-verification window has elapsed."""


class SessionBlockedError(GeoError):
    """Session blocked pending geolocation re-verification."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LocationMethod(str, enum.Enum):
    GPS = "gps"
    WIFI = "wifi"
    IP = "ip"
    BLOCKED = "blocked"


class GeoStatus(str, enum.Enum):
    VERIFIED = "verified"
    PENDING = "pending"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    OUTSIDE_BRAZIL = "outside_brazil"
    VPN_DETECTED = "vpn_detected"


class BrazilianRegion(str, enum.Enum):
    NORTE = "norte"
    NORDESTE = "nordeste"
    CENTRO_OESTE = "centro_oeste"
    SUDESTE = "sudeste"
    SUL = "sul"


# ---------------------------------------------------------------------------
# Constants -- Brazil geographic boundaries
# ---------------------------------------------------------------------------

BRAZIL_BBOX = {
    "lat_min": -33.75,
    "lat_max": 5.27,
    "lon_min": -73.99,
    "lon_max": -28.84,
}

# ISO 3166-2:BR state codes
BRAZIL_STATES = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
    "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
    "RO", "RR", "RS", "SC", "SE", "SP", "TO",
}

# Re-verification interval
REVERIFICATION_INTERVAL_SECONDS = 1800  # 30 minutes

# Minimum GPS accuracy in metres for hard confirmation
MIN_GPS_ACCURACY_METRES = 1000

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class GPSCoordinates(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_metres: float = Field(default=100.0, gt=0)
    altitude_metres: Optional[float] = None
    heading: Optional[float] = None
    speed_kmh: Optional[float] = None


class WiFiNetworks(BaseModel):
    """Client-submitted WiFi scan data for location estimation."""
    networks: List[Dict[str, Any]] = Field(
        ...,
        description="List of {bssid, ssid, signal_strength} objects",
    )


class GeoVerificationRequest(BaseModel):
    session_id: str
    player_id: str
    method: LocationMethod
    gps: Optional[GPSCoordinates] = None
    wifi: Optional[WiFiNetworks] = None
    # NOT trusted as-is: a client can put anything here. The FastAPI route
    # handler overwrites this with the real peer IP (`request.client.host`)
    # before the request reaches GeolocationService.verify(). The field
    # stays on the model only so internal callers (tests, batch jobs) that
    # already know the peer IP can populate it directly.
    ip_address: str = Field(default="0.0.0.0")
    user_agent: Optional[str] = None


class GeoVerificationResponse(BaseModel):
    verification_id: str
    session_id: str
    player_id: str
    status: GeoStatus
    method_used: LocationMethod
    country_code: Optional[str] = None
    state_code: Optional[str] = None
    municipality: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_metres: Optional[float] = None
    vpn_detected: bool = False
    verified_at: datetime
    next_verification_due: datetime
    message: str = ""


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class LocationFix:
    method: LocationMethod
    latitude: float
    longitude: float
    accuracy_metres: float
    country_code: str
    state_code: Optional[str]
    municipality: Optional[str]
    region: Optional[BrazilianRegion]
    vpn_detected: bool
    confidence: float  # 0.0 - 1.0


@dataclass
class GeoSession:
    session_id: str
    player_id: str
    status: GeoStatus
    last_fix: Optional[LocationFix]
    verified_at: Optional[datetime]
    next_due: Optional[datetime]
    verification_count: int = 0
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# VPN / Proxy Detector
# ---------------------------------------------------------------------------


class VPNDetector:
    """
    Checks IP against known VPN/proxy/Tor ranges.
    In production: integrate ipinfo.io, ip-api.com, or MaxMind.
    """

    # Known commercial VPN provider CIDR stubs
    _VPN_PREFIXES = [
        "185.220.", "185.244.", "45.142.", "198.54.",
        "104.16.", "172.67.", "91.108.", "149.154.",
    ]

    # Tor exit node list (stub; pull from https://check.torproject.org/exit-addresses)
    _TOR_PREFIXES = ["199.249.", "104.244.", "185.220."]

    async def is_vpn_or_proxy(self, ip: str) -> Tuple[bool, str]:
        """Returns (is_vpn, reason)."""
        for prefix in self._VPN_PREFIXES:
            if ip.startswith(prefix):
                return True, f"IP in known VPN range: {prefix}*"
        for prefix in self._TOR_PREFIXES:
            if ip.startswith(prefix):
                return True, f"IP is a Tor exit node: {prefix}*"

        # Optional: call external IP reputation API
        # result = await self._call_ipinfo(ip)
        # if result.get("vpn") or result.get("proxy"):
        #     return True, result.get("reason", "external-check")

        return False, ""

    async def _call_ipinfo(self, ip: str) -> Dict[str, Any]:
        """Stub for ipinfo.io paid API."""
        return {}


# ---------------------------------------------------------------------------
# IP Geolocation Provider
# ---------------------------------------------------------------------------


class IPGeolocationProvider:
    """
    Resolves IP address to geographic location.
    In production: MaxMind GeoIP2 or similar.
    """

    async def lookup(self, ip: str) -> Optional[LocationFix]:
        """
        Returns LocationFix if IP resolves to Brazil, None otherwise.
        Stub returns a São Paulo location for non-reserved IPs.
        """
        # Reserved / private IPs -- can't geolocate
        if ip.startswith(("127.", "10.", "192.168.", "172.16.", "0.")):
            return LocationFix(
                method=LocationMethod.IP,
                latitude=-23.5505,
                longitude=-46.6333,
                accuracy_metres=50000.0,
                country_code="BR",
                state_code="SP",
                municipality="São Paulo",
                region=BrazilianRegion.SUDESTE,
                vpn_detected=False,
                confidence=0.55,
            )

        # Stub: treat all public IPs as Brazil/SP in demo
        return LocationFix(
            method=LocationMethod.IP,
            latitude=-23.5505,
            longitude=-46.6333,
            accuracy_metres=50000.0,
            country_code="BR",
            state_code="SP",
            municipality="São Paulo",
            region=BrazilianRegion.SUDESTE,
            vpn_detected=False,
            confidence=0.55,
        )


# ---------------------------------------------------------------------------
# WiFi Geolocation Provider
# ---------------------------------------------------------------------------


class WiFiGeolocationProvider:
    """
    Uses observed WiFi BSSID scan to estimate location.
    In production: Google Geolocation API or Apple Location Services.
    """

    async def locate(
        self, networks: List[Dict[str, Any]]
    ) -> Optional[LocationFix]:
        """Stub: returns a Rio de Janeiro fix if >= 2 networks visible."""
        if len(networks) < 2:
            return None
        return LocationFix(
            method=LocationMethod.WIFI,
            latitude=-22.9068,
            longitude=-43.1729,
            accuracy_metres=150.0,
            country_code="BR",
            state_code="RJ",
            municipality="Rio de Janeiro",
            region=BrazilianRegion.SUDESTE,
            vpn_detected=False,
            confidence=0.80,
        )


# ---------------------------------------------------------------------------
# Core Geolocation Service
# ---------------------------------------------------------------------------


class GeolocationService:
    """
    Orchestrates the GPS → WiFi → IP → block fallback chain,
    enforces Brazil residency, and manages per-session re-verification.
    """

    def __init__(
        self,
        vpn_detector: VPNDetector,
        ip_provider: IPGeolocationProvider,
        wifi_provider: WiFiGeolocationProvider,
    ) -> None:
        self.vpn = vpn_detector
        self.ip_geo = ip_provider
        self.wifi_geo = wifi_provider
        self._sessions: Dict[str, GeoSession] = {}
        self._lock = asyncio.Lock()

    async def verify(
        self, req: GeoVerificationRequest
    ) -> GeoVerificationResponse:
        """
        Main entry point.  Runs the full fallback chain:
          1. If GPS coordinates provided and accurate → use GPS
          2. Elif WiFi networks provided → use WiFi
          3. Fallback to IP geolocation
          4. VPN check on all paths
          5. Brazil boundary check
          6. Update session state
        """
        verification_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # VPN / proxy check first (applies regardless of location method)
        is_vpn, vpn_reason = await self.vpn.is_vpn_or_proxy(req.ip_address)
        if is_vpn:
            logger.warning(
                "geo_vpn_detected",
                player_id=req.player_id,
                ip=req.ip_address,
                reason=vpn_reason,
            )
            await self._update_session(
                req.session_id, req.player_id, GeoStatus.VPN_DETECTED, None
            )
            raise VPNDetectedError(f"VPN/proxy detected: {vpn_reason}")

        fix = await self._resolve_location(req)

        if fix is None:
            await self._update_session(
                req.session_id, req.player_id, GeoStatus.BLOCKED, None
            )
            raise GeoError("Unable to determine location via any method")

        # Cross-check GPS/WiFi fixes against the IP-derived country. GPS
        # coordinates are client-reported and can be spoofed by rooted
        # devices or emulators; the network-layer IP is a second,
        # independent signal that a spoofed GPS reading alone cannot
        # satisfy. A mismatch is treated as location fraud, not merely a
        # boundary miss.
        if fix.method != LocationMethod.IP:
            ip_fix = await self.ip_geo.lookup(req.ip_address)
            if ip_fix is None or ip_fix.country_code != "BR":
                logger.warning(
                    "geo_gps_ip_mismatch",
                    player_id=req.player_id,
                    session_id=req.session_id,
                    gps_country=fix.country_code,
                    ip_country=ip_fix.country_code if ip_fix else None,
                )
                await self._update_session(
                    req.session_id, req.player_id, GeoStatus.OUTSIDE_BRAZIL, fix
                )
                raise LocationOutsideBrazilError(
                    "Reported location does not match the network-derived location"
                )

        # Brazil check
        if fix.country_code != "BR":
            await self._update_session(
                req.session_id, req.player_id, GeoStatus.OUTSIDE_BRAZIL, fix
            )
            raise LocationOutsideBrazilError(
                f"Player location is outside Brazil (country={fix.country_code})"
            )

        if not self._within_brazil_bbox(fix.latitude, fix.longitude):
            await self._update_session(
                req.session_id, req.player_id, GeoStatus.OUTSIDE_BRAZIL, fix
            )
            raise LocationOutsideBrazilError(
                "Coordinates fall outside Brazil geographic boundary"
            )

        next_due = now + timedelta(seconds=REVERIFICATION_INTERVAL_SECONDS)
        await self._update_session(
            req.session_id, req.player_id, GeoStatus.VERIFIED, fix, next_due
        )

        logger.info(
            "geo_verified",
            player_id=req.player_id,
            session_id=req.session_id,
            method=fix.method.value,
            state=fix.state_code,
            accuracy=fix.accuracy_metres,
        )

        return GeoVerificationResponse(
            verification_id=verification_id,
            session_id=req.session_id,
            player_id=req.player_id,
            status=GeoStatus.VERIFIED,
            method_used=fix.method,
            country_code=fix.country_code,
            state_code=fix.state_code,
            municipality=fix.municipality,
            latitude=fix.latitude,
            longitude=fix.longitude,
            accuracy_metres=fix.accuracy_metres,
            vpn_detected=False,
            verified_at=now,
            next_verification_due=next_due,
            message=f"Location verified: {fix.municipality or fix.state_code}, Brazil",
        )

    async def _resolve_location(
        self, req: GeoVerificationRequest
    ) -> Optional[LocationFix]:
        """GPS → WiFi → IP fallback chain."""

        # 1. GPS
        if req.gps and req.method == LocationMethod.GPS:
            if req.gps.accuracy_metres <= MIN_GPS_ACCURACY_METRES:
                fix = self._gps_to_fix(req.gps)
                if fix:
                    return fix
            else:
                logger.info(
                    "geo_gps_accuracy_insufficient",
                    accuracy=req.gps.accuracy_metres,
                    threshold=MIN_GPS_ACCURACY_METRES,
                )

        # 2. WiFi
        if req.wifi and len(req.wifi.networks) >= 2:
            fix = await self.wifi_geo.locate(req.wifi.networks)
            if fix:
                return fix

        # 3. IP fallback
        fix = await self.ip_geo.lookup(req.ip_address)
        if fix:
            logger.info(
                "geo_ip_fallback_used",
                player_id=req.player_id,
                ip=req.ip_address,
                accuracy=fix.accuracy_metres,
            )
            return fix

        return None

    def _gps_to_fix(self, gps: GPSCoordinates) -> Optional[LocationFix]:
        """Converts a GPS reading to a LocationFix with state mapping."""
        state = self._coords_to_state(gps.latitude, gps.longitude)
        return LocationFix(
            method=LocationMethod.GPS,
            latitude=gps.latitude,
            longitude=gps.longitude,
            accuracy_metres=gps.accuracy_metres,
            country_code="BR" if self._within_brazil_bbox(gps.latitude, gps.longitude) else "XX",
            state_code=state,
            municipality=None,
            region=None,
            vpn_detected=False,
            confidence=min(0.99, 1000 / max(gps.accuracy_metres, 1)),
        )

    def _within_brazil_bbox(self, lat: float, lon: float) -> bool:
        bb = BRAZIL_BBOX
        return bb["lat_min"] <= lat <= bb["lat_max"] and bb["lon_min"] <= lon <= bb["lon_max"]

    def _coords_to_state(self, lat: float, lon: float) -> Optional[str]:
        """
        Simplified coordinate-to-state mapping.
        In production use a PostGIS polygon lookup or a shapefile.
        """
        if -23.9 <= lat <= -21.0 and -48.0 <= lon <= -43.0:
            return "SP"
        if -23.1 <= lat <= -20.9 and -44.0 <= lon <= -40.9:
            return "RJ"
        if -20.5 <= lat <= -14.5 and -51.0 <= lon <= -39.0:
            return "MG"
        if -30.5 <= lat <= -22.5 and -54.5 <= lon <= -47.5:
            return "PR"
        if -33.9 <= lat <= -24.9 and -54.9 <= lon <= -47.0:
            return "RS"
        return None

    async def _update_session(
        self,
        session_id: str,
        player_id: str,
        status: GeoStatus,
        fix: Optional[LocationFix],
        next_due: Optional[datetime] = None,
    ) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            now = datetime.now(timezone.utc)
            if not session:
                session = GeoSession(
                    session_id=session_id,
                    player_id=player_id,
                    status=status,
                    last_fix=fix,
                    verified_at=now if status == GeoStatus.VERIFIED else None,
                    next_due=next_due,
                )
            else:
                session.status = status
                session.last_fix = fix
                if status == GeoStatus.VERIFIED:
                    session.verified_at = now
                    session.next_due = next_due
                    session.verification_count += 1
            session.audit_trail.append(
                {
                    "event": "geo_check",
                    "status": status.value,
                    "method": fix.method.value if fix else "none",
                    "state": fix.state_code if fix else None,
                    "timestamp": now.isoformat(),
                }
            )
            self._sessions[session_id] = session

    async def get_session_status(self, session_id: str) -> Optional[GeoSession]:
        return self._sessions.get(session_id)

    async def is_session_valid(self, session_id: str) -> bool:
        """Returns True if session has a current, non-expired geo verification."""
        session = self._sessions.get(session_id)
        if not session or session.status != GeoStatus.VERIFIED:
            return False
        if session.next_due and datetime.now(timezone.utc) > session.next_due:
            session.status = GeoStatus.EXPIRED
            return False
        return True


# ---------------------------------------------------------------------------
# 30-Minute Re-verification Monitor
# ---------------------------------------------------------------------------


class GeoReverificationMonitor:
    """
    Background monitor that flags expired geo sessions.
    The frontend client is notified via WebSocket to re-submit location.
    """

    def __init__(self, service: GeolocationService) -> None:
        self.service = service
        self._task: Optional[asyncio.Task] = None
        self._websockets: Dict[str, WebSocket] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        logger.info("geo_monitor_started", interval_seconds=60)

    async def _run(self) -> None:
        while True:
            await self._check_expirations()
            await asyncio.sleep(60)

    async def _check_expirations(self) -> None:
        now = datetime.now(timezone.utc)
        expired_sessions = []
        for session_id, session in self.service._sessions.items():
            if (
                session.status == GeoStatus.VERIFIED
                and session.next_due
                and now >= session.next_due
            ):
                session.status = GeoStatus.EXPIRED
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            logger.info("geo_session_expired", session_id=session_id)
            ws = self._websockets.get(session_id)
            if ws:
                try:
                    await ws.send_json({
                        "type": "geo_reverification_required",
                        "session_id": session_id,
                        "message": "Localização expirou. Re-verificação necessária.",
                    })
                except Exception:
                    pass

    def register_websocket(self, session_id: str, ws: WebSocket) -> None:
        self._websockets[session_id] = ws

    def deregister_websocket(self, session_id: str) -> None:
        self._websockets.pop(session_id, None)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

geo_service: Optional[GeolocationService] = None
monitor: Optional[GeoReverificationMonitor] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global geo_service, monitor
    geo_service = GeolocationService(
        vpn_detector=VPNDetector(),
        ip_provider=IPGeolocationProvider(),
        wifi_provider=WiFiGeolocationProvider(),
    )
    monitor = GeoReverificationMonitor(geo_service)
    monitor.start()
    logger.info("geo_service_started")
    yield
    monitor.stop()
    logger.info("geo_service_shutdown")


app = FastAPI(
    title="Geolocation Verification Service",
    description="Brazil-only location enforcement for betting platform",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/v1/geo/verify", response_model=GeoVerificationResponse)
async def verify_location(
    req: GeoVerificationRequest, request: Request
) -> GeoVerificationResponse:
    """Verify player location. Enforces Brazil-only access.

    The IP used for VPN/geo checks is always the real peer IP from the
    ASGI connection, never the client-supplied `req.ip_address`. This
    service is not documented as sitting behind a trusted reverse proxy,
    so no `X-Forwarded-For`-style header is honoured; if this deploys
    behind one, that proxy contract must be documented here first.
    """
    req.ip_address = request.client.host if request.client else "0.0.0.0"
    return await geo_service.verify(req)  # type: ignore[union-attr]


@app.get("/v1/geo/sessions/{session_id}", response_model=Dict[str, Any])
async def get_session(session_id: str) -> Dict[str, Any]:
    """Get geo verification status for a session."""
    session = await geo_service.get_session_status(session_id)  # type: ignore[union-attr]
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "player_id": session.player_id,
        "status": session.status.value,
        "state_code": session.last_fix.state_code if session.last_fix else None,
        "verified_at": session.verified_at.isoformat() if session.verified_at else None,
        "next_due": session.next_due.isoformat() if session.next_due else None,
        "verification_count": session.verification_count,
        "is_valid": await geo_service.is_session_valid(session_id),  # type: ignore[union-attr]
    }


@app.websocket("/v1/geo/ws/{session_id}")
async def geo_websocket(websocket: WebSocket, session_id: str) -> None:
    """WebSocket for real-time geo reverification notifications."""
    await websocket.accept()
    monitor.register_websocket(session_id, websocket)  # type: ignore[union-attr]
    try:
        await websocket.send_json({"type": "connected", "session_id": session_id})
        while True:
            data = await websocket.receive_text()
            # Client can send heartbeat or updated location
            logger.info("geo_ws_message", session_id=session_id, data=data[:100])
    except WebSocketDisconnect:
        logger.info("geo_ws_disconnected", session_id=session_id)
    finally:
        monitor.deregister_websocket(session_id)  # type: ignore[union-attr]


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "geolocation-service"}


if __name__ == "__main__":
    uvicorn.run("geolocation_service:app", host="0.0.0.0", port=8004, reload=False)
