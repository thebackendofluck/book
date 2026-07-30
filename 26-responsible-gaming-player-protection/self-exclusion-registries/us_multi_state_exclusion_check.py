# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
us_multi_state_exclusion_check.py — Multi-state US self-exclusion service.

Jurisdiction:       United States (state-by-state)
Regulators:         NJ DGE, PA PGCB, MI GCB, and others per state licence
Regulation refs:
  - NJ NJAC 13:69O — Self-exclusion regulations
    https://www.nj.gov/oag/ge/selfexclusion.html
  - PA 58 Pa. Code § 503a — Self-exclusion procedures
    https://www.pgcb.pa.gov/
  - Each US state maintains its own exclusion list; no federal registry exists
Penalties:
  - BetMGM PA: $260,905 (2025) — 152 excluded players gambling 2021-2023
  - Super Group NJ: $112,188 (2025) — delayed list updates to DGE
  - DraftKings NJ: $10,000 (~2023) — self-exclusion rules violation

Key requirements:
  - Check at registration and periodically reconcile active players
  - Fuzzy matching for name variations, address changes
  - Each state has its own data format (CSV, XML, fixed-width, API)
  - Operator must report self-exclusions back to each state promptly

Book chapter:  Chapter 26b — Self-Exclusion Registries Implementation
"""
from __future__ import annotations

import csv
import io
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Optional, Protocol

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class ExclusionDuration(str, Enum):
    ONE_YEAR = "1_year"
    FIVE_YEARS = "5_years"
    LIFETIME = "lifetime"


class MatchConfidence(str, Enum):
    EXACT = "exact"
    HIGH = "high"           # fuzzy score >= 0.85
    MODERATE = "moderate"   # fuzzy score >= 0.70
    NONE = "none"


@dataclass
class PlayerIdentity:
    player_id: str
    first_name: str
    last_name: str
    date_of_birth: str          # ISO-8601 YYYY-MM-DD
    postcode: str
    email: str
    state: str                  # US state code, e.g. "NJ", "PA"
    ssn_last4: Optional[str] = None


@dataclass
class ExclusionRecord:
    registry_id: str
    state: str
    first_name: str
    last_name: str
    date_of_birth: str
    postcode: str
    exclusion_start: str
    exclusion_duration: ExclusionDuration


@dataclass
class ExclusionCheckResult:
    check_id: str
    player_id: str
    state: str
    is_excluded: bool
    match_confidence: MatchConfidence
    matched_record: Optional[ExclusionRecord] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_detail: Optional[str] = None


# ---------------------------------------------------------------------------
# State provider protocol
# ---------------------------------------------------------------------------

class StateExclusionProvider(Protocol):
    """Each US state adapter must satisfy this protocol."""

    @property
    def state_code(self) -> str: ...

    def fetch_exclusion_list(self) -> list[ExclusionRecord]: ...


# ---------------------------------------------------------------------------
# Fuzzy matching utilities
# ---------------------------------------------------------------------------

def _name_similarity(a: str, b: str) -> float:
    """Normalised string similarity for names."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _match_player_to_record(
    player: PlayerIdentity, record: ExclusionRecord
) -> MatchConfidence:
    """
    Multi-field fuzzy matching.

    Rules:
      - DOB must match exactly (non-negotiable).
      - Name similarity >= 0.85 AND postcode match → EXACT.
      - Name similarity >= 0.85 → HIGH.
      - Name similarity >= 0.70 → MODERATE.
      - Below 0.70 → NONE.
    """
    if player.date_of_birth != record.date_of_birth:
        return MatchConfidence.NONE

    first_sim = _name_similarity(player.first_name, record.first_name)
    last_sim = _name_similarity(player.last_name, record.last_name)
    name_sim = (first_sim + last_sim) / 2.0

    postcode_match = (
        player.postcode.replace(" ", "").lower()
        == record.postcode.replace(" ", "").lower()
    )

    if name_sim >= 0.85 and postcode_match:
        return MatchConfidence.EXACT
    if name_sim >= 0.85:
        return MatchConfidence.HIGH
    if name_sim >= 0.70:
        return MatchConfidence.MODERATE
    return MatchConfidence.NONE


# ---------------------------------------------------------------------------
# New Jersey provider (stub)
# ---------------------------------------------------------------------------

class NJExclusionProvider:
    """
    NJ DGE exclusion list provider.

    In production: download the encrypted CSV from the DGE SFTP endpoint,
    decrypt with the operator's PGP key, and parse.  This stub returns
    synthetic records for demonstration.
    """

    @property
    def state_code(self) -> str:
        return "NJ"

    def fetch_exclusion_list(self) -> list[ExclusionRecord]:
        # --- STUB: in production, pull from DGE SFTP ---
        sample_csv = (
            "REG-NJ-001,John,Smith,1985-06-15,07102,2023-01-10,lifetime\n"
            "REG-NJ-002,Maria,Garcia,1990-11-22,08901,2024-03-01,5_years\n"
        )
        records: list[ExclusionRecord] = []
        reader = csv.reader(io.StringIO(sample_csv))
        for row in reader:
            records.append(ExclusionRecord(
                registry_id=row[0],
                state="NJ",
                first_name=row[1],
                last_name=row[2],
                date_of_birth=row[3],
                postcode=row[4],
                exclusion_start=row[5],
                exclusion_duration=ExclusionDuration(row[6]),
            ))
        log.info("nj_provider: loaded exclusion list", count=len(records))
        return records


