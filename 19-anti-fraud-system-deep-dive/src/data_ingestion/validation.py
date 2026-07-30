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
Data validation for the Fraud Detection Data Ingestion Service
"""

import re
from typing import Dict, Any, List, Optional
from ipaddress import ip_address, IPv4Address, IPv6Address
import structlog

logger = structlog.get_logger(__name__)


class DataValidator:
    """Data validation service for fraud detection events"""

    def __init__(self):
        # Validation rules
        self.max_string_length = 1000
        self.max_list_length = 100
        self.allowed_currencies = {"USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY"}
        self.allowed_transaction_types = {"deposit", "withdrawal", "bet", "win"}
        self.allowed_user_events = {"login", "logout", "page_view", "button_click", "game_start", "game_end"}
        self.allowed_game_events = {"game_start", "game_end", "spin", "bet", "win", "loss", "bonus", "jackpot"}

        # Email regex
        self.email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

        # Phone regex (international format)
        self.phone_pattern = re.compile(r'^\+?[1-9]\d{1,14}$')

    def validate_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate transaction event data

        Args:
            data: Transaction data dictionary

        Returns:
            Validation result with 'valid' boolean and 'errors' list
        """

        errors = []

        # Required fields
        required_fields = ["player_id", "amount", "transaction_type"]
        for field in required_fields:
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")

        if errors:
            return {"valid": False, "errors": errors}

        # Player ID validation
        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")

        # Amount validation
        if not self._validate_amount(data.get("amount")):
            errors.append("Invalid amount: must be positive number")

        # Currency validation
        if "currency" in data and data["currency"] is not None and data["currency"] not in self.allowed_currencies:
            errors.append(f"Invalid currency: {data['currency']}")

        # Transaction type validation
        if data.get("transaction_type") not in self.allowed_transaction_types:
            errors.append(f"Invalid transaction_type: {data['transaction_type']}")

        # Payment method validation
        if "payment_method" in data and data["payment_method"] is not None and not self._validate_string(data["payment_method"]):
            errors.append("Invalid payment_method format")

        # Game type validation
        if "game_type" in data and data["game_type"] is not None and not self._validate_string(data["game_type"]):
            errors.append("Invalid game_type format")

        # External transaction ID validation
        if "external_transaction_id" in data and data["external_transaction_id"] is not None and not self._validate_string(data["external_transaction_id"]):
            errors.append("Invalid external_transaction_id format")

        # IP address validation
        if "ip_address" in data and data["ip_address"] is not None and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")

        # User agent validation
        if "user_agent" in data and data["user_agent"] is not None and not self._validate_user_agent(data["user_agent"]):
            errors.append("Invalid user_agent format")

        # Device fingerprint validation
        if "device_fingerprint" in data and data["device_fingerprint"] is not None and not self._validate_string(data["device_fingerprint"]):
            errors.append("Invalid device_fingerprint format")

        # Location data validation
        if "location_data" in data and data["location_data"] is not None and not self._validate_location_data(data["location_data"]):
            errors.append("Invalid location_data format")

        # Metadata validation
        if "metadata" in data and data["metadata"] is not None and not self._validate_metadata(data["metadata"]):
            errors.append("Invalid metadata format")

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    def validate_user_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate user event data

        Args:
            data: User event data dictionary

        Returns:
            Validation result
        """

        errors = []

        # Required fields
        required_fields = ["player_id", "event_type"]
        for field in required_fields:
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")

        if errors:
            return {"valid": False, "errors": errors}

        # Player ID validation
        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")

        # Event type validation
        if data.get("event_type") not in self.allowed_user_events:
            errors.append(f"Invalid event_type: {data['event_type']}")

        # Session ID validation
        if "session_id" in data and data["session_id"] is not None and not self._validate_string(data["session_id"]):
            errors.append("Invalid session_id format")

        # Page URL validation
        if "page_url" in data and data["page_url"] is not None and not self._validate_url(data["page_url"]):
            errors.append("Invalid page_url format")

        # Element ID validation
        if "element_id" in data and data["element_id"] is not None and not self._validate_string(data["element_id"]):
            errors.append("Invalid element_id format")

        # Game type validation
        if "game_type" in data and data["game_type"] is not None and not self._validate_string(data["game_type"]):
            errors.append("Invalid game_type format")

        # Duration validation
        if "duration_seconds" in data and data["duration_seconds"] is not None and not self._validate_duration(data["duration_seconds"]):
            errors.append("Invalid duration_seconds: must be non-negative integer")

        # IP address validation
        if "ip_address" in data and data["ip_address"] is not None and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")

        # User agent validation
        if "user_agent" in data and data["user_agent"] is not None and not self._validate_user_agent(data["user_agent"]):
            errors.append("Invalid user_agent format")

        # Device fingerprint validation
        if "device_fingerprint" in data and data["device_fingerprint"] is not None and not self._validate_string(data["device_fingerprint"]):
            errors.append("Invalid device_fingerprint format")

        # Location data validation
        if "location_data" in data and data["location_data"] is not None and not self._validate_location_data(data["location_data"]):
            errors.append("Invalid location_data format")

        # Event data validation
        if "event_data" in data and data["event_data"] is not None and not self._validate_event_data(data["event_data"]):
            errors.append("Invalid event_data format")

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    def validate_game_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate game event data

        Args:
            data: Game event data dictionary

        Returns:
            Validation result
        """

        errors = []

        # Required fields
        required_fields = ["player_id", "game_type", "game_session_id", "event_type"]
        for field in required_fields:
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: {field}")

        if errors:
            return {"valid": False, "errors": errors}

        # Player ID validation
        if not self._validate_player_id(data.get("player_id")):
            errors.append("Invalid player_id format")

        # Game type validation
        if not self._validate_string(data.get("game_type")):
            errors.append("Invalid game_type format")

        # Game session ID validation
        if not self._validate_string(data.get("game_session_id")):
            errors.append("Invalid game_session_id format")

        # Event type validation
        if data.get("event_type") not in self.allowed_game_events:
            errors.append(f"Invalid event_type: {data['event_type']}")

        # Bet amount validation
        if "bet_amount" in data and data["bet_amount"] is not None and not self._validate_amount(data["bet_amount"]):
            errors.append("Invalid bet_amount: must be non-negative number")

        # Win amount validation
        if "win_amount" in data and data["win_amount"] is not None and not self._validate_amount(data["win_amount"]):
            errors.append("Invalid win_amount: must be non-negative number")

        # Game state validation
        if "game_state" in data and data["game_state"] is not None and not self._validate_game_state(data["game_state"]):
            errors.append("Invalid game_state format")

        # IP address validation
        if "ip_address" in data and data["ip_address"] is not None and not self._validate_ip_address(data["ip_address"]):
            errors.append("Invalid IP address format")

        # Device fingerprint validation
        if "device_fingerprint" in data and data["device_fingerprint"] is not None and not self._validate_string(data["device_fingerprint"]):
            errors.append("Invalid device_fingerprint format")

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    def _validate_player_id(self, player_id: Any) -> bool:
        """Validate player ID format"""
        if not isinstance(player_id, str):
            return False
        if not 1 <= len(player_id) <= 100:
            return False
        # Allow alphanumeric, hyphens, and underscores
        return bool(re.match(r'^[a-zA-Z0-9_-]+$', player_id))

    def _validate_amount(self, amount: Any) -> bool:
        """Validate monetary amount"""
        try:
            return isinstance(amount, (int, float)) and amount >= 0
        except (TypeError, ValueError):
            return False

    def _validate_string(self, value: Any, max_length: Optional[int] = None) -> bool:
        """Validate string value"""
        if not isinstance(value, str):
            return False
        max_len = max_length or self.max_string_length
        return 1 <= len(value) <= max_len

    def _validate_ip_address(self, ip: Any) -> bool:
        """Validate IP address"""
        if not isinstance(ip, str):
            return False
        try:
            ip_address(ip)
            return True
        except ValueError:
            return False

    def _validate_url(self, url: Any) -> bool:
        """Validate URL format"""
        if not isinstance(url, str):
            return False
        if not 1 <= len(url) <= 2000:
            return False
        # Basic URL validation
        return url.startswith(('http://', 'https://'))

    def _validate_user_agent(self, ua: Any) -> bool:
        """Validate user agent string"""
        return self._validate_string(ua, max_length=500)

    def _validate_duration(self, duration: Any) -> bool:
        """Validate duration in seconds"""
        try:
            return isinstance(duration, int) and duration >= 0
        except (TypeError, ValueError):
            return False

    def _validate_location_data(self, location: Any) -> bool:
        """Validate location data structure"""
        if not isinstance(location, dict):
            return False

        required_fields = ["country"]
        for field in required_fields:
            if field not in location:
                return False

        # Validate country code
        country = location.get("country")
        if not isinstance(country, str) or len(country) != 2:
            return False

        # Optional fields
        optional_fields = ["city", "region", "postal_code", "latitude", "longitude"]
        for field in optional_fields:
            if field in location:
                if field in ["latitude", "longitude"]:
                    try:
                        float(location[field])
                    except (TypeError, ValueError):
                        return False
                elif not isinstance(location[field], str):
                    return False

        return True

    def _validate_metadata(self, metadata: Any) -> bool:
        """Validate metadata structure"""
        if not isinstance(metadata, dict):
            return False
        if len(metadata) > 50:  # Limit number of metadata fields
            return False

        for key, value in metadata.items():
            if not isinstance(key, str) or len(key) > 100:
                return False
            # Allow basic types
            if not isinstance(value, (str, int, float, bool)):
                return False

        return True

    def _validate_event_data(self, event_data: Any) -> bool:
        """Validate event data structure"""
        if not isinstance(event_data, dict):
            return False
        if len(event_data) > 20:  # Limit number of event data fields
            return False

        for key, value in event_data.items():
            if not isinstance(key, str) or len(key) > 100:
                return False
            # Allow various types but limit complexity
            if isinstance(value, (list, dict)) and len(str(value)) > 1000:
                return False

        return True

    def _validate_game_state(self, game_state: Any) -> bool:
        """Validate game state structure"""
        if not isinstance(game_state, dict):
            return False
        if len(game_state) > 50:  # Limit game state complexity
            return False

        # Allow nested structures but limit depth and size
        def validate_nested(obj, depth=0):
            if depth > 3:  # Max nesting depth
                return False
            if isinstance(obj, dict):
                return all(validate_nested(v, depth + 1) for v in obj.values())
            elif isinstance(obj, list):
                return len(obj) <= 10 and all(validate_nested(item, depth + 1) for item in obj)
            else:
                return isinstance(obj, (str, int, float, bool))

        return validate_nested(game_state)
