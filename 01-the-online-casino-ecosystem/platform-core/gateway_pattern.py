# Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Gateway Pattern: Site Gateway and User Gateway
# Source: Production casino platform (sanitized)
# Chapter 1 - The Online Casino Ecosystem
#
# The platform uses a two-tier gateway architecture:
# - SiteGateway: Handles brand-level operations (game catalog, site config)
# - UserGateway: Handles player-level operations (auth, balance, transactions)
#
# This separation enables the multi-brand model where each casino brand
# routes through its own gateway configuration while sharing the
# underlying platform services.
# =============================================================================

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# SITE GATEWAY (Brand-level routing and configuration)
# ---------------------------------------------------------------------------
# The SiteGateway is the entry point for brand-specific operations.
# It resolves which brand configuration to use based on the incoming
# request, provides game catalogs, and manages brand-level state.


class SiteGatewayRequestProcessor:
    """
    Brand-level operations routed through the site gateway:
    - Game catalog retrieval (filtered by brand, jurisdiction, country)
    - Site configuration (logos, themes, footer content)
    - Bonus/promotion catalog for the brand
    - Localized content delivery

    Each brand has its own URL routing but shares the same
    application instance, database, and game supplier connections.
    """


# ---------------------------------------------------------------------------
# USER GATEWAY (Player-level operations)
# ---------------------------------------------------------------------------
# The UserGateway handles all player-facing operations. Every API call
# from a player's browser or mobile app routes through here.


@dataclass
class XMLWrapper:
    tag: str
    attributes: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def set_key(self, key: str, value: str) -> None:
        self.attributes[key] = value

    @classmethod
    def create_new(cls, tag: str) -> "XMLWrapper":
        return cls(tag=tag)


class UserGatewayRequestProcessor:
    @staticmethod
    def create_response(response_type: str) -> XMLWrapper:
        """
        Creates a standardized XML response envelope.
        All user gateway responses follow this pattern:
        <response type="..." code="0" message="OK">...</response>

        The code field uses standardized error codes across all operations.
        """
        response = XMLWrapper.create_new("response")
        response.set_key("type", response_type)
        response.set_key("code", "0")
        response.set_key("message", "OK")
        return response


# ---------------------------------------------------------------------------
# ACCOUNTS BRIDGE (Python layer bridging gateways to the accounts system)
# ---------------------------------------------------------------------------
# The AccountsBridge is the modern layer that sits between supplier
# endpoints and the accounts system. It provides:
# - Per-player concurrency control (one operation at a time per player)
# - Transaction idempotency (deduplication via supplier reference)
# - Automatic retry/recovery for failed transactions


class TransactionInsertResult:
    pass


@dataclass
class UnprocessedTransaction(TransactionInsertResult):
    id: int
    previously_failed: bool


@dataclass
class AlreadyProcessedTransaction(TransactionInsertResult):
    id: int


@dataclass
class AlreadyRefundedTransaction(TransactionInsertResult):
    id: int


class AccountsBridge:
    """
    Per-player concurrency: ensures only one financial operation
    processes at a time for any given player. This prevents race
    conditions where two suppliers might try to debit the same
    balance simultaneously.
    """

    def __init__(self) -> None:
        self._locks: dict[int, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    def _get_lock(self, player_id: int) -> threading.Lock:
        with self._locks_mutex:
            if player_id not in self._locks:
                self._locks[player_id] = threading.Lock()
            return self._locks[player_id]

    def player_action(self, player_id: int):
        """
        Context manager for per-player locking.

        NOTE - deliberately no transaction at this level.
        We always want the PLAYER_TXN_REQUEST row committed
        (for audit trail) even if the actual transaction fails.
        The inner accounts system manages its own transactions.
        """
        return self._get_lock(player_id)


# ---------------------------------------------------------------------------
# REQUEST FLOW SUMMARY
# ---------------------------------------------------------------------------
#
# Player Browser/App
#     |
#     v
# [SiteGateway] -- Brand routing, game catalog, site config
#     |
#     v
# [UserGateway] -- Auth, balance queries, deposit/withdraw
#     |
#     v
# [AccountsBridge] -- Per-player locking, idempotency, transaction routing
#     |
#     v
# [AccountsProvider] -- Brand-specific accounts implementation
#     |
#     v
# [TransactionProcessor] -- Debit/credit with bonus wagering
#     |
#     v
# [Database] -- PostgreSQL with row-level locking
#
# Game Supplier (Evolution, NetEnt, etc.)
#     |
#     v
# [SupplierEndpoint] -- Supplier-specific HTTP/REST endpoint
#     |
#     v
# [AccountsBridge] -- Same path as above
