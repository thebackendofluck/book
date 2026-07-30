# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""push_token_manager.py — APNs/FCM push token lifecycle management.

Tracks device push tokens per player, records last-seen, and invalidates
tokens the provider reports as stale (APNs 410 Unregistered / BadDeviceToken,
FCM UNREGISTERED / INVALID_ARGUMENT). Companion module for Chapter 33c.

Invalidation is what keeps a sending pipeline healthy: a token the OS has
rotated will silently black-hole every push until it is removed, and providers
throttle senders with high invalid-token rates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Platform(str, Enum):
    APNS = "apns"
    FCM = "fcm"


@dataclass
class DeviceToken:
    player_id: str
    platform: Platform
    token: str
    registered_at: datetime
    last_seen_at: datetime
    active: bool = True


# Provider responses that mean "stop sending to this token".
_INVALIDATING = {
    "Unregistered",  # APNs 410
    "BadDeviceToken",  # APNs 400
    "UNREGISTERED",  # FCM
    "INVALID_ARGUMENT",  # FCM (malformed token)
}


@dataclass
class PushTokenManager:
    _tokens: dict[str, DeviceToken] = field(default_factory=dict)

    def register(self, player_id: str, platform: Platform, token: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        existing = self._tokens.get(token)
        if existing is not None:
            existing.last_seen_at = now
            existing.active = True
            return
        self._tokens[token] = DeviceToken(player_id, platform, token, now, now)

    def active_tokens(self, player_id: str) -> list[DeviceToken]:
        return [t for t in self._tokens.values() if t.player_id == player_id and t.active]

    def touch(self, token: str, now: datetime | None = None) -> None:
        entry = self._tokens.get(token)
        if entry is not None:
            entry.last_seen_at = now or datetime.now(timezone.utc)

    def handle_provider_response(self, token: str, reason: str) -> bool:
        """Invalidate a token if the provider reason indicates it is dead.

        Returns True if the token was invalidated.
        """
        if reason in _INVALIDATING:
            entry = self._tokens.get(token)
            if entry is not None:
                entry.active = False
            return True
        return False

    def prune(self) -> int:
        """Drop invalidated tokens entirely. Returns the number removed."""
        dead = [tok for tok, t in self._tokens.items() if not t.active]
        for tok in dead:
            del self._tokens[tok]
        return len(dead)
