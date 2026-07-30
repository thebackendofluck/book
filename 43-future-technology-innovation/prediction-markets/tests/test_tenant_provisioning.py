#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tests for B2B tenant provisioning of the prediction-markets vertical
(chapter 43c, Pattern 2)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jurisdiction_gate import JurisdictionGate  # noqa: E402
from market_lifecycle import MarketCategory  # noqa: E402
from tenant_provisioning import (  # noqa: E402
    ProvisioningError,
    TenantLicence,
    TenantProvisioner,
)


def make_clock():
    state = {"t": 1_700_000_000.0}

    def clock():
        state["t"] += 1.0
        return state["t"]

    return clock


@pytest.fixture
def provisioner():
    return TenantProvisioner(gate=JurisdictionGate(), clock=make_clock())


class TestRegistration:
    def test_register_tenant(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        version, config = provisioner.config_for("op-gi")
        assert version == 1
        assert config.enabled is False

    def test_duplicate_register_raises(self, provisioner):
        licence = TenantLicence("op-gi", "GI", "LGA/167/2026")
        provisioner.register_tenant(licence)
        with pytest.raises(ProvisioningError):
            provisioner.register_tenant(licence)

    def test_unknown_jurisdiction_propagates(self, provisioner):
        from jurisdiction_gate import UnknownJurisdiction

        with pytest.raises(UnknownJurisdiction):
            provisioner.register_tenant(
                TenantLicence("op-xx", "XX", "no-such-licence")
            )


class TestEnableDirect:
    def test_gibraltar_tenant_enables_football_and_politics_direct(
        self, provisioner
    ):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        config = provisioner.enable_prediction_markets(
            "op-gi",
            [MarketCategory.FOOTBALL, MarketCategory.POLITICS],
            fee_bps=150,
        )
        assert config.enabled is True
        assert config.partner_route is None
        assert set(config.categories) == {
            MarketCategory.FOOTBALL,
            MarketCategory.POLITICS,
        }

    def test_invalid_fee_bps_raises(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        with pytest.raises(ValueError):
            provisioner.enable_prediction_markets(
                "op-gi", [MarketCategory.FOOTBALL], fee_bps=1001
            )
        with pytest.raises(ValueError):
            provisioner.enable_prediction_markets(
                "op-gi", [MarketCategory.FOOTBALL], fee_bps=-1
            )


class TestEnablePartnerEmbedded:
    def test_brazil_requires_partner_route(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-br", "BR", "SPA/MF/2024-01")
        )
        with pytest.raises(ProvisioningError):
            provisioner.enable_prediction_markets(
                "op-br", [MarketCategory.FOOTBALL], fee_bps=100
            )

    def test_brazil_enables_with_matchbook_partner(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-br", "BR", "SPA/MF/2024-01")
        )
        config = provisioner.enable_prediction_markets(
            "op-br",
            [MarketCategory.FOOTBALL],
            fee_bps=100,
            partner_route="matchbook",
        )
        assert config.enabled is True
        assert config.partner_route == "matchbook"

    def test_brazil_politics_in_request_denies_naming_politics(
        self, provisioner
    ):
        provisioner.register_tenant(
            TenantLicence("op-br", "BR", "SPA/MF/2024-01")
        )
        with pytest.raises(ProvisioningError) as exc:
            provisioner.enable_prediction_markets(
                "op-br",
                [MarketCategory.FOOTBALL, MarketCategory.POLITICS],
                fee_bps=100,
                partner_route="matchbook",
            )
        assert "POLITICS" in str(exc.value)

    def test_brazil_wrong_partner_route_denied(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-br", "BR", "SPA/MF/2024-01")
        )
        with pytest.raises(ProvisioningError):
            provisioner.enable_prediction_markets(
                "op-br",
                [MarketCategory.FOOTBALL],
                fee_bps=100,
                partner_route="fanatics-markets",
            )


class TestEnableBlocked:
    def test_germany_tenant_denied(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-de", "DE", "n/a")
        )
        with pytest.raises(ProvisioningError):
            provisioner.enable_prediction_markets(
                "op-de", [MarketCategory.FOOTBALL], fee_bps=100
            )


class TestVersioning:
    def test_version_increments_on_enable_disable_enable(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        v0, _ = provisioner.config_for("op-gi")

        provisioner.enable_prediction_markets(
            "op-gi", [MarketCategory.FOOTBALL], fee_bps=50
        )
        v1, c1 = provisioner.config_for("op-gi")
        assert v1 == v0 + 1
        assert c1.enabled is True

        provisioner.disable_prediction_markets("op-gi")
        v2, c2 = provisioner.config_for("op-gi")
        assert v2 == v1 + 1
        assert c2.enabled is False

        provisioner.enable_prediction_markets(
            "op-gi", [MarketCategory.FOOTBALL], fee_bps=50
        )
        v3, c3 = provisioner.config_for("op-gi")
        assert v3 == v2 + 1
        assert c3.enabled is True

    def test_failed_enable_does_not_bump_version(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-de", "DE", "n/a")
        )
        v0, _ = provisioner.config_for("op-de")
        with pytest.raises(ProvisioningError):
            provisioner.enable_prediction_markets(
                "op-de", [MarketCategory.FOOTBALL], fee_bps=50
            )
        v1, _ = provisioner.config_for("op-de")
        assert v1 == v0


class TestDistribute:
    def test_distribute_includes_disabled_default_for_never_enabled(
        self, provisioner
    ):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        provisioner.register_tenant(
            TenantLicence("op-br", "BR", "SPA/MF/2024-01")
        )
        provisioner.enable_prediction_markets(
            "op-gi", [MarketCategory.FOOTBALL], fee_bps=50
        )

        snapshot = provisioner.distribute()
        assert set(snapshot) == {"op-gi", "op-br"}
        assert snapshot["op-gi"]["config"].enabled is True
        assert snapshot["op-br"]["config"].enabled is False
        assert snapshot["op-br"]["config"].categories == ()
        assert snapshot["op-br"]["version"] == 1

    def test_audit_log_records_lifecycle_events(self, provisioner):
        provisioner.register_tenant(
            TenantLicence("op-gi", "GI", "LGA/167/2026")
        )
        provisioner.enable_prediction_markets(
            "op-gi", [MarketCategory.FOOTBALL], fee_bps=50
        )
        provisioner.disable_prediction_markets("op-gi")
        events = [e for (_, tid, e) in provisioner.audit_log if tid == "op-gi"]
        assert any("registered" in e for e in events)
        assert any("enabled" in e for e in events)
        assert any(e == "disabled" for e in events)
