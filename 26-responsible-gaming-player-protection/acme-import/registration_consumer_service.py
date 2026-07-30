# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Kafka-driven registration-time self-exclusion matcher.

Python port of RegistrationConsumerService.scala referenced in chapter
26, section "Multi-Jurisdiction Registration-Time Matching (Acme Import
Import)". The service consumes registration events from Kafka, fetches
the player's PII, and runs two parallel matching pipelines against the
NJ DGE and PA DAP exclusion lists. Any match triggers an immediate
account lock.

The Scala original used `Future` composition for parallelism. Here we
use `asyncio.gather` for the same effect without bringing in a third-
party framework. The matcher functions themselves are deliberately
decoupled from the event loop so they can also be called from a
synchronous HTTP handler for manual compliance lookups (`SyncHttp`
mode documented in config.py).

This module is self-contained in the sense that it defines its own
matching primitives and stubs the external services (Kafka consumer,
PII lookup, lock task creation, alerting). A production deployment
would wire the same `match_registration` core function into a real
`aiokafka.AIOKafkaConsumer` and a real PII microservice client.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

# Directory name has a hyphen so it is not a valid Python package name;
# fall back to a sys.path insertion for the sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MatchingConfig, AcmeImportConfig  # noqa: E402

LOG = logging.getLogger("acme_import.registration_consumer")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerPII:
    """The PII the matcher needs to answer the exclusion question."""

    user_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    # SSN is optional because only NJ players supply it. Store it in
    # canonical form (digits only). Never log.
    ssn: str | None = None
    # Address components used by PA DAP
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


@dataclass(frozen=True)
class ExclusionRecord:
    """A row from an exclusion list that the matcher compares against."""

    source: str  # "nj-dge" or "pa-dap"
    first_name: str
    last_name: str
    date_of_birth: date | None
    ssn: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


@dataclass(frozen=True)
class MatchResult:
    """Outcome of a matcher run."""

    matched: bool
    source: str
    match_type: str  # "ssn-full", "ssn-partial-dob", "dob-lastname", "pa-fuzzy"
    score: float     # 0.0 = no match; 1.0 = exact
    notes: str = ""
    record: ExclusionRecord | None = None


NO_MATCH = MatchResult(matched=False, source="", match_type="none", score=0.0)


# ---------------------------------------------------------------------------
# External service protocols (injected in tests)
# ---------------------------------------------------------------------------


class PiiLookup(Protocol):
    """Fetches PII for a user id. Implementations round-trip a real
    microservice; tests provide in-memory stubs.
    """

    async def get(self, user_id: str) -> PlayerPII | None: ...


class ExclusionDatabase(Protocol):
    """Provides access to one exclusion list source."""

    @property
    def source_id(self) -> str: ...

    async def find_candidates(
        self, pii: PlayerPII
    ) -> list[ExclusionRecord]: ...


class LockTaskCreator(Protocol):
    """Creates an account lock task when a match is found."""

    async def create_lock(
        self, user_id: str, result: MatchResult
    ) -> None: ...


class Alerter(Protocol):
    """Notifies the compliance team of a match (OpsGenie, Slack, etc.)."""

    async def notify(self, user_id: str, result: MatchResult) -> None: ...


# ---------------------------------------------------------------------------
# Pure matching primitives
# ---------------------------------------------------------------------------


def normalize_ssn(ssn: str | None) -> str | None:
    """Strip non-digits from an SSN. Returns None for empty or missing."""
    if ssn is None:
        return None
    digits = "".join(c for c in ssn if c.isdigit())
    return digits or None


def levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    O(len(a) * len(b)) time, O(min(len(a), len(b))) memory. Written
    in plain Python so that the matcher has no external dependency for
    fuzzy matching and the algorithm is auditable by compliance
    reviewers.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            substitute_cost = previous[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert_cost, delete_cost, substitute_cost))
        previous = current
    return previous[-1]


def match_nj_dge(
    pii: PlayerPII,
    candidates: list[ExclusionRecord],
    config: MatchingConfig,
) -> MatchResult:
    """SSN-centric waterfall used by NJ DGE.

    Steps, in order, with the first hit winning:
      1. Full SSN match (both sides have full 9-digit SSN)
      2. Partial SSN (last N digits) + exact DOB
      3. Exact DOB + case-insensitive last name match (last resort)
    """
    pii_ssn = normalize_ssn(pii.ssn)
    partial_len = config.nj_dge_ssn_partial_digits

    for rec in candidates:
        if rec.source != "nj-dge":
            continue
        rec_ssn = normalize_ssn(rec.ssn)

        # Step 1: full SSN
        if pii_ssn and rec_ssn and len(pii_ssn) == 9 and pii_ssn == rec_ssn:
            return MatchResult(
                matched=True,
                source="nj-dge",
                match_type="ssn-full",
                score=1.0,
                notes="full SSN match",
                record=rec,
            )

        # Step 2: partial SSN + DOB
        if (
            pii_ssn
            and rec_ssn
            and len(pii_ssn) >= partial_len
            and len(rec_ssn) >= partial_len
            and pii_ssn[-partial_len:] == rec_ssn[-partial_len:]
            and rec.date_of_birth == pii.date_of_birth
        ):
            return MatchResult(
                matched=True,
                source="nj-dge",
                match_type="ssn-partial-dob",
                score=0.9,
                notes=f"partial SSN (last {partial_len}) + DOB match",
                record=rec,
            )

        # Step 3: DOB + last name
        if (
            rec.date_of_birth == pii.date_of_birth
            and rec.last_name.strip().lower() == pii.last_name.strip().lower()
        ):
            return MatchResult(
                matched=True,
                source="nj-dge",
                match_type="dob-lastname",
                score=0.7,
                notes="DOB + last name fallback",
                record=rec,
            )

    return MatchResult(matched=False, source="nj-dge", match_type="none", score=0.0)


