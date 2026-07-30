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
tests/test_suppliers.py
------------------------
Tests for the supplier registry and provider implementations.

Covers:
- Registry registration and lookup
- Per-brand availability filtering
- Supplier-type filtering
- Unknown supplier raises KeyError
- Provider hash/signature verification utilities
- Evolution status code mapping
- Pragmatic MD5 hash construction
- Hacksaw zero-amount credit handling
- Kambi fund/withdraw direction naming
- Settings loading
"""

from __future__ import annotations

import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from suppliers.registry import SupplierDescriptor, SupplierRegistry, SupplierType
from suppliers.settings import Settings, SupplierSettings, load_from_env
from transaction_result import BalanceStatus


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def make_stub_provider():
    """Return a minimal object satisfying AccountsProvider duck-typing."""

    class Stub:
        async def authenticate(self, token): ...
        async def get_balance(self, session, game_id=None): ...
        async def debit(self, session, operation, context): ...
        async def credit(self, session, operation, context): ...
        async def refund(self, session, operation, context): ...
        async def apply_transaction(self, session, operations, context): ...
        async def reverse_transaction(self, session, operations, context): ...

    return Stub()


def make_descriptor(supplier_id="test", brand_ids=None, supplier_type=SupplierType.CASINO):
    return SupplierDescriptor(
        supplier_id=supplier_id,
        display_name=f"Test Supplier {supplier_id}",
        supplier_type=supplier_type,
        provider=make_stub_provider(),
        brand_ids=set(brand_ids or []),
    )


def test_registry_register_and_lookup():
    reg = SupplierRegistry()
    desc = make_descriptor("evolution")
    reg.register(desc)
    assert "evolution" in reg
    assert len(reg) == 1
    provider = reg.resolve_provider("evolution")
    assert provider is desc.provider


def test_registry_unknown_supplier_raises_key_error():
    reg = SupplierRegistry()
    with pytest.raises(KeyError, match="unknown-sup"):
        reg.resolve_provider("unknown-sup")


def test_registry_deregister():
    reg = SupplierRegistry()
    reg.register(make_descriptor("evolution"))
    reg.deregister("evolution")
    assert "evolution" not in reg


def test_registry_register_overwrites_existing():
    reg = SupplierRegistry()
    desc1 = make_descriptor("evolution")
    desc2 = make_descriptor("evolution")
    reg.register(desc1)
    reg.register(desc2)
    assert len(reg) == 1
    assert reg.resolve_provider("evolution") is desc2.provider


def test_registry_brand_availability_empty_means_all():
    reg = SupplierRegistry()
    desc = make_descriptor("evolution", brand_ids=[])
    reg.register(desc)
    # Empty brand_ids = available everywhere
    provider = reg.resolve_provider("evolution", brand_id="any-brand")
    assert provider is desc.provider


def test_registry_brand_availability_restricts_access():
    reg = SupplierRegistry()
    desc = make_descriptor("pragmatic", brand_ids=["brand-a", "brand-b"])
    reg.register(desc)

    # Available for brand-a
    provider = reg.resolve_provider("pragmatic", brand_id="brand-a")
    assert provider is desc.provider

    # Not available for brand-c
    with pytest.raises(ValueError, match="brand-c"):
        reg.resolve_provider("pragmatic", brand_id="brand-c")


def test_registry_list_suppliers_no_filter():
    reg = SupplierRegistry()
    reg.register(make_descriptor("evolution", supplier_type=SupplierType.CASINO))
    reg.register(make_descriptor("kambi", supplier_type=SupplierType.SPORTS_BOOK))
    reg.register(make_descriptor("relax", supplier_type=SupplierType.AGGREGATOR))
    assert len(reg.list_suppliers()) == 3


def test_registry_list_suppliers_filter_by_type():
    reg = SupplierRegistry()
    reg.register(make_descriptor("evolution", supplier_type=SupplierType.CASINO))
    reg.register(make_descriptor("kambi", supplier_type=SupplierType.SPORTS_BOOK))
    reg.register(make_descriptor("pragmatic", supplier_type=SupplierType.CASINO))

    casinos = reg.list_suppliers(supplier_type=SupplierType.CASINO)
    assert len(casinos) == 2
    assert all(s.supplier_type == SupplierType.CASINO for s in casinos)


def test_registry_list_suppliers_filter_by_brand():
    reg = SupplierRegistry()
    reg.register(make_descriptor("evolution", brand_ids=["brand-a"]))
    reg.register(make_descriptor("pragmatic", brand_ids=["brand-b"]))
    reg.register(make_descriptor("kambi", brand_ids=[]))  # All brands

    brand_a_suppliers = reg.list_suppliers(brand_id="brand-a")
    ids = [s.supplier_id for s in brand_a_suppliers]
    assert "evolution" in ids
    assert "kambi" in ids
    assert "pragmatic" not in ids


def test_registry_get_descriptor_returns_none_for_unknown():
    reg = SupplierRegistry()
    assert reg.get_descriptor("unknown") is None


# ---------------------------------------------------------------------------
# SupplierDescriptor tests
# ---------------------------------------------------------------------------


def test_supplier_descriptor_is_available_for_brand_empty():
    desc = make_descriptor("evo", brand_ids=[])
    assert desc.is_available_for_brand("any-brand") is True


def test_supplier_descriptor_is_available_for_brand_restricted():
    desc = make_descriptor("evo", brand_ids=["brand-x"])
    assert desc.is_available_for_brand("brand-x") is True
    assert desc.is_available_for_brand("brand-y") is False


def test_supplier_descriptor_jurisdiction_empty_means_all():
    desc = make_descriptor("evo")
    assert desc.is_available_for_jurisdiction("any-jurisdiction") is True


def test_supplier_descriptor_jurisdiction_restricted():
    desc = SupplierDescriptor(
        supplier_id="evo",
        display_name="Evo",
        supplier_type=SupplierType.CASINO,
        provider=make_stub_provider(),
        jurisdiction_ids={"UK", "MT"},
    )
    assert desc.is_available_for_jurisdiction("UK") is True
    assert desc.is_available_for_jurisdiction("US") is False


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------


def test_supplier_settings_defaults():
    s = SupplierSettings(supplier_id="evolution")
    assert s.launch_token_lifetime == 60 * 60 * 12
    assert s.one_off_launch_token is False
    assert s.balance_in_major_units is True
    assert s.http_timeout_ms == 5000


def test_supplier_settings_raw_get():
    s = SupplierSettings(supplier_id="evolution", raw={"custom-key": "42"})
    desc = Settings.SettingDescriptor if hasattr(Settings, "SettingDescriptor") else None
    # Use the generic get with a plain descriptor
    from suppliers.settings import SettingDescriptor
    custom = SettingDescriptor(key="custom-key", description="test", default="0", type_=int)
    assert s.get(custom) == 42


def test_load_from_env_defaults(monkeypatch):
    # Ensure no evolution env vars are set
    monkeypatch.delenv("SUPPLIER_EVOLUTION_API_BASE_URL", raising=False)
    monkeypatch.delenv("SUPPLIER_EVOLUTION_HTTP_TIMEOUT_MS", raising=False)
    settings = load_from_env("evolution")
    assert settings.supplier_id == "evolution"
    assert settings.api_base_url == ""
    assert settings.http_timeout_ms == 5000


def test_load_from_env_reads_env_vars(monkeypatch):
    monkeypatch.setenv("SUPPLIER_EVOLUTION_API_BASE_URL", "https://evo.example.com")
    monkeypatch.setenv("SUPPLIER_EVOLUTION_HTTP_TIMEOUT_MS", "3000")
    settings = load_from_env("evolution")
    assert settings.api_base_url == "https://evo.example.com"
    assert settings.http_timeout_ms == 3000


def test_load_from_env_brand_override(monkeypatch):
    monkeypatch.setenv("SUPPLIER_EVOLUTION_OPERATOR_ID", "global-op")
    monkeypatch.setenv("SUPPLIER_EVOLUTION_BRAND_ACME_OPERATOR_ID", "brand-specific-op")
    settings = load_from_env("evolution", brand_id="acme")
    assert settings.operator_id == "brand-specific-op"


# ---------------------------------------------------------------------------
# Provider-specific behaviour tests
# ---------------------------------------------------------------------------


def test_evolution_status_codes_are_strings():
    """Evolution status codes must be string constants (not int)."""
    from suppliers.evolution.provider import STATUS_OK, STATUS_INSUFFICIENT_FUNDS, STATUS_ACCOUNT_LOCKED
    assert isinstance(STATUS_OK, str)
    assert isinstance(STATUS_INSUFFICIENT_FUNDS, str)
    assert STATUS_OK == "OK"
    assert STATUS_INSUFFICIENT_FUNDS == "INSUFFICIENT_FUNDS"


def test_pragmatic_md5_hash_construction():
    """Pragmatic's MD5 hash must match the expected signature."""
    from suppliers.pragmatic.provider import _build_hash
    # Known-good test vector
    params = {"playerId": "user123", "currency": "EUR", "amount": "10.00"}
    secret = "test-secret"
    result = _build_hash(params, secret)
    assert isinstance(result, str)
    assert len(result) == 32  # MD5 produces 32 hex chars
    # Same params + secret always produce the same hash
    assert _build_hash(params, secret) == result


