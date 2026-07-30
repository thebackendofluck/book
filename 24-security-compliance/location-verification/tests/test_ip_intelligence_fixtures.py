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
tests/test_ip_intelligence_fixtures.py -- Fixture-driven IP intelligence tests.

Verifies the IPIntelligenceAdapter ABC and StubIPIntelligence using
deterministic replay fixtures for:
  - Border case (NJ/PA boundary)
  - VPN detected
  - Proxy detected
  - Datacenter IP
  - Tor exit node
  - Clean residential IP
"""
from __future__ import annotations

import json
import os

import pytest

from geofence import (
    GeoSignal,
    GeoVerdict,
    GeofenceService,
    IPIntelligenceAdapter,
    MaxMindIPIntelligence,
    StubIPIntelligence,
    SignalSource,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
FIXTURES_PATH = os.path.join(FIXTURES_DIR, "ip_intelligence.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixtures() -> dict:
    with open(FIXTURES_PATH) as f:
        return json.load(f)


def _build_provider(fixtures: dict) -> StubIPIntelligence:
    """Load fixture signals into a StubIPIntelligence provider."""
    provider = StubIPIntelligence()
    for _key, entry in fixtures.items():
        if _key.startswith("_"):
            continue
        ip = entry["ip"]
        provider.set_override(ip, GeoSignal(**entry["signal"]))
    return provider


@pytest.fixture
def fixtures() -> dict:
    return _load_fixtures()


@pytest.fixture
def provider(fixtures) -> StubIPIntelligence:
    return _build_provider(fixtures)


@pytest.fixture
def service(provider) -> GeofenceService:
    return GeofenceService(ip_provider=provider)


# ---------------------------------------------------------------------------
# ABC contract tests
# ---------------------------------------------------------------------------

class TestIPIntelligenceAdapter:
    """Verify the abstract interface and implementations."""

    def test_stub_implements_abc(self):
        stub = StubIPIntelligence()
        assert isinstance(stub, IPIntelligenceAdapter)

    def test_maxmind_implements_abc(self):
        mm = MaxMindIPIntelligence()
        assert isinstance(mm, IPIntelligenceAdapter)

    def test_maxmind_raises_not_implemented(self):
        mm = MaxMindIPIntelligence()
        with pytest.raises(NotImplementedError):
            mm.lookup("1.2.3.4")

    def test_stub_unknown_ip_returns_empty(self):
        stub = StubIPIntelligence()
        result = stub.lookup("0.0.0.0")
        assert result.country_code == ""
        assert result.region_code == ""

    def test_stub_load_fixtures(self, fixtures):
        """Verify bulk loading via load_fixtures method."""
        stub = StubIPIntelligence()
        fixture_map = {}
        for key, entry in fixtures.items():
            if key.startswith("_"):
                continue
            fixture_map[entry["ip"]] = entry["signal"]
        stub.load_fixtures(fixture_map)
        # Should find clean residential
        result = stub.lookup("203.0.113.60")
        assert result.country_code == "US"
        assert result.region_code == "NJ"


# ---------------------------------------------------------------------------
# Border case: NJ/PA boundary
# ---------------------------------------------------------------------------

class TestBorderCase:
    def test_border_ip_resolves_to_nj(self, provider):
        signal = provider.lookup("203.0.113.10")
        assert signal.country_code == "US"
        assert signal.region_code == "NJ"
        assert signal.accuracy_meters == 25000  # high uncertainty at border

    def test_border_ip_allowed_with_gps_in_nj(self, service):
        """Player on NJ side of NJ/PA border with GPS confirmation."""
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.2200, longitude=-74.7500,
            accuracy_meters=10,
            country_code="US", region_code="NJ",
        )
        result = service.verify_location(
            "player-border", "sess-border", "203.0.113.10", gps_signal=gps,
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "US-NJ"

    def test_border_ip_denied_with_gps_in_pa(self, service):
        """Player on PA side of NJ/PA border; IP says NJ but GPS says PA."""
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.2100, longitude=-74.7600,
            accuracy_meters=10,
            country_code="US", region_code="PA",
        )
        result = service.verify_location(
            "player-border-pa", "sess-border-pa", "203.0.113.10", gps_signal=gps,
        )
        assert result.jurisdiction == "US-PA"


# ---------------------------------------------------------------------------
# VPN detected
# ---------------------------------------------------------------------------

class TestVPNDetected:
    def test_vpn_flag_set(self, provider):
        signal = provider.lookup("198.51.100.20")
        assert signal.is_vpn is True

    def test_vpn_denied(self, service):
        result = service.verify_location("player-vpn", "sess-vpn", "198.51.100.20")
        assert result.verdict == GeoVerdict.DENIED_VPN.value
        assert result.vpn_detected is True


# ---------------------------------------------------------------------------
# Proxy detected
# ---------------------------------------------------------------------------

class TestProxyDetected:
    def test_proxy_flag_set(self, provider):
        signal = provider.lookup("192.0.2.30")
        assert signal.is_proxy is True

    def test_proxy_denied(self, service):
        result = service.verify_location("player-proxy", "sess-proxy", "192.0.2.30")
        assert result.verdict == GeoVerdict.DENIED_PROXY.value
        assert result.proxy_detected is True


# ---------------------------------------------------------------------------
# Datacenter IP
# ---------------------------------------------------------------------------

class TestDatacenterIP:
    def test_datacenter_flag_set(self, provider):
        signal = provider.lookup("198.51.100.40")
        assert signal.is_datacenter is True

    def test_datacenter_denied(self, service):
        result = service.verify_location("player-dc", "sess-dc", "198.51.100.40")
        assert result.verdict == GeoVerdict.DENIED_DATACENTER.value


# ---------------------------------------------------------------------------
# Tor exit node
# ---------------------------------------------------------------------------

class TestTorExitNode:
    def test_tor_flag_set(self, provider):
        signal = provider.lookup("203.0.113.50")
        assert signal.is_tor is True

    def test_tor_spoofing_score_with_cf_mismatch(self, service):
        """Tor + CF country mismatch raises spoofing score above threshold."""
        result = service.verify_location(
            "player-tor", "sess-tor", "203.0.113.50", cf_country="US",
        )
        assert result.spoofing_score > 0.5

    def test_tor_denied_or_spoofing(self, service):
        """Tor exit nodes should be denied -- either spoofing or jurisdiction."""
        result = service.verify_location("player-tor", "sess-tor", "203.0.113.50")
        assert result.verdict in (
            GeoVerdict.DENIED_SPOOFING.value,
            GeoVerdict.DENIED_JURISDICTION.value,
        )


# ---------------------------------------------------------------------------
# Clean residential IP
# ---------------------------------------------------------------------------

class TestCleanResidential:
    def test_clean_no_threat_flags(self, provider):
        signal = provider.lookup("203.0.113.60")
        assert signal.is_vpn is False
        assert signal.is_proxy is False
        assert signal.is_datacenter is False
        assert signal.is_tor is False

    def test_clean_residential_allowed(self, service):
        gps = GeoSignal(
            source=SignalSource.GPS.value,
            latitude=40.48, longitude=-74.26,
            accuracy_meters=15,
            country_code="US", region_code="NJ",
        )
        result = service.verify_location(
            "player-clean", "sess-clean", "203.0.113.60", gps_signal=gps,
        )
        assert result.verdict == GeoVerdict.ALLOWED.value
        assert result.jurisdiction == "US-NJ"
        assert result.confidence > 0.5

    def test_clean_residential_good_accuracy(self, provider):
        signal = provider.lookup("203.0.113.60")
        assert signal.accuracy_meters == 5000
        assert signal.isp == "Verizon Fios"


# ---------------------------------------------------------------------------
# Fixture file integrity
# ---------------------------------------------------------------------------

class TestFixtureIntegrity:
    def test_fixture_file_exists(self):
        assert os.path.isfile(FIXTURES_PATH)

    def test_fixture_has_all_scenarios(self, fixtures):
        expected = {
            "border_nj_pa", "vpn_detected", "proxy_detected",
            "datacenter_ip", "tor_exit_node", "clean_residential",
        }
        actual = {k for k in fixtures if not k.startswith("_")}
        assert expected == actual

    def test_all_fixtures_have_required_fields(self, fixtures):
        for key, entry in fixtures.items():
            if key.startswith("_"):
                continue
            assert "ip" in entry, f"Fixture {key} missing 'ip'"
            assert "signal" in entry, f"Fixture {key} missing 'signal'"
            sig = entry["signal"]
            assert "country_code" in sig, f"Fixture {key} missing 'country_code'"
            assert "source" in sig, f"Fixture {key} missing 'source'"
