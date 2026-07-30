# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Reporting Supplier abstraction
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
#
# Each game supplier (IGT, NetEnt, Evolution, Kambi) implements this ABC.
# The multi-supplier aggregation pattern lets the report engine collect data
# uniformly regardless of how each supplier delivers it:
#   - IGT/NetEnt/Evolution: parse CSV files from SFTP drops
#   - Kambi: query the platform database directly
#
# This abstraction is critical because US regulators require a unified report
# that merges data from all active Remote Gaming Servers (RGS).
# =============================================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Sequence

from models import StuckBetRow, WsrData


class ReportingSupplier(ABC):
    """
    Base class for all US regulatory reporting suppliers.
    Each implementation is responsible for fetching its own data
    and mapping it to the unified WsrData / StuckBetRow formats.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_wsr_data(self, db, from_date: datetime) -> Sequence[WsrData]: ...

    @abstractmethod
    def get_stuck_bets(self, db, from_date: datetime) -> Sequence[StuckBetRow]: ...