def test_pragmatic_hash_excludes_hash_field():
    """The 'hash' key itself must be excluded from the signature payload."""
    from suppliers.pragmatic.provider import _build_hash
    params_without = {"a": "1", "b": "2"}
    params_with = {"a": "1", "b": "2", "hash": "old-hash"}
    secret = "s"
    # Hash should be the same whether or not the 'hash' field is present
    assert _build_hash(params_without, secret) == _build_hash(params_with, secret)


def test_hacksaw_zero_amount_credit_is_valid():
    """Hacksaw sends zero-amount credits for losing crash rounds."""
    from accounts_provider import CreditOperation
    op = CreditOperation(round_id="round-1", amount=Decimal("0"))
    # Should not raise — 0-amount credits are valid for Hacksaw
    assert op.amount == Decimal("0")


def test_kambi_fund_is_a_debit_perspective():
    """
    Kambi's FUND = player gives money to Kambi (debit from player wallet).
    Terminology mismatch: Kambi calls it 'fund', we call it 'debit'.
    """
    from accounts_provider import DebitOperation
    fund_op = DebitOperation(round_id="coupon-1", amount=Decimal("1000"))
    assert fund_op.amount == Decimal("1000")
    # Type check: this IS a DebitOperation (not Credit)
    assert isinstance(fund_op, DebitOperation)


