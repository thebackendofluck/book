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
gameservice.suppliers — Supplier Integration Layer
====================================================

This package contains the supplier registry, base adapter contract, and
concrete adapters for every integrated gaming supplier.

Architecture overview
---------------------
The integration layer follows the **Adapter** pattern: each supplier's
idiosyncratic API is wrapped behind a uniform ``SupplierAdapter`` protocol
so that the platform core never needs to know which supplier it is talking to.

Directory layout::

    suppliers/
    ├── __init__.py          ← this file; public re-exports
    ├── base.py              ← SupplierAdapter protocol + BaseSupplierAdapter
    ├── registry.py          ← SupplierRegistry — resolve adapters at runtime
    ├── settings.py          ← SupplierConfig + SupplierSettingsManager
    ├── capability_matrix.py ← CapabilityMatrix — query supplier capabilities
    ├── evolution/           ← Evolution Gaming (live casino, PUSH)
    ├── pragmatic/           ← Pragmatic Play (slots + live, SEAMLESS)
    ├── netent/              ← NetEnt (slots, SEAMLESS)
    ├── kambi/               ← Kambi (sportsbook, PULL)
    ├── playngo/             ← Play'n GO (slots, SEAMLESS)
    ├── hacksaw/             ← Hacksaw Gaming (crash + slots, SEAMLESS)
    ├── push_gaming/         ← Push Gaming (slots, SEAMLESS)
    ├── igt/                 ← IGT (slots + tables, PULL/SOAP)
    ├── nyx/                 ← NYX / Scientific Games (aggregator, SEAMLESS)
    ├── relax/               ← Relax Gaming (aggregator, SEAMLESS)
    └── betgenius/           ← Betgenius (sports data feed, PUSH)

Usage::

    from acmetocasino.gameservice.suppliers import SupplierRegistry
    from acmetocasino.gameservice.suppliers.pragmatic import PragmaticAdapter

    registry = SupplierRegistry()
    registry.register("pragmatic", PragmaticAdapter)
    adapter = registry.resolve("pragmatic", brand_id="acme_uk", jurisdiction="MGA")
    result = adapter.launch_session(request)
"""

from __future__ import annotations

from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    SupplierAdapter,
    SupplierCapabilities,
    SupplierInfo,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.capability_matrix import CapabilityMatrix
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry
from acmetocasino.gameservice.suppliers.settings import (
    SupplierConfig,
    SupplierSettingsManager,
)

__all__ = [
    "BaseSupplierAdapter",
    "CapabilityMatrix",
    "LaunchResult",
    "SupplierAdapter",
    "SupplierCapabilities",
    "SupplierConfig",
    "SupplierInfo",
    "SupplierRegistry",
    "SupplierSettingsManager",
    "TransactionResult",
]
