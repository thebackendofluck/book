# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Unit tests for SupplierConfig and SupplierSettingsManager.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from acmetocasino.gameservice.suppliers.settings import SupplierConfig, SupplierSettingsManager


def _base_raw(supplier_id: str = "pragmatic") -> dict:
    return {
        "supplier_id": supplier_id,
        "display_name": "Pragmatic Play",
        "api_base_url": "https://api.pragmaticplay.net",
        "supported_currencies": ["EUR", "GBP"],
    }


def test_supplier_config_validates_correctly() -> None:
    cfg = SupplierConfig(**_base_raw())
    assert cfg.supplier_id == "pragmatic"
    assert cfg.timeout_seconds == 30.0
    assert cfg.max_retries == 3


def test_supplier_config_empty_supplier_id_rejected() -> None:
    raw = _base_raw()
    raw["supplier_id"] = ""
    with pytest.raises(ValidationError):
        SupplierConfig(**raw)


def test_supplier_config_is_brand_enabled_wildcard() -> None:
    cfg = SupplierConfig(**_base_raw())
    assert cfg.is_brand_enabled("any_brand") is True


def test_supplier_config_is_brand_enabled_specific() -> None:
    raw = _base_raw()
    raw["enabled_brands"] = ["acme_uk", "acme_mt"]
    cfg = SupplierConfig(**raw)
    assert cfg.is_brand_enabled("acme_uk") is True
    assert cfg.is_brand_enabled("acme_br") is False


def test_supplier_config_is_jurisdiction_blocked() -> None:
    raw = _base_raw()
    raw["blocked_jurisdictions"] = ["US", "DE"]
    cfg = SupplierConfig(**raw)
    assert cfg.is_jurisdiction_blocked("US") is True
    assert cfg.is_jurisdiction_blocked("MGA") is False


def test_supplier_config_jurisdiction_check_case_insensitive() -> None:
    raw = _base_raw()
    raw["blocked_jurisdictions"] = ["us"]
    cfg = SupplierConfig(**raw)
    assert cfg.is_jurisdiction_blocked("US") is True


def test_supplier_config_callback_url_resolves() -> None:
    raw = _base_raw()
    raw["callback_url_template"] = "https://{brand_id}.acme.com/wallet/{supplier_id}"
    cfg = SupplierConfig(**raw)
    url = cfg.callback_url_for("acme_uk")
    assert url == "https://acme_uk.acme.com/wallet/pragmatic"


def test_supplier_config_callback_url_empty_when_no_template() -> None:
    cfg = SupplierConfig(**_base_raw())
    assert cfg.callback_url_for("acme_uk") == ""


def test_settings_manager_load_and_get_config() -> None:
    mgr = SupplierSettingsManager()
    mgr.load({"pragmatic": _base_raw()})
    cfg = mgr.get_config("pragmatic")
    assert cfg.supplier_id == "pragmatic"


def test_settings_manager_unknown_supplier_raises() -> None:
    mgr = SupplierSettingsManager()
    with pytest.raises(KeyError):
        mgr.get_config("unknown")


def test_settings_manager_brand_override_applied() -> None:
    raw = _base_raw()
    raw["api_key"] = "BASE_KEY"
    raw["brand_overrides"] = {"acme_br": {"api_key": "BR_KEY"}}
    mgr = SupplierSettingsManager()
    mgr.load({"pragmatic": raw})
    cfg = mgr.get_config_for_brand("pragmatic", "acme_br")
    assert cfg.api_key == "BR_KEY"


def test_settings_manager_brand_no_override_uses_base() -> None:
    raw = _base_raw()
    raw["api_key"] = "BASE_KEY"
    raw["brand_overrides"] = {"acme_br": {"api_key": "BR_KEY"}}
    mgr = SupplierSettingsManager()
    mgr.load({"pragmatic": raw})
    cfg = mgr.get_config_for_brand("pragmatic", "acme_uk")
    assert cfg.api_key == "BASE_KEY"


def test_settings_manager_update_config() -> None:
    mgr = SupplierSettingsManager()
    mgr.load({"pragmatic": _base_raw()})
    mgr.update_config("pragmatic", {"api_key": "NEW_KEY"})
    cfg = mgr.get_config("pragmatic")
    assert cfg.api_key == "NEW_KEY"


def test_settings_manager_update_unknown_supplier_raises() -> None:
    mgr = SupplierSettingsManager()
    with pytest.raises(KeyError):
        mgr.update_config("unknown", {"api_key": "x"})


def test_settings_manager_registered_suppliers() -> None:
    mgr = SupplierSettingsManager()
    mgr.load({"pragmatic": _base_raw(), "netent": {**_base_raw(), "supplier_id": "netent", "display_name": "NetEnt"}})
    suppliers = mgr.registered_suppliers()
    assert "pragmatic" in suppliers
    assert "netent" in suppliers
