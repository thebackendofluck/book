# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for the chapter-26 Acme Import registration-time matcher.

Run with:
    python -m pytest writing/new-book/scripts/chapter-26/acme-import/

Or directly:
    python -m unittest writing.new-book.scripts.chapter-26.acme-import.test_acme_import
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    AlertingConfig,
    KafkaConfig,
    MatchingConfig,
    MatchingMode,
    AcmeImportConfig,
    SftpSourceConfig,
)
from registration_consumer_service import (  # noqa: E402
    ExclusionRecord,
    InMemoryAlerter,
    InMemoryExclusionDatabase,
    InMemoryLockTaskCreator,
    InMemoryPiiLookup,
    MatchResult,
    NO_MATCH,
    PlayerPII,
    RegistrationConsumerService,
    levenshtein,
    match_nj_dge,
    match_pa_dap,
    merge_results,
    normalize_ssn,
)


def _config(levenshtein_max: int = 2) -> AcmeImportConfig:
    return AcmeImportConfig(
        mode=MatchingMode.ASYNC_KAFKA,
        sources={
            "nj-dge": SftpSourceConfig(
                source_id="nj-dge", host="nj.example", port=22,
                username="u", password=None, private_key_path="/tmp/key",
                remote_path="/", poll_interval_seconds=3600,
            ),
            "pa-dap": SftpSourceConfig(
                source_id="pa-dap", host="pa.example", port=22,
                username="u", password="pw", private_key_path=None,
                remote_path="/", poll_interval_seconds=3600,
            ),
        },
        kafka=KafkaConfig(
            bootstrap_servers="kafka:9092", topic="reg",
            group_id="acme_import", min_backoff_seconds=1.0,
            max_backoff_seconds=30.0, max_restarts=3,
        ),
        alerting=AlertingConfig(
            webhook_url="http://alerts.example", api_key="k", enabled=True,
        ),
        matching=MatchingConfig(pa_dap_levenshtein_max_distance=levenshtein_max),
        pii_lookup_url="http://pii.internal/lookup",
    )


# ---------------------------------------------------------------------------
# Pure matcher tests
# ---------------------------------------------------------------------------


class NormalizeSsnTests(unittest.TestCase):
    def test_strips_dashes_and_spaces(self) -> None:
        self.assertEqual(normalize_ssn("123-45-6789"), "123456789")
        self.assertEqual(normalize_ssn("  123 45 6789 "), "123456789")

    def test_none_passes_through(self) -> None:
        self.assertIsNone(normalize_ssn(None))
        self.assertIsNone(normalize_ssn(""))
        self.assertIsNone(normalize_ssn("   "))


class LevenshteinTests(unittest.TestCase):
    def test_identical_strings(self) -> None:
        self.assertEqual(levenshtein("smith|john", "smith|john"), 0)

    def test_single_substitution(self) -> None:
        self.assertEqual(levenshtein("smith|john", "smith|jobn"), 1)

    def test_transposition_counted_as_two(self) -> None:
        self.assertEqual(levenshtein("ab", "ba"), 2)

    def test_empty_strings(self) -> None:
        self.assertEqual(levenshtein("", ""), 0)
        self.assertEqual(levenshtein("abc", ""), 3)
        self.assertEqual(levenshtein("", "abc"), 3)


class NjDgeMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = _config().matching
        self.pii = PlayerPII(
            user_id="u1",
            first_name="Jane",
            last_name="Smith",
            date_of_birth=date(1985, 4, 15),
            ssn="123-45-6789",
        )

    def test_full_ssn_match(self) -> None:
        records = [
            ExclusionRecord(
                source="nj-dge",
                first_name="Jane",
                last_name="Smith",
                date_of_birth=date(1985, 4, 15),
                ssn="123456789",
            )
        ]
        result = match_nj_dge(self.pii, records, self.cfg)
        self.assertTrue(result.matched)
        self.assertEqual(result.match_type, "ssn-full")
        self.assertEqual(result.score, 1.0)

    def test_partial_ssn_plus_dob_match(self) -> None:
        records = [
            ExclusionRecord(
                source="nj-dge",
                first_name="J",
                last_name="S",
                date_of_birth=date(1985, 4, 15),
                ssn="999-99-6789",  # shares last 4
            )
        ]
        result = match_nj_dge(self.pii, records, self.cfg)
        self.assertTrue(result.matched)
        self.assertEqual(result.match_type, "ssn-partial-dob")

    def test_dob_lastname_fallback(self) -> None:
        pii_no_ssn = PlayerPII(
            user_id="u2",
            first_name="Jane",
            last_name="Smith",
            date_of_birth=date(1985, 4, 15),
            ssn=None,
        )
        records = [
            ExclusionRecord(
                source="nj-dge",
                first_name="Jane",
                last_name="SMITH",
                date_of_birth=date(1985, 4, 15),
                ssn=None,
            )
        ]
        result = match_nj_dge(pii_no_ssn, records, self.cfg)
        self.assertTrue(result.matched)
        self.assertEqual(result.match_type, "dob-lastname")

    def test_no_match_when_no_signal(self) -> None:
        records = [
            ExclusionRecord(
                source="nj-dge",
                first_name="John",
                last_name="Doe",
                date_of_birth=date(1970, 1, 1),
                ssn="000000000",
            )
        ]
        result = match_nj_dge(self.pii, records, self.cfg)
        self.assertFalse(result.matched)

    def test_ignores_pa_dap_records(self) -> None:
        records = [
            ExclusionRecord(
                source="pa-dap",
                first_name="Jane",
                last_name="Smith",
                date_of_birth=date(1985, 4, 15),
                ssn="123456789",
            )
        ]
        self.assertFalse(match_nj_dge(self.pii, records, self.cfg).matched)


class PaDapMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = _config(levenshtein_max=2).matching
        self.pii = PlayerPII(
            user_id="u3",
            first_name="Maria",
            last_name="Gonzalez",
            date_of_birth=date(1990, 12, 1),
        )

    def test_exact_match(self) -> None:
        records = [
            ExclusionRecord(
                source="pa-dap",
                first_name="Maria",
                last_name="Gonzalez",
                date_of_birth=date(1990, 12, 1),
            )
        ]
        result = match_pa_dap(self.pii, records, self.cfg)
        self.assertTrue(result.matched)
        self.assertEqual(result.score, 1.0)

    def test_single_typo_matches_within_threshold(self) -> None:
        records = [
            ExclusionRecord(
                source="pa-dap",
                first_name="Maria",
                last_name="Gonzales",  # last letter diff
                date_of_birth=date(1990, 12, 1),
            )
        ]
        result = match_pa_dap(self.pii, records, self.cfg)
        self.assertTrue(result.matched)
        self.assertEqual(result.match_type, "pa-fuzzy")

    def test_distance_beyond_threshold_fails(self) -> None:
        records = [
            ExclusionRecord(
                source="pa-dap",
                first_name="Pablo",
                last_name="Rodriguez",
                date_of_birth=date(1990, 12, 1),
            )
        ]
        result = match_pa_dap(self.pii, records, self.cfg)
        self.assertFalse(result.matched)

    def test_dob_mismatch_blocks_even_exact_name(self) -> None:
        records = [
            ExclusionRecord(
                source="pa-dap",
                first_name="Maria",
                last_name="Gonzalez",
                date_of_birth=date(1991, 12, 1),  # off by a year
            )
        ]
        result = match_pa_dap(self.pii, records, self.cfg)
        self.assertFalse(result.matched)


class MergeResultsTests(unittest.TestCase):
    def test_neither_matches_returns_no_match(self) -> None:
        self.assertEqual(merge_results(NO_MATCH, NO_MATCH), NO_MATCH)

    def test_nj_wins_on_tie(self) -> None:
        nj = MatchResult(True, "nj-dge", "ssn-full", 1.0)
        pa = MatchResult(True, "pa-dap", "pa-fuzzy", 1.0)
        self.assertEqual(merge_results(nj, pa).source, "nj-dge")

    def test_higher_score_wins(self) -> None:
        nj = MatchResult(True, "nj-dge", "dob-lastname", 0.7)
        pa = MatchResult(True, "pa-dap", "pa-fuzzy", 0.95)
        self.assertEqual(merge_results(nj, pa).source, "pa-dap")


# ---------------------------------------------------------------------------
# Async end-to-end tests
# ---------------------------------------------------------------------------


class RegistrationConsumerServiceTests(unittest.TestCase):
    def _build(self) -> tuple[RegistrationConsumerService, InMemoryLockTaskCreator, InMemoryAlerter]:
        cfg = _config()
        pii_lookup = InMemoryPiiLookup(store={
            "player-1": PlayerPII(
                user_id="player-1",
                first_name="Jane",
                last_name="Smith",
                date_of_birth=date(1985, 4, 15),
                ssn="123456789",
            ),
        })
        nj_db = InMemoryExclusionDatabase(
            source_id_value="nj-dge",
            records=[
                ExclusionRecord(
                    source="nj-dge",
                    first_name="Jane",
                    last_name="Smith",
                    date_of_birth=date(1985, 4, 15),
                    ssn="123456789",
                )
            ],
        )
        pa_db = InMemoryExclusionDatabase(source_id_value="pa-dap", records=[])
        lock_task = InMemoryLockTaskCreator()
        alerter = InMemoryAlerter()
        svc = RegistrationConsumerService(
            config=cfg,
            pii_lookup=pii_lookup,
            nj_dge_db=nj_db,
            pa_dap_db=pa_db,
            lock_task=lock_task,
            alerter=alerter,
        )
        return svc, lock_task, alerter

    def test_match_registration_returns_hit(self) -> None:
        svc, _, _ = self._build()
        result = asyncio.run(svc.match_registration("player-1"))
        self.assertTrue(result.matched)
        self.assertEqual(result.source, "nj-dge")

    def test_handle_event_creates_lock_and_alert(self) -> None:
        svc, lock_task, alerter = self._build()
        asyncio.run(svc.handle_registration_event("player-1"))
        self.assertEqual(len(lock_task.calls), 1)
        self.assertEqual(len(alerter.calls), 1)

    def test_no_match_leaves_lock_and_alert_empty(self) -> None:
        svc, lock_task, alerter = self._build()
        # Override PII so no match is found
        svc.pii_lookup = InMemoryPiiLookup(store={
            "player-2": PlayerPII(
                user_id="player-2",
                first_name="Bob",
                last_name="Johnson",
                date_of_birth=date(1960, 1, 1),
                ssn="000000000",
            ),
        })
        asyncio.run(svc.handle_registration_event("player-2"))
        self.assertEqual(lock_task.calls, [])
        self.assertEqual(alerter.calls, [])

    def test_unknown_user_id_is_no_match(self) -> None:
        svc, lock_task, _ = self._build()
        result = asyncio.run(svc.match_registration("does-not-exist"))
        self.assertEqual(result, NO_MATCH)
        asyncio.run(svc.handle_registration_event("does-not-exist"))
        self.assertEqual(lock_task.calls, [])


if __name__ == "__main__":
    unittest.main()