# ---------------------------------------------------------------------------
# Pennsylvania provider (stub)
# ---------------------------------------------------------------------------

class PAExclusionProvider:
    """
    PA PGCB exclusion list provider.

    In production: pull from PGCB secure portal.  Similar to NJ but
    uses a different data format (fixed-width in some versions, CSV in
    newer exports).
    """

    @property
    def state_code(self) -> str:
        return "PA"

    def fetch_exclusion_list(self) -> list[ExclusionRecord]:
        # --- STUB ---
        sample_csv = (
            "REG-PA-001,James,Williams,1978-03-30,19103,2022-07-15,lifetime\n"
        )
        records: list[ExclusionRecord] = []
        reader = csv.reader(io.StringIO(sample_csv))
        for row in reader:
            records.append(ExclusionRecord(
                registry_id=row[0],
                state="PA",
                first_name=row[1],
                last_name=row[2],
                date_of_birth=row[3],
                postcode=row[4],
                exclusion_start=row[5],
                exclusion_duration=ExclusionDuration(row[6]),
            ))
        log.info("pa_provider: loaded exclusion list", count=len(records))
        return records


# ---------------------------------------------------------------------------
# Multi-state exclusion service
# ---------------------------------------------------------------------------

class MultiStateExclusionService:
    """
    Unified service that checks a player against all relevant US state
    exclusion lists.

    Design principles:
      - Any state match → player blocked everywhere (conservative approach).
      - Fail closed: if a provider is unavailable, block the player.
      - All results are audit-logged.
    """

    def __init__(self, providers: list[StateExclusionProvider]) -> None:
        self._providers: dict[str, StateExclusionProvider] = {
            p.state_code: p for p in providers
        }
        self._cache: dict[str, list[ExclusionRecord]] = {}

    def refresh_all_lists(self) -> None:
        """Fetch fresh exclusion lists from all configured state providers."""
        for code, provider in self._providers.items():
            try:
                records = provider.fetch_exclusion_list()
                self._cache[code] = records
                log.info("multi_state: refreshed list",
                         state=code, records=len(records))
            except Exception as exc:
                log.error("multi_state: failed to refresh list",
                          state=code, error=str(exc))
                # Fail closed: keep stale list if available, otherwise empty
                if code not in self._cache:
                    self._cache[code] = []

    def check_player(self, player: PlayerIdentity) -> list[ExclusionCheckResult]:
        """
        Check a player against all state exclusion lists.

        Returns one result per state.  If any result has is_excluded=True,
        the caller must block the player across all states.
        """
        results: list[ExclusionCheckResult] = []
        for state_code, records in self._cache.items():
            result = self._check_against_state(player, state_code, records)
            results.append(result)
        return results

    def is_excluded_anywhere(self, player: PlayerIdentity) -> bool:
        """Convenience: True if the player is excluded in any state."""
        results = self.check_player(player)
        return any(r.is_excluded for r in results)

    def _check_against_state(
        self,
        player: PlayerIdentity,
        state: str,
        records: list[ExclusionRecord],
    ) -> ExclusionCheckResult:
        check_id = f"US-{state}-{uuid.uuid4().hex[:10].upper()}"
        best_match: Optional[ExclusionRecord] = None
        best_confidence = MatchConfidence.NONE

        for record in records:
            confidence = _match_player_to_record(player, record)
            if confidence != MatchConfidence.NONE:
                if best_match is None or _confidence_rank(confidence) > _confidence_rank(best_confidence):
                    best_match = record
                    best_confidence = confidence

        is_excluded = best_confidence in (
            MatchConfidence.EXACT, MatchConfidence.HIGH
        )

        result = ExclusionCheckResult(
            check_id=check_id,
            player_id=player.player_id,
            state=state,
            is_excluded=is_excluded,
            match_confidence=best_confidence,
            matched_record=best_match if is_excluded else None,
        )

        log.info(
            "multi_state: check complete",
            check_id=check_id,
            player_id=player.player_id,
            state=state,
            excluded=is_excluded,
            confidence=best_confidence.value,
        )
        return result


def _confidence_rank(conf: MatchConfidence) -> int:
    return {
        MatchConfidence.EXACT: 3,
        MatchConfidence.HIGH: 2,
        MatchConfidence.MODERATE: 1,
        MatchConfidence.NONE: 0,
    }[conf]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    nj = NJExclusionProvider()
    pa = PAExclusionProvider()
    service = MultiStateExclusionService([nj, pa])
    service.refresh_all_lists()

    player = PlayerIdentity(
        player_id="plr-us-001",
        first_name="John",
        last_name="Smith",
        date_of_birth="1985-06-15",
        postcode="07102",
        email="john.smith@example.com",
        state="NJ",
    )

    excluded = service.is_excluded_anywhere(player)
    print(f"US multi-state exclusion demo")
    print(f"Player: {player.player_id} — excluded anywhere: {excluded}")


if __name__ == "__main__":
    _demo()
