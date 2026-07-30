# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Reconciliation Service -- Adapter Interfaces and Implementations

Architecture: each external system (ledger, wallet, PSP, bank, tax) is
accessed through an abstract adapter interface (ABC). Stub implementations
are provided for testing; HTTP implementations are provided for production.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# Abstract adapter interfaces
# ---------------------------------------------------------------------------


class LedgerAdapter(ABC):
    """Abstract interface for querying the general ledger."""

    @abstractmethod
    def get_balance(self, account_id: str) -> int: ...


class WalletAdapter(ABC):
    """Abstract interface for the player wallet service."""

    @abstractmethod
    def get_balance(self, player_id: str) -> int: ...

    @abstractmethod
    def list_player_ids(self) -> list[str]: ...


class PSPAdapter(ABC):
    """Abstract interface for PSP settlement reporting."""

    @abstractmethod
    def get_position(self, psp_name: str) -> int: ...

    @abstractmethod
    def list_psp_names(self) -> list[str]: ...


class BankAdapter(ABC):
    """Abstract interface for the bank statement feed."""

    @abstractmethod
    def get_balance(self, account_name: str) -> int: ...

    @abstractmethod
    def list_accounts(self) -> list[str]: ...


class TaxAdapter(ABC):
    """Abstract interface for tax authority reporting."""

    @abstractmethod
    def get_liability(self, jurisdiction: str) -> int: ...

    @abstractmethod
    def list_jurisdictions(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# Stub implementations (for testing and book demos)
# ---------------------------------------------------------------------------


class StubLedgerAdapter(LedgerAdapter):
    """In-memory ledger adapter for testing."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, account_id: str, balance: int) -> None:
        self._balances[account_id] = balance

    def get_balance(self, account_id: str) -> int:
        return self._balances.get(account_id, 0)


class StubWalletAdapter(WalletAdapter):
    """In-memory wallet adapter for testing."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, player_id: str, balance: int) -> None:
        self._balances[player_id] = balance

    def get_balance(self, player_id: str) -> int:
        return self._balances.get(player_id, 0)

    def list_player_ids(self) -> list[str]:
        return list(self._balances.keys())


class StubPSPAdapter(PSPAdapter):
    """In-memory PSP adapter for testing."""

    def __init__(self) -> None:
        self._positions: dict[str, int] = {}

    def set_position(self, psp_name: str, balance: int) -> None:
        self._positions[psp_name] = balance

    def get_position(self, psp_name: str) -> int:
        return self._positions.get(psp_name, 0)

    def list_psp_names(self) -> list[str]:
        return list(self._positions.keys())


class StubBankAdapter(BankAdapter):
    """In-memory bank adapter for testing."""

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def set_balance(self, account_name: str, balance: int) -> None:
        self._balances[account_name] = balance

    def get_balance(self, account_name: str) -> int:
        return self._balances.get(account_name, 0)

    def list_accounts(self) -> list[str]:
        return list(self._balances.keys())


class StubTaxAdapter(TaxAdapter):
    """In-memory tax adapter for testing."""

    def __init__(self) -> None:
        self._liabilities: dict[str, int] = {}

    def set_liability(self, jurisdiction: str, amount: int) -> None:
        self._liabilities[jurisdiction] = amount

    def get_liability(self, jurisdiction: str) -> int:
        return self._liabilities.get(jurisdiction, 0)

    def list_jurisdictions(self) -> list[str]:
        return list(self._liabilities.keys())


# ---------------------------------------------------------------------------
# HTTP adapter implementations (for production)
# ---------------------------------------------------------------------------


class HttpLedgerAdapter(LedgerAdapter):
    """Production ledger adapter calling the Ledger Service REST API."""

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _get(self, path: str) -> dict[str, Any]:
        import urllib.request
        url = f"{self._base_url}{path}"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def get_balance(self, account_id: str) -> int:
        return int(self._get(f"/accounts/{account_id}/balance")["balance"])


class HttpWalletAdapter(WalletAdapter):
    """Production wallet adapter calling the Wallet Service REST API."""

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _get(self, path: str) -> dict[str, Any]:
        import urllib.request
        url = f"{self._base_url}{path}"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def get_balance(self, player_id: str) -> int:
        return int(self._get(f"/players/{player_id}/balance")["balance"])

    def list_player_ids(self) -> list[str]:
        return list(self._get("/players")["player_ids"])


class HttpPSPAdapter(PSPAdapter):
    """Production PSP adapter calling settlement reporting APIs."""

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _get(self, path: str) -> dict[str, Any]:
        import urllib.request
        url = f"{self._base_url}{path}"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def get_position(self, psp_name: str) -> int:
        return int(self._get(f"/positions/{psp_name}")["position"])

    def list_psp_names(self) -> list[str]:
        return list(self._get("/positions")["psp_names"])


class HttpBankAdapter(BankAdapter):
    """Production bank adapter calling the banking integration API."""

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _get(self, path: str) -> dict[str, Any]:
        import urllib.request
        url = f"{self._base_url}{path}"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def get_balance(self, account_name: str) -> int:
        return int(self._get(f"/accounts/{account_name}/balance")["balance"])

    def list_accounts(self) -> list[str]:
        return list(self._get("/accounts")["accounts"])


class HttpTaxAdapter(TaxAdapter):
    """Production tax adapter calling the tax authority reporting API."""

    def __init__(self, base_url: str, session: Any = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _get(self, path: str) -> dict[str, Any]:
        import urllib.request
        url = f"{self._base_url}{path}"
        if self._session is not None:
            resp = self._session.get(url)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def get_liability(self, jurisdiction: str) -> int:
        return int(self._get(f"/liabilities/{jurisdiction}")["liability"])

    def list_jurisdictions(self) -> list[str]:
        return list(self._get("/liabilities")["jurisdictions"])
