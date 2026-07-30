# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 18 — RTC System dataclasses and HMAC signing."""

import sys
import os
import hmac
import hashlib
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rtc-system"))

from casino_rtc import CasinoRTC, RTCConfig, RTCMessage


class TestRTCConfig:
    """Validate RTCConfig defaults and construction."""

    def test_config_has_sensible_defaults(self):
        config = RTCConfig(
            base_url="https://rtc.casino.com",
            api_key="key-123",
            secret_key="secret-456",
        )
        assert config.timeout_s == 30.0
        assert config.reconnect_attempts == 3
        assert config.reconnect_delay_s == 2.0

    def test_config_stores_credentials(self):
        config = RTCConfig(
            base_url="https://rtc.casino.com",
            api_key="mykey",
            secret_key="mysecret",
        )
        assert config.api_key == "mykey"
        assert config.secret_key == "mysecret"


class TestRTCMessage:
    """Validate RTCMessage construction."""

    def test_message_auto_timestamps(self):
        before = time.time()
        msg = RTCMessage(event_type="bet_placed", payload={"amount": 10.0})
        after = time.time()
        assert before <= msg.timestamp <= after

    def test_message_stores_payload(self):
        payload = {"game_id": "blackjack-1", "result": "win"}
        msg = RTCMessage(event_type="game_result", payload=payload)
        assert msg.payload == payload
        assert msg.event_type == "game_result"


class TestCasinoRTCAuth:
    """Validate HMAC signing and auth header generation."""

    def _make_rtc(self):
        config = RTCConfig(
            base_url="https://rtc.example.com",
            api_key="test-api-key",
            secret_key="test-secret-key",
        )
        return CasinoRTC(config)

    def test_sign_produces_valid_hex_digest(self):
        rtc = self._make_rtc()
        signature = rtc._sign("hello world")
        # Should be a 64-char hex string (SHA-256)
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)

    def test_sign_is_deterministic(self):
        rtc = self._make_rtc()
        sig1 = rtc._sign("test message")
        sig2 = rtc._sign("test message")
        assert sig1 == sig2

    def test_sign_matches_reference_hmac(self):
        rtc = self._make_rtc()
        message = "verify-this"
        expected = hmac.new(
            b"test-secret-key", message.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert rtc._sign(message) == expected

    def test_auth_headers_contain_required_fields(self):
        rtc = self._make_rtc()
        headers = rtc._build_auth_headers()
        assert "X-API-Key" in headers
        assert "X-Timestamp" in headers
        assert "X-Signature" in headers
        assert headers["X-API-Key"] == "test-api-key"
