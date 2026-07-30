# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
customer_event.py -- Customer event domain model for the iGaming CDP.

Every player touchpoint produces a CustomerEvent:
  page_view, bet_placed, deposit, withdrawal, login, logout,
  bonus_claimed, game_launched, search, support_contact, ...

Events are ingested via Kafka and processed by Apache Flink for
real-time identity resolution and profile enrichment.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CustomerEvent:
    """
    Immutable record of a single player touchpoint.

    Fields:
        event_id:           UUID4 string — unique identifier for this event
        customer_id:        Platform player ID (or anonymous ID before login)
        event_type:         Semantic event name: 'page_view', 'bet_placed',
                            'deposit', 'withdrawal', 'login', 'game_launched', etc.
        properties:         Arbitrary event-specific payload (game_id, amount, etc.)
        timestamp:          UTC time the event occurred (not ingestion time)
        source:             Originating channel: 'web', 'mobile_ios', 'mobile_android', 'api'
        session_id:         Session UUID — links events within a continuous session
        device_id:          Persistent device fingerprint (survives app reinstalls)
        ip_address:         Client IP (used for geo-resolution and fraud detection)
        user_agent:         Raw UA string
        consent_categories: GDPR consent categories active at time of event.
                            Minimum is always ['essential']. Marketing and analytics
                            events should only be processed if 'analytics' is present.
    """

    event_id: str
    customer_id: str
    event_type: str          # 'page_view', 'bet_placed', 'deposit', 'login', ...
    properties: dict[str, Any]
    timestamp: datetime
    source: str              # 'web', 'mobile_ios', 'mobile_android', 'api'
    session_id: str
    device_id: str
    ip_address: str
    user_agent: str
    consent_categories: list[str] = field(default_factory=lambda: ["essential"])

    # ---------------------------------------------------------------------------
    # Consent helpers
    # ---------------------------------------------------------------------------

    @property
    def has_analytics_consent(self) -> bool:
        """True if the player has consented to analytics data collection."""
        return "analytics" in self.consent_categories

    @property
    def has_marketing_consent(self) -> bool:
        """True if the player has consented to marketing communications."""
        return "marketing" in self.consent_categories

    @property
    def has_personalisation_consent(self) -> bool:
        """True if the player has consented to personalised content."""
        return "personalisation" in self.consent_categories

    # ---------------------------------------------------------------------------
    # Event type helpers
    # ---------------------------------------------------------------------------

    @property
    def is_financial_event(self) -> bool:
        """True for deposit, withdrawal, and bet events."""
        return self.event_type in ("deposit", "withdrawal", "bet_placed", "win_received")

    @property
    def is_game_event(self) -> bool:
        """True for game interaction events."""
        return self.event_type in ("game_launched", "game_closed", "bet_placed", "win_received")

    @property
    def is_registration_event(self) -> bool:
        """True for account creation and KYC events."""
        return self.event_type in ("registration_started", "registration_completed", "kyc_submitted")

    # ---------------------------------------------------------------------------
    # Serialization helpers
    # ---------------------------------------------------------------------------

    def to_kafka_value(self) -> dict[str, Any]:
        """Serialize to a dict suitable for Kafka JSON serialization."""
        return {
            "event_id": self.event_id,
            "customer_id": self.customer_id,
            "event_type": self.event_type,
            "properties": self.properties,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "consent_categories": self.consent_categories,
        }

    @classmethod
    def from_kafka_value(cls, data: dict[str, Any]) -> "CustomerEvent":
        """Deserialize from a Kafka JSON message dict."""
        return cls(
            event_id=data["event_id"],
            customer_id=data["customer_id"],
            event_type=data["event_type"],
            properties=data.get("properties", {}),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data.get("source", "api"),
            session_id=data.get("session_id", ""),
            device_id=data.get("device_id", ""),
            ip_address=data.get("ip_address", ""),
            user_agent=data.get("user_agent", ""),
            consent_categories=data.get("consent_categories", ["essential"]),
        )
