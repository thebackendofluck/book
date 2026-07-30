# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Casino RTC (Real-Time Communication) SDK — Python implementation.

WebSocket-based real-time communication for casino platforms.
Handles authenticated connections, HMAC signing, and event streaming.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx
import websockets

logger = logging.getLogger(__name__)


@dataclass
class RTCConfig:
    base_url: str
    api_key: str
    secret_key: str
    timeout_s: float = 30.0
    reconnect_attempts: int = 3
    reconnect_delay_s: float = 2.0


@dataclass
class RTCMessage:
    event_type: str
    payload: dict
    timestamp: float = field(default_factory=time.time)
    signature: str = ""


class CasinoRTC:
    """Real-time communication client for casino platforms.

    Supports WebSocket connections with HMAC-SHA256 authentication,
    automatic reconnection, and event-based message handling.
    """

    def __init__(self, config: RTCConfig) -> None:
        self.config = config
        self._ws: Optional[websockets.ClientConnection] = None  # type: ignore[attr-defined]
        self._handlers: dict[str, list[Callable]] = {}
        self._running = False

    def _sign(self, message: str) -> str:
        """Generate HMAC-SHA256 signature for message authentication."""
        return hmac.new(
            self.config.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers with timestamp and signature."""
        timestamp = str(int(time.time()))
        signature = self._sign(f"{self.config.api_key}:{timestamp}")
        return {
            "X-API-Key": self.config.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    async def connect(self) -> None:
        """Establish authenticated WebSocket connection."""
        ws_url = self.config.base_url.replace("http", "ws") + "/ws"
        headers = self._build_auth_headers()

        for attempt in range(self.config.reconnect_attempts):
            try:
                self._ws = await websockets.connect(
                    ws_url,
                    extra_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                )
                self._running = True
                return
            except (ConnectionError, OSError) as e:
                if attempt < self.config.reconnect_attempts - 1:
                    await asyncio.sleep(self.config.reconnect_delay_s * (attempt + 1))
                else:
                    raise ConnectionError(
                        f"Failed to connect after {self.config.reconnect_attempts} attempts: {e}"
                    )

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    def on(self, event_type: str, handler: Callable) -> None:
        """Register event handler."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def send(self, event_type: str, payload: dict) -> None:
        """Send signed message to server."""
        if not self._ws:
            raise RuntimeError("Not connected")

        message = RTCMessage(event_type=event_type, payload=payload)
        raw = json.dumps({"type": event_type, "data": payload, "ts": message.timestamp})
        message.signature = self._sign(raw)

        await self._ws.send(json.dumps({
            "type": event_type,
            "data": payload,
            "ts": message.timestamp,
            "sig": message.signature,
        }))

    def _verify_inbound(self, msg: dict) -> bool:
        """Verify the HMAC-SHA256 signature on an inbound server message.

        Mirrors the signing scheme used by `send()`: the signature covers
        the exact {"type", "data", "ts"} payload, and is compared using a
        constant-time comparison to avoid timing side channels.
        """
        sig = msg.get("sig")
        if not sig or not isinstance(sig, str):
            return False

        raw = json.dumps({
            "type": msg.get("type"),
            "data": msg.get("data", {}),
            "ts": msg.get("ts"),
        })
        expected = self._sign(raw)
        return hmac.compare_digest(expected, sig)

    async def listen(self) -> None:
        """Listen for incoming messages and dispatch to handlers."""
        if not self._ws:
            raise RuntimeError("Not connected")

        while self._running:
            try:
                raw = await self._ws.recv()
                msg = json.loads(raw)

                if not self._verify_inbound(msg):
                    logger.warning(
                        "Dropping inbound RTC message with missing/invalid signature: type=%r",
                        msg.get("type", "unknown"),
                    )
                    continue

                event_type = msg.get("type", "unknown")

                for handler in self._handlers.get(event_type, []):
                    if asyncio.iscoroutinefunction(handler):
                        await handler(msg.get("data", {}))
                    else:
                        handler(msg.get("data", {}))

            except websockets.ConnectionClosed:
                if self._running:
                    await self.connect()  # Auto-reconnect
                else:
                    break

    # --- REST API methods ---

    async def get_active_sessions(self, game_id: str) -> list[dict]:
        """Get active player sessions for a game via REST API."""
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            resp = await client.get(
                f"{self.config.base_url}/api/sessions/{game_id}",
                headers=self._build_auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def broadcast(self, channel: str, event: str, data: dict) -> None:
        """Broadcast event to all subscribers of a channel via REST API."""
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            resp = await client.post(
                f"{self.config.base_url}/api/broadcast",
                headers=self._build_auth_headers(),
                json={"channel": channel, "event": event, "data": data},
            )
            resp.raise_for_status()
