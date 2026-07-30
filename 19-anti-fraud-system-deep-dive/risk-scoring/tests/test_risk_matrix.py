# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
test_risk_matrix.py – 15+ tests for the risk-matrix scoring engine.

Covers:
  - evaluate_condition (safe condition parser)
  - MatrixScorer.apply_scores with various event types
  - PlayerProfiler build / update / level resolution
  - matrix_engine score_event dispatch
  - Jurisdiction filtering
  - Metric caching within a single scoring cycle
  - Resettable scores
"""

from __future__ import annotations

import importlib.util
import sys
import os
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, List, Optional, cast
from unittest.mock import MagicMock, call

import pytest

# Skip the whole module if confluent_kafka is not installed. alert_streams.py
# imports it at module level and this test's _load_local_module executes that
# import eagerly, so the collection fails hard without the dep. The kafka
# integration is optional chapter tooling; core book tests must not require it.
pytest.importorskip("confluent_kafka")

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# The chapter-19 book has two microservices that both ship a `models.py`
# (`risk-scoring/models.py` and `risk-alerting/models.py`). With pytest's
# default import behaviour the module that wins `sys.modules["models"]`
# depends on conftest load order, which breaks cross-service test
# collection. We sidestep the race by loading each sibling module via
# `importlib.util.spec_from_file_location` BEFORE any `from models import`
# runs -- that way `scorer.py`, `matrix_engine.py` etc. pick up the
# risk-scoring copy of `models` regardless of what any other test
# directory has cached.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    """Load a sibling module under an explicit sys.modules entry.

    Pops any previously cached version so the local service directory
    always wins, then installs the new module and executes it.
    """
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Order matters: `models` first, then modules that do
# `from models import ...` at top level.
_load_local_module("models", "models.py")
_load_local_module("alert_streams", "alert_streams.py")
_load_local_module("player_profiler", "player_profiler.py")
_load_local_module("scorer", "scorer.py")
_load_local_module("matrix_engine", "matrix_engine.py")

from scorer import (
    MatrixScorer,
    MetricProvider,
    ScoreRepository,
    UnresolvedMetricError,
    evaluate_condition,
)
from models import (
    EventType,
    MatrixLevel,
    MatrixScoreType,
    PlayerRiskProfile,
    UserMatrixScore,
)
from player_profiler import (
    PlayerProfiler,
    ProfileStore,
    classify_overall_risk,
    compute_level_number,
    resolve_level,
)
from matrix_engine import (
    FlagsService,
    MatrixScoreRepository,
    UserEventsService,
    _DepositConfirmedProvider,
    _DepositDeclinedProvider,
    _TimeoutAppliedProvider,
    score_event,
)

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_type(
    st_id: int = 1,
    matrix_id: str = "rg",
    label: str = "Test",
    event: str = "deposit-confirmed",
    condition: str = "depositCount >= 5",
    period_sec: int = 86400,
    jurisdiction: Optional[str] = None,
    alert_type: Optional[str] = None,
    interaction_type: Optional[str] = None,
    propagate_globally: bool = False,
) -> MatrixScoreType:
    return MatrixScoreType(
        score_matrix_id=matrix_id,
        id=st_id,
        label=label,
        calculate_on=event,
        metric_period_seconds=period_sec,
        condition=condition,
        score_value=5,
        jurisdiction=jurisdiction,
        triggered_alert_type=alert_type,
        triggered_interaction_type=interaction_type,
        propagate_globally=propagate_globally,
    )


class _FixedMetricProvider(MetricProvider):
    def __init__(self, values: dict) -> None:
        self._values = values

    def provide_metric(self, user_id, global_id, metric, from_ts, to_ts, is_global=False) -> Any:
        return self._values.get(metric, 0)


class _TrackingScoreRepository(ScoreRepository):
    def __init__(self):
        self.scores: List[UserMatrixScore] = []

    def add_user_score(self, score: UserMatrixScore) -> None:
        self.scores.append(score)


class _StubUE(UserEventsService):
    def __init__(self, **kw): self._kw = kw
    def _v(self, k): return self._kw.get(k, 0)
    def count_deposits(self, u, f, t): return self._v("count_deposits")
    def deposit_total(self, u, f, t): return self._v("deposit_total")
    def count_declined_deposits(self, u, f, t): return self._v("declined")
    def count_failed_deposits(self, u, f, t): return self._v("failed")
    def count_deposit_limit_changes(self, u, f, t, g): return self._v("limit_changes")
    def count_deposit_limit_increases(self, u, f, t): return self._v("limit_increases")
    def payment_options_created(self, u, f, t): return self._v("options_created")
    def payments_options_created_after_failed_deposit(self, u, f, t): return self._v("options_after_fail")
    def depositing_days(self, u, f, t): return self._v("depositing_days")
    def count_reversed_withdrawals(self, u, f, t): return self._v("reversed_wds")
    def reversed_withdrawals_total(self, u, f, t): return self._v("reversed_total")
    def count_interactions_by_types(self, u, g, types, f, t): return self._v("interactions")
    def count_timeouts(self, u, g, f, t): return self._v("timeouts")


class _StubFlags(FlagsService):
    def __init__(self, flags=None): self._flags = flags or set()
    def user_has_flag(self, user_id, flag_id): return flag_id in self._flags


class _StubMatrixRepo(MatrixScoreRepository, _TrackingScoreRepository):
    def get_scores_by_type_for_user(self, user_id, score_type): return 0
    def get_current_user_level(self, user_id, matrix_id): return None
    def add_user_score(self, score): return _TrackingScoreRepository.add_user_score(self, score)


# ---------------------------------------------------------------------------
# evaluate_condition tests
# ---------------------------------------------------------------------------


def test_condition_simple_greater_than():
    assert evaluate_condition("depositCount > 4", {"depositCount": 5}) is True


def test_condition_simple_less_than():
    assert evaluate_condition("depositCount < 4", {"depositCount": 3}) is True


def test_condition_equals():
    assert evaluate_condition("depositsDeclined = 3", {"depositsDeclined": 3}) is True


def test_condition_and_operator():
    assert evaluate_condition(
        "depositCount > 2 and depositTotal >= 1000",
        {"depositCount": 5, "depositTotal": 2000}
    ) is True


def test_condition_or_operator():
    assert evaluate_condition(
        "depositCount > 100 or depositsDeclined > 3",
        {"depositCount": 1, "depositsDeclined": 5}
    ) is True


def test_condition_none_string_evaluates_false():
    assert evaluate_condition("None", {}) is False


def test_condition_empty_string_evaluates_false():
    assert evaluate_condition("", {}) is False


def test_condition_missing_metric_defaults_to_zero():
    # depositCount not in dict → defaults to 0, so 0 > 5 is False
    assert evaluate_condition("depositCount > 5", {}) is False


def test_condition_boolean_flag_true():
    assert evaluate_condition("has_flag_7", {"has_flag_7": True}) is True


# ---------------------------------------------------------------------------
# MatrixScorer.apply_scores tests
# ---------------------------------------------------------------------------


def test_scorer_matches_single_rule():
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCount >= 5")],
        score_repo=repo,
    )
    provider = _FixedMetricProvider({"depositCount": 7})
    matched = scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    assert matched == ["rg"]
    assert len(repo.scores) == 1


def test_scorer_no_match_below_threshold():
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCount >= 10")],
        score_repo=repo,
    )
    provider = _FixedMetricProvider({"depositCount": 3})
    matched = scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    assert matched == []


def test_scorer_jurisdiction_filtering():
    """GB-only rule should not fire for a BR player."""
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCount >= 1", jurisdiction="GB")],
        score_repo=repo,
    )
    provider = _FixedMetricProvider({"depositCount": 5})
    matched = scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="BR")
    assert matched == []


def test_scorer_jurisdiction_specific_takes_precedence():
    """When both a generic and a jurisdiction-specific rule exist for same id, specific wins."""
    generic = _score_type(st_id=1, condition="depositCount >= 1")
    specific = _score_type(st_id=1, condition="depositCount >= 3", jurisdiction="GB")
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(score_types=[generic, specific], score_repo=repo)
    provider = _FixedMetricProvider({"depositCount": 2})
    # 2 >= 3 is False → no match for GB
    matched = scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    assert matched == []


def test_scorer_alert_sent_when_triggered():
    sender = MagicMock()
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCount >= 1", alert_type="rg_level1")],
        score_repo=repo,
        message_sender=sender,
    )
    provider = _FixedMetricProvider({"depositCount": 5})
    scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    sender.send_alert.assert_called_once()


def test_scorer_interaction_sent_when_triggered():
    sender = MagicMock()
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCount >= 1", interaction_type="rg3.1")],
        score_repo=repo,
        message_sender=sender,
    )
    provider = _FixedMetricProvider({"depositCount": 1})
    scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    sender.send_interaction.assert_called_once()


# ---------------------------------------------------------------------------
# PlayerProfiler tests
# ---------------------------------------------------------------------------


RG_LEVELS = [
    MatrixLevel(score_matrix_id="rg", level_number=1, label="Yellow", colour="#FFD700", min_score=8, max_score=19),
    MatrixLevel(score_matrix_id="rg", level_number=2, label="Orange", colour="#FFA500", min_score=20, max_score=39),
    MatrixLevel(score_matrix_id="rg", level_number=3, label="Red", colour="#FF0000", min_score=40, max_score=999),
]


def test_resolve_level_returns_correct_level():
    level = resolve_level(25, RG_LEVELS)
    assert level is not None
    assert level.level_number == 2


def test_resolve_level_below_all_returns_none():
    assert resolve_level(3, RG_LEVELS) is None


def test_compute_level_number_below_threshold():
    assert compute_level_number(5, RG_LEVELS) == 0


def test_profiler_updates_scores():
    store = ProfileStore()
    profiler = PlayerProfiler(store, {"rg": RG_LEVELS})
    score = UserMatrixScore(
        user_id=50,
        score_type_id=1,
        score_matrix_id="rg",
        comments="depositCount=6",
    )
    profile = profiler.update_profile(50, "GB", [score])
    assert "rg" in profile.scores


def test_profiler_add_and_remove_risk_flag():
    store = ProfileStore()
    profiler = PlayerProfiler(store, {})
    store.set(PlayerRiskProfile(user_id=55, jurisdiction="GB"))
    profiler.add_risk_flag(55, "aml_watch")
    profile = store.get(55)
    assert "aml_watch" in profile.risk_flags
    profiler.remove_risk_flag(55, "aml_watch")
    assert "aml_watch" not in store.get(55).risk_flags


def test_classify_overall_risk_severe():
    profile = PlayerRiskProfile(user_id=60, jurisdiction="MT", scores={"rg": 45})
    assert classify_overall_risk(profile) == "SEVERE"


def test_classify_overall_risk_low():
    profile = PlayerRiskProfile(user_id=61, jurisdiction="MT", scores={"rg": 3})
    assert classify_overall_risk(profile) == "LOW"


# ---------------------------------------------------------------------------
# matrix_engine.score_event integration
# ---------------------------------------------------------------------------


def test_score_event_deposit_confirmed_triggers():
    st = _score_type(condition="depositCount >= 3")
    repo = _StubMatrixRepo()
    ue = _StubUE(count_deposits=5)
    flags = _StubFlags()
    matched = score_event(
        user_id=100,
        global_id=100,
        event_type=EventType.DEPOSIT_CONFIRMED.value,
        jurisdiction="GB",
        score_types=[st],
        score_repo=repo,
        user_events=ue,
        flags=flags,
    )
    assert "rg" in matched


def test_score_event_deposit_declined_triggers():
    st = _score_type(event="deposit-declined", condition="depositsDeclined > 5")
    repo = _StubMatrixRepo()
    matched = score_event(
        user_id=101,
        global_id=101,
        event_type=EventType.DEPOSIT_DECLINED.value,
        jurisdiction="GB",
        score_types=[st],
        score_repo=repo,
        user_events=_StubUE(declined=6),
        flags=_StubFlags(),
    )
    assert "rg" in matched


# ---------------------------------------------------------------------------
# Unresolved-metric fail-closed behaviour
# ---------------------------------------------------------------------------


def test_resolve_value_default_dict_still_zero_defaults():
    # Direct callers passing a plain dict keep the historical, non-strict
    # zero-default behaviour (unchanged from before the hardening pass).
    assert evaluate_condition("depositCount > 5", {}) is False


def test_evaluate_condition_strict_raises_on_missing_metric():
    with pytest.raises(UnresolvedMetricError):
        evaluate_condition("depositCount > 5", {}, strict=True)


def test_evaluate_condition_strict_passes_through_when_metric_present():
    assert evaluate_condition("depositCount > 5", {"depositCount": 7}, strict=True) is True


class _RaisingProvider(MetricProvider):
    """Simulates a MetricProvider that doesn't recognise a metric name --
    mirrors matrix_engine.py's provider fallback after the hardening fix."""

    def __init__(self, known: dict):
        self._known = known

    def provide_metric(self, user_id, global_id, metric, from_ts, to_ts, is_global=False):
        if metric not in self._known:
            raise UnresolvedMetricError(metric)
        return self._known[metric]