def match_pa_dap(
    pii: PlayerPII,
    candidates: list[ExclusionRecord],
    config: MatchingConfig,
) -> MatchResult:
    """Fuzzy Levenshtein matcher used by PA DAP.

    Computes the edit distance between the concatenated last-name +
    first-name strings and reports a match when the distance is at or
    below the configured threshold and the DOB matches exactly.
    """
    player_key = f"{pii.last_name.lower()}|{pii.first_name.lower()}"
    best: MatchResult = MatchResult(
        matched=False, source="pa-dap", match_type="pa-fuzzy", score=0.0
    )

    for rec in candidates:
        if rec.source != "pa-dap":
            continue
        if rec.date_of_birth != pii.date_of_birth:
            continue
        rec_key = f"{rec.last_name.lower()}|{rec.first_name.lower()}"
        distance = levenshtein(player_key, rec_key)
        if distance <= config.pa_dap_levenshtein_max_distance:
            denominator = max(len(player_key), len(rec_key), 1)
            score = 1.0 - (distance / denominator)
            if not best.matched or score > best.score:
                best = MatchResult(
                    matched=True,
                    source="pa-dap",
                    match_type="pa-fuzzy",
                    score=score,
                    notes=f"Levenshtein distance {distance}",
                    record=rec,
                )
    return best


def merge_results(
    nj_result: MatchResult, pa_result: MatchResult
) -> MatchResult:
    """Combine parallel matcher outputs into a single result.

    If both match, the higher-score result wins; ties go to NJ because
    its SSN-based waterfall is stricter. If neither matches, returns
    NO_MATCH.
    """
    if nj_result.matched and pa_result.matched:
        return nj_result if nj_result.score >= pa_result.score else pa_result
    if nj_result.matched:
        return nj_result
    if pa_result.matched:
        return pa_result
    return NO_MATCH


# ---------------------------------------------------------------------------
# The async core: one function, two call sites (Kafka + HTTP)
# ---------------------------------------------------------------------------


@dataclass
class RegistrationConsumerService:
    """The composition root for the Acme Import matcher.

    All external dependencies are injected so the core logic remains
    pure. In production the Kafka consumer drives
    `handle_registration_event`; the SyncHttp endpoint calls
    `match_registration` directly and awaits the result.
    """

    config: AcmeImportConfig
    pii_lookup: PiiLookup
    nj_dge_db: ExclusionDatabase
    pa_dap_db: ExclusionDatabase
    lock_task: LockTaskCreator
    alerter: Alerter

    async def match_registration(self, user_id: str) -> MatchResult:
        """Core matching pipeline, callable from any context."""
        pii = await self.pii_lookup.get(user_id)
        if pii is None:
            LOG.warning("acme_import: user_id=%s has no PII; skipping match", user_id)
            return NO_MATCH

        nj_task = self._run_nj(pii)
        pa_task = self._run_pa(pii)
        nj_result, pa_result = await asyncio.gather(nj_task, pa_task)
        return merge_results(nj_result, pa_result)

    async def handle_registration_event(self, user_id: str) -> None:
        """Kafka-driven entry point: match, then lock and alert on hit."""
        result = await self.match_registration(user_id)
        if not result.matched:
            return
        LOG.info(
            "acme_import: match user_id=%s source=%s type=%s score=%.2f",
            user_id, result.source, result.match_type, result.score,
        )
        await self.lock_task.create_lock(user_id, result)
        if self.alerter is not None and self.config.alerting.enabled:
            await self.alerter.notify(user_id, result)

    # ------------------------------------------------------------------

    async def _run_nj(self, pii: PlayerPII) -> MatchResult:
        candidates = await self.nj_dge_db.find_candidates(pii)
        return match_nj_dge(pii, candidates, self.config.matching)

    async def _run_pa(self, pii: PlayerPII) -> MatchResult:
        candidates = await self.pa_dap_db.find_candidates(pii)
        return match_pa_dap(pii, candidates, self.config.matching)


# ---------------------------------------------------------------------------
# Simple in-memory stubs for local testing
# ---------------------------------------------------------------------------


@dataclass
class InMemoryPiiLookup:
    """Test double for PiiLookup backed by a dict."""

    store: dict[str, PlayerPII] = field(default_factory=dict)

    async def get(self, user_id: str) -> PlayerPII | None:
        return self.store.get(user_id)


@dataclass
class InMemoryExclusionDatabase:
    """Test double for ExclusionDatabase backed by a list."""

    source_id_value: str
    records: list[ExclusionRecord] = field(default_factory=list)

    @property
    def source_id(self) -> str:
        return self.source_id_value

    async def find_candidates(self, pii: PlayerPII) -> list[ExclusionRecord]:
        return [r for r in self.records if r.source == self.source_id_value]


@dataclass
class InMemoryLockTaskCreator:
    """Test double for LockTaskCreator that records calls."""

    calls: list[tuple[str, MatchResult]] = field(default_factory=list)

    async def create_lock(self, user_id: str, result: MatchResult) -> None:
        self.calls.append((user_id, result))


@dataclass
class InMemoryAlerter:
    """Test double for Alerter that records calls."""

    calls: list[tuple[str, MatchResult]] = field(default_factory=list)

    async def notify(self, user_id: str, result: MatchResult) -> None:
        self.calls.append((user_id, result))
