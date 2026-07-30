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
Data Validation for the Fraud Detection Ingestion Layer

Validates transaction, user-behavior, and game events before they enter
the Kafka pipeline.  Catches malformed data early so downstream ML models
never see garbage inputs.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import re
from ipaddress import ip_address
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class DataValidator:
    """Stateless validator for all fraud-detection event types."""

    def __init__(self):
        self.max_string_length = 1000
        self.max_list_length = 100
        self.allowed_currencies = {"USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY"}
        self.allowed_transaction_types = {"deposit", "withdrawal", "bet", "win"}
        self.allowed_user_events = {
            "login", "logout", "page_view", "button_click",
            "game_start", "game_end",
        }
        self.allowed_game_events = {
            "game_start", "game_end", "spin", "bet", "win",
            "loss", "bonus", "jackpot",
        }
        self.email_pattern = re.compile(
            r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        )
        self.phone_pattern = re.compile(r"^\+?[1-9]\d{1,14}$")

    # ------------------------------------------------------------------
    # Public validation methods
    # ------------------------------------------------------------------

    def validate_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Return ``{"valid": True/False, "errors": [...]}``."""
        errors: List[str] = []

        for field in ("player_id", "amount", "transaction_type"):
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")
        if errors:
            return {"valid": False, "errors": errors}

        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")
        if not self._validate_amount(data.get("amount")):
            errors.append("Invalid amount: must be positive number")
        if "currency" in data and data["currency"] not in self.allowed_currencies:
            errors.append(f"Invalid currency: {data['currency']}")
        if data.get("transaction_type") not in self.allowed_transaction_types:
            errors.append(f"Invalid transaction_type: {data['transaction_type']}")
        if "ip_address" in data and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")
        if "user_agent" in data and not self._validate_user_agent(data["user_agent"]):
            errors.append("Invalid user_agent format")
        if "location_data" in data and not self._validate_location_data(data["location_data"]):
            errors.append("Invalid location_data format")
        if "metadata" in data and not self._validate_metadata(data["metadata"]):
            errors.append("Invalid metadata format")

        return {"valid": len(errors) == 0, "errors": errors}

    def validate_user_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        errors: List[str] = []

        for field in ("player_id", "event_type"):
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")
        if errors:
            return {"valid": False, "errors": errors}

        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")
        if data.get("event_type") not in self.allowed_user_events:
            errors.append(f"Invalid event_type: {data['event_type']}")
        if "page_url" in data and not self._validate_url(data["page_url"]):
            errors.append("Invalid page_url format")
        if "duration_seconds" in data and not self._validate_duration(data["duration_seconds"]):
            errors.append("Invalid duration_seconds: must be non-negative integer")
        if "ip_address" in data and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")
        if "location_data" in data and not self._validate_location_data(data["location_data"]):
            errors.append("Invalid location_data format")

        return {"valid": len(errors) == 0, "errors": errors}

    def validate_game_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        errors: List[str] = []

        for field in ("player_id", "game_type", "game_session_id", "event_type"):
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")
        if errors:
            return {"valid": False, "errors": errors}

        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")
        if data.get("event_type") not in self.allowed_game_events:
            errors.append(f"Invalid event_type: {data['event_type']}")
        if "bet_amount" in data and not self._validate_amount(data["bet_amount"]):
            errors.append("Invalid bet_amount: must be non-negative number")
        if "win_amount" in data and not self._validate_amount(data["win_amount"]):
            errors.append("Invalid win_amount: must be non-negative number")
        if "game_state" in data and not self._validate_game_state(data["game_state"]):
            errors.append("Invalid game_state format")
        if "ip_address" in data and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")

        return {"valid": len(errors) == 0, "errors": errors}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_player_id(self, player_id: Any) -> bool:
        if not isinstance(player_id, str):
            return False
        if not 1 <= len(player_id) <= 100:
            return False
        return bool(re.match(r"^[a-zA-Z0-9_-]+$", player_id))

    def _validate_amount(self, amount: Any) -> bool:
        try:
            return isinstance(amount, (int, float)) and amount >= 0
        except (TypeError, ValueError):
            return False

    def _validate_string(self, value: Any, max_length: Optional[int] = None) -> bool:
        if not isinstance(value, str):
            return False
        return 1 <= len(value) <= (max_length or self.max_string_length)

    def _validate_ip_address(self, ip: Any) -> bool:
        if not isinstance(ip, str):
            return False
        try:
            ip_address(ip)
            return True
        except ValueError:
            return False

    def _validate_url(self, url: Any) -> bool:
        if not isinstance(url, str):
            return False
        if not 1 <= len(url) <= 2000:
            return False
        return url.startswith(("http://", "https://"))

    def _validate_user_agent(self, ua: Any) -> bool:
        return self._validate_string(ua, max_length=500)

    def _validate_duration(self, duration: Any) -> bool:
        try:
            return isinstance(duration, int) and duration >= 0
        except (TypeError, ValueError):
            return False

    def _validate_location_data(self, location: Any) -> bool:
        if not isinstance(location, dict):
            return False
        country = location.get("country")
        if not isinstance(country, str) or len(country) != 2:
            return False
        for field in ("latitude", "longitude"):
            if field in location:
                try:
                    float(location[field])
                except (TypeError, ValueError):
                    return False
        return True

    def _validate_metadata(self, metadata: Any) -> bool:
        if not isinstance(metadata, dict) or len(metadata) > 50:
            return False
        for key, value in metadata.items():
            if not isinstance(key, str) or len(key) > 100:
                return False
            if not isinstance(value, (str, int, float, bool)):
                return False
        return True

    def _validate_game_state(self, game_state: Any) -> bool:
        if not isinstance(game_state, dict) or len(game_state) > 50:
            return False

        def _check(obj, depth=0):
            if depth > 3:
                return False
            if isinstance(obj, dict):
                return all(_check(v, depth + 1) for v in obj.values())
            if isinstance(obj, list):
                return len(obj) <= 10 and all(_check(i, depth + 1) for i in obj)
            return isinstance(obj, (str, int, float, bool))

        return _check(game_state)
