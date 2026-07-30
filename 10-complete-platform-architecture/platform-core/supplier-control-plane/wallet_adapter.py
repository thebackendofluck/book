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
wallet_adapter.py
-----------------
WalletAdapter interface and implementations for the Supplier Control Plane.

Domain handlers (GameRoundHandler, WalletHandler) use this adapter to query
player wallet balances instead of returning placeholder values.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class WalletAdapter(ABC):
    """Abstract interface for querying player wallet balances."""

    @abstractmethod
    def get_balance(self, player_id: str) -> float:
        """Return the player's current balance."""
        ...


class StubWalletAdapter(WalletAdapter):
    """In-memory wallet adapter for testing."""

    def __init__(self) -> None:
        self._balances: dict[str, float] = {}

    def set_balance(self, player_id: str, balance: float) -> None:
        self._balances[player_id] = balance

    def get_balance(self, player_id: str) -> float:
        return self._balances.get(player_id, 0.0)


class HttpWalletAdapter(WalletAdapter):
    """
    Production wallet adapter that calls the Wallet Service REST API.

    Expected API: GET {base_url}/players/{player_id}/balance
    Response: {"balance": 150.50}
    """

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def get_balance(self, player_id: str) -> float:
        import urllib.request
        url = f"{self._base_url}/players/{player_id}/balance"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return float(resp.json()["balance"])
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return float(data["balance"])
