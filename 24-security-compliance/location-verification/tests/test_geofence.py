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
tests/test_geofence.py — Tests for platform-grade location verification.

Covers:
  - IP geolocation lookup and override
  - VPN/proxy/datacenter detection
  - GPS validation and IP-GPS cross-reference
  - Cloudflare edge geo comparison
  - Jurisdiction matrix resolution
  - US state-level precision (NJ, PA, MI)
  - Blocked countries enforcement
  - Exclusion zone detection
  - Spoofing score computation
  - Confidence scoring
  - Session re-verification timing
  - Audit trail
"""
from __future__ import annotations

import sys
import os
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from geofence import (
    BLOCKED_COUNTRIES,
    JURISDICTION_MATRIX,
    GeoSignal,
    GeoVerdict,
    GeofenceService,
    IPIntelligenceProvider,
    JurisdictionConfig,
    SignalSource,
    haversine_km,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ip_provider() -> IPIntelligenceProvider:
    provider = IPIntelligenceProvider()
    # NJ player
    provider.set_override("1.2.3.4", GeoSignal(
        source=SignalSource.IP.value,
        latitude=40.7128, longitude=-74.0060,
        country_code="US", region_code="NJ",
        city="Newark",
    ))
    # UK player
    provider.set_override("5.6.7.8", GeoSignal(
        source=SignalSource.IP.value,
        latitude=51.5074, longitude=-0.1278,
        country_code="GB", region_code="",
        city="London",
    ))
    # VPN user
    provider.set_override("10.0.0.1", GeoSignal(
        source=SignalSource.IP.value,
        country_code="US", region_code="NJ",
        is_vpn=True,
    ))
    # Proxy user
    provider.set_override("10.0.0.2", GeoSignal(
        source=SignalSource.IP.value,
        country_code="GB",
        is_proxy=True,
    ))
    # Datacenter IP
    provider.set_override("10.0.0.3", GeoSignal(
        source=SignalSource.IP.value,
        country_code="US", region_code="NJ",
        is_datacenter=True,
    ))
    # Iran (blocked)
    provider.set_override("8.8.8.1", GeoSignal(
        source=SignalSource.IP.value,
        country_code="IR",
    ))
    # Malta player
    provider.set_override("9.9.9.1", GeoSignal(
        source=SignalSource.IP.value,
        latitude=35.8997, longitude=14.5146,
        country_code="MT",
    ))
    # Brazil player
    provider.set_override("9.9.9.2", GeoSignal(
        source=SignalSource.IP.value,
        latitude=-23.5505, longitude=-46.6333,
        country_code="BR",
    ))
    return provider


@pytest.fixture
def service(ip_provider) -> GeofenceService:
    return GeofenceService(ip_provider=ip_provider)


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_zero_distance(self):
        assert haversine_km(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_nyc_london(self):
        dist = haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert 5500 < dist < 5700  # ~5570km

    def test_short_distance(self):
        # Newark to Jersey City ~10km
        dist = haversine_km(40.7357, -74.1724, 40.7178, -74.0431)
        assert 5 < dist < 20


# ---------------------------------------------------------------------------
# VPN / Proxy / Datacenter detection
# ---------------------------------------------------------------------------

class TestThreatDetection:
    def test_vpn_denied(self, service: GeofenceService):
        result = service.verify_location(
            "player-1", "sess-1", "10.0.0.1",
        )
        assert result.verdict == GeoVerdict.DENIED_VPN.value
        assert result.vpn_detected is True

    def test_proxy_denied(self, service: GeofenceService):
        result = service.verify_location(
            "player-2", "sess-2", "10.0.0.2",
        )
        assert result.verdict == GeoVerdict.DENIED_PROXY.value
        assert result.proxy_detected is True

    def test_datacenter_denied(self, service: GeofenceService):
        result = service.verify_location(
            "player-3", "sess-3", "10.0.0.3",
        )
        assert result.verdict == GeoVerdict.DENIED_DATACENTER.value


# ---------------------------------------------------------------------------
# Jurisdiction resolution
# ---------------------------------------------------------------------------

class TestJurisdiction:
    def test_nj_player_allowed(self, service: GeofenceService):
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.73, longitude=-74.17,
            country_code="US", region_code="NJ",
        )
        result = service.verify_location(
            "player-nj", "sess-nj", "1.2.3.4", gps_signal=gps,
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "US-NJ"

    def test_uk_player_allowed(self, service: GeofenceService):
        result = service.verify_location(
            "player-uk", "sess-uk", "5.6.7.8",
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "GB"

    def test_malta_player_allowed(self, service: GeofenceService):
        result = service.verify_location(
            "player-mt", "sess-mt", "9.9.9.1",
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "MT"

    def test_brazil_player_allowed(self, service: GeofenceService):
        result = service.verify_location(
            "player-br", "sess-br", "9.9.9.2",
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "BR"

    def test_blocked_country_denied(self, service: GeofenceService):
        result = service.verify_location(
            "player-ir", "sess-ir", "8.8.8.1",
        )
        assert result.verdict == GeoVerdict.DENIED_JURISDICTION.value
        assert "blocked" in result.message.lower()

    def test_us_without_state_denied(self, service: GeofenceService):
        """US at country level is blocked; only licensed states allowed."""
        service.ip_provider.set_override("2.2.2.2", GeoSignal(
            source=SignalSource.IP.value,
            country_code="US", region_code="",
        ))
        result = service.verify_location("p-us", "s-us", "2.2.2.2")
        assert result.verdict == GeoVerdict.DENIED_JURISDICTION.value


# ---------------------------------------------------------------------------
# US state-level precision
# ---------------------------------------------------------------------------

class TestUSStatePrecision:
    def test_nj_gps_overrides_ip(self, service: GeofenceService):
        """GPS in NJ should be used even if IP resolves differently."""
        service.ip_provider.set_override("3.3.3.3", GeoSignal(
            source=SignalSource.IP.value,
            latitude=40.0, longitude=-75.0,
            country_code="US", region_code="PA",
        ))
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.73, longitude=-74.17,
            country_code="US", region_code="NJ",
        )
        result = service.verify_location(
            "p-nj-gps", "s-nj-gps", "3.3.3.3", gps_signal=gps,
        )
        assert result.jurisdiction == "US-NJ"

    def test_mobile_without_gps_denied_in_us(self, service: GeofenceService):
        """US states require GPS on mobile devices."""
        result = service.verify_location(
            "p-nj-mobile", "s-nj-mobile", "1.2.3.4",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
        )
        assert result.verdict == GeoVerdict.DENIED_INSUFFICIENT.value
        assert "GPS" in result.message


# ---------------------------------------------------------------------------
# Cloudflare edge comparison
# ---------------------------------------------------------------------------

class TestCloudflareEdge:
    def test_cf_country_adds_confidence(self, service: GeofenceService):
        result = service.verify_location(
            "p-uk", "s-uk", "5.6.7.8", cf_country="GB",
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert "CF_EDGE" in result.signals_used
        assert result.confidence > 0.5

    def test_cf_ip_mismatch_increases_spoofing(self, service: GeofenceService):
        # IP says GB, CF says IR
        result = service.verify_location(
            "p-mismatch", "s-mismatch", "5.6.7.8", cf_country="IR",
        )
        # Still might be allowed (IP wins), but spoofing score should be higher
        assert result.spoofing_score > 0.0


# ---------------------------------------------------------------------------
# IP-GPS discrepancy
# ---------------------------------------------------------------------------

class TestIPGPSDiscrepancy:
    def test_large_discrepancy_denied(self, service: GeofenceService):
        """IP in NJ, GPS in California = mismatch."""
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=34.05, longitude=-118.24,  # Los Angeles
            country_code="US", region_code="CA",
        )
        result = service.verify_location(
            "p-mismatch", "s-mismatch", "1.2.3.4", gps_signal=gps,
        )
        # Should be denied: IP says NJ, GPS says CA
        # Either mismatch or jurisdiction denied
        assert result.verdict in (
            GeoVerdict.DENIED_MISMATCH.value,
            GeoVerdict.DENIED_JURISDICTION.value,
        )

    def test_small_discrepancy_allowed(self, service: GeofenceService):
        """IP and GPS both in NJ area = allowed."""
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.72, longitude=-74.05,  # near Newark
            country_code="US", region_code="NJ",
        )
        result = service.verify_location(
            "p-close", "s-close", "1.2.3.4", gps_signal=gps,
        )
        assert result.verdict == GeoVerdict.ALLOWED.value


# ---------------------------------------------------------------------------
# Spoofing detection
# ---------------------------------------------------------------------------

class TestSpoofing:
    def test_sub_meter_gps_flagged(self, service: GeofenceService):
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=51.5074, longitude=-0.1278,
            accuracy_meters=0.5,  # suspiciously precise
            country_code="GB",
        )
        result = service.verify_location(
            "p-spoof", "s-spoof", "5.6.7.8", gps_signal=gps,
        )
        assert result.spoofing_score > 0.0


# ---------------------------------------------------------------------------
# Blocked countries
# ---------------------------------------------------------------------------

class TestBlockedCountries:
    @pytest.mark.parametrize("country", ["KP", "IR", "SY", "CU"])
    def test_sanctioned_countries_blocked(self, country: str):
        assert country in BLOCKED_COUNTRIES

    def test_us_country_level_blocked(self):
        assert "US" in BLOCKED_COUNTRIES


# ---------------------------------------------------------------------------
# Jurisdiction matrix
# ---------------------------------------------------------------------------

class TestJurisdictionMatrix:
    def test_all_jurisdictions_have_required_fields(self):
        for code, config in JURISDICTION_MATRIX.items():
            assert config.code == code
            assert config.name != ""
            assert config.country != ""
            assert config.min_age >= 18
            assert config.currency != ""
            assert len(config.allowed_game_types) > 0

    def test_us_states_require_gps(self):
        for code, config in JURISDICTION_MATRIX.items():
            if code.startswith("US-"):
                assert config.requires_gps is True

    def test_us_states_age_21(self):
        for code, config in JURISDICTION_MATRIX.items():
            if code.startswith("US-"):
                assert config.min_age == 21

    def test_european_age_18(self):
        for code in ["GB", "MT", "SE", "DK"]:
            assert JURISDICTION_MATRIX[code].min_age == 18


# ---------------------------------------------------------------------------
# Session re-verification
# ---------------------------------------------------------------------------

class TestReverification:
    def test_needs_reverification_after_interval(self, service: GeofenceService):
        last_check = time.time() - 3600  # 1 hour ago
        assert service.needs_reverification("s1", "GB", last_check) is True

    def test_no_reverification_needed_recently(self, service: GeofenceService):
        last_check = time.time() - 60  # 1 minute ago
        assert service.needs_reverification("s1", "GB", last_check) is False

    def test_unknown_jurisdiction_always_reverify(self, service: GeofenceService):
        last_check = time.time()
        assert service.needs_reverification("s1", "XX-UNKNOWN", last_check) is True


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_verification_logged(self, service: GeofenceService):
        service.verify_location("p1", "s1", "5.6.7.8")
        log = service.get_audit_log(player_id="p1")
        assert len(log) >= 1
        assert log[0]["player_id"] == "p1"
        assert log[0]["verdict"] == GeoVerdict.ALLOWED.value

    def test_denial_logged(self, service: GeofenceService):
        service.verify_location("p-vpn", "s-vpn", "10.0.0.1")
        log = service.get_audit_log(player_id="p-vpn")
        assert len(log) >= 1
        assert log[0]["verdict"] == GeoVerdict.DENIED_VPN.value