def test_scorer_fails_closed_when_condition_metric_unresolved():
    """A rule condition referencing a metric the provider can't resolve
    (e.g. a typo) must fire (fail closed / max-risk), not silently no-op."""
    repo = _TrackingScoreRepository()
    scorer = MatrixScorer(
        score_types=[_score_type(condition="depositCoutn > 5")],  # typo, unresolvable
        score_repo=repo,
    )
    provider = _RaisingProvider({"depositCount": 7})
    matched = scorer.apply_scores(1, 1, "deposit-confirmed", provider, jurisdiction="GB")
    assert matched == ["rg"]
    assert len(repo.scores) == 1


def test_score_event_unknown_metric_name_fails_closed_via_real_provider():
    """Integration test through the real matrix_engine dispatch table: a
    condition with a metric name no _DepositConfirmedProvider branch
    recognises must still match rather than silently never fire."""
    st = _score_type(condition="totallyUnknownMetric > 0")
    repo = _StubMatrixRepo()
    matched = score_event(
        user_id=200,
        global_id=200,
        event_type=EventType.DEPOSIT_CONFIRMED.value,
        jurisdiction="GB",
        score_types=[st],
        score_repo=repo,
        user_events=_StubUE(),
        flags=_StubFlags(),
    )
    assert "rg" in matched
