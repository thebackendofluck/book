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
GeoComply API Integration Mock
================================
Illustrates the full GeoComply SDK + server-side verification flow used by US-licensed
iGaming operators (NJ, PA, MI, WV, CT, DE, RI).

In production:
- The GeoComply SDK runs on the CLIENT (JavaScript SDK for web, native SDK for iOS/Android).
  It collects GPS, WiFi BSSID list, cell tower IDs, and IP metadata, encrypts the bundle,
  and returns an opaque "GeoPacket" string to the frontend.
- The frontend sends the GeoPacket to your backend /geo/verify endpoint.
- Your backend forwards the GeoPacket to the GeoComply Server-Side API for decryption and
  validation. GeoComply returns a JSON result with state determination, confidence score,
  and a signed geo-lease token.
- Your backend stores the geo-lease and enforces re-verification every 11-30 minutes
  depending on state regulator requirements.

This file mocks the server-side portion (the only part you write).
The GeoComply client SDK is provided by GeoComply under a licensing agreement.

References:
  GeoComply Operator Guide (NDA-protected): https://geocomply.com/igaming/
  NJ DGE Technical Standard N-5 (geolocation): https://www.nj.gov/oag/ge/
  PA iGCB Temporary Regulations 58 Pa. Code §§ 1200a et seq.

Usage:
  python geocomply-integration.py --player player-123 --packet <GeoPacket> --state NJ
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import requests  # pip install requests

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEOCOMPLY_API_URL = os.environ.get(
    "GEOCOMPLY_API_URL",
    "https://geocomply-us.example.com/api/v2",  # Replace with production endpoint
)
GEOCOMPLY_LICENSE_KEY = os.environ.get("GEOCOMPLY_LICENSE_KEY", "mock-license-key")
GEOCOMPLY_API_SECRET = os.environ.get("GEOCOMPLY_API_SECRET", "mock-secret")

# Re-verification intervals by state (seconds).
# NJ DGE requires re-check every 11 minutes (660s). PA iGCB: 15 min (900s).
# MI MGCB: 30 min (1800s). WV LCB: 15 min (900s). CT DCP: 15 min (900s).
GEO_LEASE_TTL: dict[str, int] = {
    "NJ": 660,    # 11 minutes — DGE Technical Standard N-5
    "PA": 900,    # 15 minutes — 58 Pa. Code § 1200a.6
    "MI": 1800,   # 30 minutes — MGCB Rule 432.654
    "WV": 900,    # 15 minutes — WVLCB Rule 179CSR8
    "CT": 900,    # 15 minutes — DCP Technical Standard 3.1
    "DE": 900,    # 15 minutes — DLC Rule 7.1
    "RI": 900,    # 15 minutes — DBR Rule 3.0
}

# States with active iGaming licenses (casino games) as of 2025
LICENSED_CASINO_STATES: set[str] = {
    "NJ",  # New Jersey — DGE, live since Nov 2013
    "PA",  # Pennsylvania — PGCB/iGCB, live since Jul 2019
    "MI",  # Michigan — MGCB, live since Jan 2021
    "WV",  # West Virginia — WVLCB, live since Jul 2020
    "CT",  # Connecticut — DCP + CGCC, live since Oct 2021
    "DE",  # Delaware — DLC, live since Nov 2012
    "RI",  # Rhode Island — DBR, live since Mar 2023 (sports + limited casino)
}