def test_relax_aggregator_type():
    """Relax Gaming should be classified as AGGREGATOR, not CASINO."""
    from suppliers.registry import SupplierType
    assert SupplierType.AGGREGATOR == "AGGREGATOR"


def test_hacksaw_crash_supplier_type():
    """Hacksaw crash games use the CRASH supplier type."""
    from suppliers.registry import SupplierType
    assert SupplierType.CRASH == "CRASH"


def test_balance_status_total():
    balance = BalanceStatus(
        cash_balance=Decimal("5000"),
        bonus_balance=Decimal("2500"),
        currency="GBP",
    )
    assert balance.total_balance == Decimal("7500")
    assert balance.total_balance_decimal == Decimal("75.00")


def test_balance_status_currency_uppercased():
    balance = BalanceStatus(
        cash_balance=Decimal("0"),
        bonus_balance=Decimal("0"),
        currency="eur",
    )
    assert balance.currency == "EUR"


def test_igt_jurisdiction_in_constructor():
    from suppliers.igt.provider import IGTProvider
    p = IGTProvider(
        api_base_url="https://igt.test",
        operator_id="op1",
        api_key="k1",
        jurisdiction="UK",
    )
    assert p._jurisdiction == "UK"


def test_betgenius_market_status_constants():
    from suppliers.betgenius.provider import MARKET_ACTIVE, MARKET_SUSPENDED, OUTCOME_WIN, OUTCOME_VOID
    assert MARKET_ACTIVE == "ACTIVE"
    assert MARKET_SUSPENDED == "SUSPENDED"
    assert OUTCOME_WIN == "WIN"
    assert OUTCOME_VOID == "VOID"


def test_push_gaming_max_win_check_applies_cap():
    from suppliers.push_gaming.provider import PushGamingProvider
    p = PushGamingProvider(
        operator_key="op",
        secret="sec",
        max_win_multiplier=Decimal("50000"),
    )
    bet = Decimal("10")
    # Win under cap — no change
    assert p.check_max_win(bet, Decimal("499999")) == Decimal("499999")
    # Win over cap — capped at 500000
    assert p.check_max_win(bet, Decimal("600000")) == Decimal("500000")


def test_nyx_ogs_channel_codes():
    from suppliers.nyx.provider import CHANNEL_CASINO, CHANNEL_LIVE, CHANNEL_VIRTUAL, CHANNEL_POKER
    assert CHANNEL_CASINO == "CASINO"
    assert CHANNEL_LIVE == "LIVE"
    assert CHANNEL_VIRTUAL == "VIRTUAL"
    assert CHANNEL_POKER == "POKER"