# States with MSIGA (Multi-State Internet Gambling Agreement) for shared poker pools
MSIGA_POKER_STATES: set[str] = {
    "NV",  # Nevada — original signatory
    "DE",  # Delaware — original signatory
    "NJ",  # New Jersey — joined 2018
    "MI",  # Michigan — joined 2022
    "PA",  # Pennsylvania — observer; pending full participation
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class GeoVerificationStatus(str, Enum):
    APPROVED = "APPROVED"           # Player is within a licensed state
    DECLINED = "DECLINED"           # Player is outside licensed jurisdiction
    UNDETERMINED = "UNDETERMINED"   # Insufficient signal quality
    BORDER_ZONE = "BORDER_ZONE"     # Player near state line, secondary check needed
    VPN_DETECTED = "VPN_DETECTED"   # Anonymizer/proxy in use
    LEASE_EXPIRED = "LEASE_EXPIRED" # Geo-lease TTL exceeded


@dataclass
class GeoLease:
    """
    A time-limited cryptographic token confirming a player's physical location.
    Issued by GeoComply after successful verification. Must be renewed before expiry.
    """
    lease_id: str
    player_id: str
    session_id: str
    state_code: str          # Two-letter US state code
    latitude: float
    longitude: float
    accuracy_meters: int     # GPS accuracy radius
    issued_at: float         # Unix timestamp
    expires_at: float        # Unix timestamp
    confidence_score: float  # 0.0 – 1.0 (GeoComply internal score)
    is_valid: bool = True
    signature: str = ""      # HMAC-SHA256 over lease fields (signed by GeoComply)

    @property
    def ttl_remaining(self) -> int:
        """Seconds until this lease expires."""
        return max(0, int(self.expires_at - time.time()))

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GeoVerificationResult:
    """Full result returned to your application after server-side GeoComply verification."""
    verification_id: str
    player_id: str
    session_id: str
    status: GeoVerificationStatus
    state_code: Optional[str]        # None if DECLINED or VPN_DETECTED
    is_licensed_state: bool
    is_msiga_poker_state: bool
    lease: Optional[GeoLease]
    decline_reason: Optional[str]
    raw_geocomply_response: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_audit_log(self) -> dict:
        return {
            "event": "GEO_VERIFICATION",
            "verification_id": self.verification_id,
            "player_id": self.player_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "state_code": self.state_code,
            "is_licensed_state": self.is_licensed_state,
            "is_msiga_poker_state": self.is_msiga_poker_state,
            "lease_id": self.lease.lease_id if self.lease else None,
            "lease_expires_at": self.lease.expires_at if self.lease else None,
            "decline_reason": self.decline_reason,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# GeoComply client
# ---------------------------------------------------------------------------

class GeoComplyClient:
    """
    Server-side GeoComply client.
    Forwards client-submitted GeoPackets to the GeoComply verification API and
    parses the result into a GeoLease.

    The GeoPacket is an encrypted blob produced by the GeoComply client SDK running
    in the player's browser or mobile app. Your backend never reads its contents —
    only GeoComply's servers can decrypt it.
    """

    def __init__(
        self,
        api_url: str = GEOCOMPLY_API_URL,
        license_key: str = GEOCOMPLY_LICENSE_KEY,
        api_secret: str = GEOCOMPLY_API_SECRET,
        timeout: int = 10,
    ) -> None:
        self._api_url = api_url
        self._license_key = license_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "X-GeoComply-License": license_key,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_geopacket(
        self,
        geopacket: str,
        player_id: str,
        session_id: str,
        product_type: str = "CASINO",  # CASINO | SPORTS | POKER
    ) -> GeoVerificationResult:
        """
        Submit a GeoPacket for server-side verification.

        Args:
            geopacket:    Encrypted blob from the GeoComply client SDK.
            player_id:    Your platform's player identifier.
            session_id:   Current game/betting session ID.
            product_type: Product type — affects which state licenses are checked.

        Returns:
            GeoVerificationResult with lease (if approved) or decline reason.
        """
        verification_id = str(uuid.uuid4())
        payload = self._build_payload(geopacket, player_id, session_id, product_type)

        logger.info(
            "Sending GeoPacket for verification player=%s session=%s",
            player_id,
            session_id,
        )

        try:
            raw_response = self._call_api("/verify", payload)
        except requests.exceptions.Timeout:
            logger.error("GeoComply API timeout player=%s", player_id)
            return self._failopen_result(verification_id, player_id, session_id)
        except requests.exceptions.RequestException as exc:
            logger.error("GeoComply API error player=%s: %s", player_id, exc)
            return self._failclosed_result(
                verification_id, player_id, session_id, str(exc)
            )

        return self._parse_response(
            raw_response, verification_id, player_id, session_id
        )

    def verify_lease(self, lease: GeoLease) -> bool:
        """
        Verify that an existing geo-lease is still valid (not expired, not revoked).
        Called every GEO_LEASE_TTL[state] seconds during an active session.

        In production, GeoComply also provides a real-time lease revocation webhook.
        Subscribe to it and maintain a local revocation cache to avoid polling.
        """
        if lease.is_expired:
            logger.warning(
                "Geo-lease expired lease_id=%s player=%s",
                lease.lease_id,
                lease.player_id,
            )
            return False

        # Optionally hit the GeoComply lease status endpoint for revocation check
        try:
            response = self._call_api(f"/leases/{lease.lease_id}/status", method="GET")
            is_active: bool = response.get("status") == "ACTIVE"
            if not is_active:
                logger.warning(
                    "Geo-lease revoked lease_id=%s player=%s reason=%s",
                    lease.lease_id,
                    lease.player_id,
                    response.get("revoke_reason", "UNKNOWN"),
                )
            return is_active
        except requests.exceptions.RequestException:
            # On API failure use local TTL only — do not fail-close on lease checks
            logger.warning(
                "GeoComply lease check failed, trusting local TTL lease_id=%s",
                lease.lease_id,
            )
            return not lease.is_expired

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        geopacket: str,
        player_id: str,
        session_id: str,
        product_type: str,
    ) -> dict:
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        signature = self._sign_request(geopacket, timestamp, nonce)
        return {
            "geopacket": geopacket,
            "player_id": player_id,
            "session_id": session_id,
            "product_type": product_type,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
        }

    def _sign_request(self, geopacket: str, timestamp: str, nonce: str) -> str:
        """HMAC-SHA256 request signing — prevents geopacket replay attacks."""
        message = f"{geopacket}:{timestamp}:{nonce}".encode()
        return hmac.new(
            self._api_secret.encode(), message, hashlib.sha256
        ).hexdigest()

    def _call_api(
        self, path: str, payload: dict | None = None, method: str = "POST"
    ) -> dict:
        url = f"{self._api_url}{path}"
        if method == "GET":
            resp = self._session.get(url, timeout=self._timeout)
        else:
            resp = self._session.post(url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _parse_response(
        self,
        raw: dict,
        verification_id: str,
        player_id: str,
        session_id: str,
    ) -> GeoVerificationResult:
        gc_status: str = raw.get("status", "UNDETERMINED")
        state_code: str | None = raw.get("state_code")
        confidence: float = float(raw.get("confidence_score", 0.0))
        decline_reason: str | None = raw.get("decline_reason")

        # Map GeoComply status to our enum
        status_map = {
            "APPROVED": GeoVerificationStatus.APPROVED,
            "DECLINED": GeoVerificationStatus.DECLINED,
            "UNDETERMINED": GeoVerificationStatus.UNDETERMINED,
            "BORDER_ZONE": GeoVerificationStatus.BORDER_ZONE,
            "VPN_DETECTED": GeoVerificationStatus.VPN_DETECTED,
        }
        status = status_map.get(gc_status, GeoVerificationStatus.UNDETERMINED)

        lease: GeoLease | None = None
        if status == GeoVerificationStatus.APPROVED and state_code:
            ttl = GEO_LEASE_TTL.get(state_code, 900)
            now = time.time()
            lease = GeoLease(
                lease_id=raw.get("lease_id", str(uuid.uuid4())),
                player_id=player_id,
                session_id=session_id,
                state_code=state_code,
                latitude=float(raw.get("latitude", 0.0)),
                longitude=float(raw.get("longitude", 0.0)),
                accuracy_meters=int(raw.get("accuracy_meters", 500)),
                issued_at=now,
                expires_at=now + ttl,
                confidence_score=confidence,
                signature=raw.get("lease_signature", ""),
            )

        return GeoVerificationResult(
            verification_id=verification_id,
            player_id=player_id,
            session_id=session_id,
            status=status,
            state_code=state_code,
            is_licensed_state=state_code in LICENSED_CASINO_STATES,
            is_msiga_poker_state=state_code in MSIGA_POKER_STATES,
            lease=lease,
            decline_reason=decline_reason,
            raw_geocomply_response=raw,
        )

    def _failopen_result(
        self, verification_id: str, player_id: str, session_id: str
    ) -> GeoVerificationResult:
        """
        Fail-open on TIMEOUT only — allow game to continue for a grace period.
        Most state regulations allow a 30–60 second grace window on API timeout
        before terminating the session. Implement the grace timer at the caller level.
        Document this behaviour in your System of Internal Controls (SIC) submission.
        """
        logger.warning(
            "GeoComply API timeout — FAIL-OPEN player=%s session=%s", player_id, session_id
        )
        return GeoVerificationResult(
            verification_id=verification_id,
            player_id=player_id,
            session_id=session_id,
            status=GeoVerificationStatus.UNDETERMINED,
            state_code=None,
            is_licensed_state=False,
            is_msiga_poker_state=False,
            lease=None,
            decline_reason="GEO_API_TIMEOUT",
        )

    def _failclosed_result(
        self, verification_id: str, player_id: str, session_id: str, reason: str
    ) -> GeoVerificationResult:
        """Fail-closed on non-timeout errors (connection refused, auth failure, etc.)."""
        return GeoVerificationResult(
            verification_id=verification_id,
            player_id=player_id,
            session_id=session_id,
            status=GeoVerificationStatus.DECLINED,
            state_code=None,
            is_licensed_state=False,
            is_msiga_poker_state=False,
            lease=None,
            decline_reason=f"GEO_API_ERROR: {reason}",
        )


# ---------------------------------------------------------------------------
# Mock for local development / CI
# ---------------------------------------------------------------------------

class GeoComplyMockClient(GeoComplyClient):
    """
    Mock implementation that never calls the GeoComply API.
    Use in unit tests and local development.
    Set GEOCOMPLY_MOCK=1 to activate automatically.

    The mock approves any player whose player_id starts with 'nj-' (returns NJ),
    'pa-' (returns PA), 'mi-' (returns MI), 'wv-' (returns WV), 'ct-' (returns CT).
    All others are declined (simulates out-of-state or VPN player).
    """

    _STATE_PREFIX_MAP: dict[str, str] = {
        "nj-": "NJ",
        "pa-": "PA",
        "mi-": "MI",
        "wv-": "WV",
        "ct-": "CT",
        "de-": "DE",
        "ri-": "RI",
        "nv-": "NV",
    }

    def verify_geopacket(
        self,
        geopacket: str,
        player_id: str,
        session_id: str,
        product_type: str = "CASINO",
    ) -> GeoVerificationResult:
        verification_id = str(uuid.uuid4())
        state_code: str | None = None
        for prefix, state in self._STATE_PREFIX_MAP.items():
            if player_id.lower().startswith(prefix):
                state_code = state
                break

        if state_code is None:
            return GeoVerificationResult(
                verification_id=verification_id,
                player_id=player_id,
                session_id=session_id,
                status=GeoVerificationStatus.DECLINED,
                state_code=None,
                is_licensed_state=False,
                is_msiga_poker_state=False,
                lease=None,
                decline_reason="PLAYER_OUTSIDE_LICENSED_STATE",
            )

        ttl = GEO_LEASE_TTL.get(state_code, 900)
        now = time.time()
        lease = GeoLease(
            lease_id=str(uuid.uuid4()),
            player_id=player_id,
            session_id=session_id,
            state_code=state_code,
            latitude=40.0583,    # Approximate NJ lat
            longitude=-74.4057,
            accuracy_meters=50,
            issued_at=now,
            expires_at=now + ttl,
            confidence_score=0.99,
            signature="mock-signature",
        )
        return GeoVerificationResult(
            verification_id=verification_id,
            player_id=player_id,
            session_id=session_id,
            status=GeoVerificationStatus.APPROVED,
            state_code=state_code,
            is_licensed_state=state_code in LICENSED_CASINO_STATES,
            is_msiga_poker_state=state_code in MSIGA_POKER_STATES,
            lease=lease,
            decline_reason=None,
        )

    def verify_lease(self, lease: GeoLease) -> bool:
        return not lease.is_expired


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_geocomply_client() -> GeoComplyClient:
    if os.environ.get("GEOCOMPLY_MOCK", "0") == "1":
        logger.info("GeoComply mock mode enabled")
        return GeoComplyMockClient()
    return GeoComplyClient()


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GeoComply server-side verification test")
    parser.add_argument("--player", required=True, help="Player ID (prefix nj-/pa-/mi- for mock)")
    parser.add_argument("--packet", default="mock-geopacket", help="GeoPacket from client SDK")
    parser.add_argument("--session", default=str(uuid.uuid4()), help="Session ID")
    parser.add_argument("--state", default="NJ", help="Expected state (informational only)")
    parser.add_argument("--mock", action="store_true", help="Force mock mode")
    args = parser.parse_args()

    if args.mock:
        os.environ["GEOCOMPLY_MOCK"] = "1"

    client = get_geocomply_client()
    result = client.verify_geopacket(args.packet, args.player, args.session)

    print("\n=== GeoComply Verification Result ===")
    print(json.dumps(result.to_audit_log(), indent=2))

    if result.lease:
        print(f"\nGeo-lease TTL: {result.lease.ttl_remaining}s")
        print(f"Lease expires: {datetime.fromtimestamp(result.lease.expires_at, tz=timezone.utc).isoformat()}")
        print(f"State: {result.lease.state_code}")
        print(f"Confidence: {result.lease.confidence_score:.2%}")

    if result.status == GeoVerificationStatus.APPROVED:
        print(f"\n[APPROVED] Player may access {result.state_code} licensed games.")
    else:
        print(f"\n[BLOCKED] {result.status.value}: {result.decline_reason}")
