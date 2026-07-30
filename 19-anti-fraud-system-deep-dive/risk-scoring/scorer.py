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
scorer.py — Core risk scoring engine.

Mirrors MatrixScorer.scala and MatrixScoreTransactionalService.scala.

For each incoming event, the scorer:
  1. Finds applicable score types filtered by jurisdiction and event type
  2. Resolves metrics (with caching) via a pluggable MetricProvider
  3. Evaluates rule conditions expressed as simple comparison expressions
  4. On match: persists the score, optionally propagates globally,
     sends alerts, and creates RG interactions
  5. Returns the list of matrix IDs that received new scores

Condition evaluation:
  The original Scala used Groovy for flexible rule expressions. This Python
  port uses a safe expression evaluator that supports numeric comparisons
  and boolean operators without executing arbitrary code.
"""
from __future__ import annotations

import logging
import operator
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from models import (
    AlertMessageParams,
    MatrixScoreType,
    UserMatrixScore,
)

log = structlog.get_logger()


class UnresolvedMetricError(Exception):
    """Raised when a scoring condition references a metric that could not
    be resolved — either a truly missing value (strict mode) or a metric
    name no MetricProvider recognises (propagated from matrix_engine.py's
    provider dispatch tables)."""

    def __init__(self, metric: str) -> None:
        self.metric = metric
        super().__init__(f"unresolved metric: {metric}")


# ---------------------------------------------------------------------------
# MetricProvider protocol
# ---------------------------------------------------------------------------

class MetricProvider(ABC):
    """
    Pluggable metric resolver — one implementation per event type.
    Mirrors the MatrixMetricProvider[T] trait in Scala.
    """

    @abstractmethod
    def provide_metric(
        self,
        user_id: int,
        global_id: int,
        metric: str,
        from_ts: datetime,
        to_ts: datetime,
        is_global: bool = False,
    ) -> Any:
        ...


# ---------------------------------------------------------------------------
# Safe condition evaluator (replaces Groovy/eval with explicit parsing)
# ---------------------------------------------------------------------------

_OPS: dict[str, Any] = {
    ">=": operator.ge,
    "<=": operator.le,
    "!=": operator.ne,
    ">":  operator.gt,
    "<":  operator.lt,
    "==": operator.eq,
    "=":  operator.eq,
}

_TOKEN_RE = re.compile(
    r'(?P<float>-?\d+\.\d+)'
    r'|(?P<int>-?\d+)'
    r'|(?P<op>>=|<=|!=|>|<|==|=)'
    r'|(?P<and>\band\b|\bAND\b)'
    r'|(?P<or>\bor\b|\bOR\b)'
    r'|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)'
    r'|(?P<bool>true|false|True|False)',
    re.IGNORECASE,
)


def _resolve_value(token: str, metrics: dict[str, Any], *, strict: bool = False) -> Any:
    """Resolve a token to a concrete value.

    In `strict` mode (used by the live scoring pipeline via
    `MatrixScorer._apply_metric`), a metric that cannot be resolved raises
    `UnresolvedMetricError` instead of silently defaulting to 0 -- a rule
    that can't be evaluated must not silently fail to fire. Direct callers
    that pass a plain, pre-resolved dict (e.g. tests exercising
    `evaluate_condition` standalone) keep the historical zero-default
    behaviour unless they opt into strict mode.
    """
    if token in ("true", "True"):
        return True
    if token in ("false", "False"):
        return False
    try:
        return float(token) if "." in token else int(token)
    except ValueError:
        try:
            return metrics[token]
        except (KeyError, UnresolvedMetricError):
            # A metric name absent from the resolved set silently becomes 0,
            # which can make a rule never fire -- surfaced here, and in
            # strict mode refused outright rather than passed through.
            log.warning("scorer.unknown_metric", token=token)
            if strict:
                raise UnresolvedMetricError(token) from None
            return 0


def evaluate_condition(condition: str, metrics: dict[str, Any], *, strict: bool = False) -> bool:
    """
    Safely evaluate a scoring rule condition against resolved metrics.

    Supports: comparisons (<, <=, >, >=, ==, =, !=), and, or.
    Example: "depositCount > 20 and depositTotal >= 5000"

    `strict=True` lets `UnresolvedMetricError` propagate instead of treating
    an unresolvable metric as 0 -- see `_resolve_value`.
    """
    if not condition or condition.strip() in ("None", ""):
        return False

    tokens = [m.group() for m in _TOKEN_RE.finditer(condition)]

    # Evaluate atomic comparisons, then combine with and/or
    parts: list[bool | str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.lower() in ("and", "or"):
            parts.append(tok.lower())
            i += 1
        elif i + 2 < len(tokens) and tokens[i + 1] in _OPS:
            lhs = _resolve_value(tok, metrics, strict=strict)
            op_fn = _OPS[tokens[i + 1]]
            rhs = _resolve_value(tokens[i + 2], metrics, strict=strict)
            try:
                result = op_fn(float(lhs), float(rhs))
            except (TypeError, ValueError):
                result = op_fn(lhs, rhs)
            parts.append(result)
            i += 3
        else:
            # single boolean variable
            v = _resolve_value(tok, metrics, strict=strict)
            parts.append(bool(v))
            i += 1

    # Combine with correct Python precedence: 'and' binds tighter than 'or'.
    # Split into or-groups on the 'or' tokens, AND within each group, then OR
    # across groups. A left-to-right fold would wrongly treat "A or B and C" as
    # "(A or B) and C" instead of "A or (B and C)".
    if not parts:
        return False

    or_groups: list[list[bool]] = [[]]
    for p in parts:
        if p == "or":
            or_groups.append([])
        elif p == "and":
            continue
        else:
            or_groups[-1].append(bool(p))

    return any(all(group) for group in or_groups if group)


# ---------------------------------------------------------------------------
# Matrix scorer
# ---------------------------------------------------------------------------

class MatrixScorer:
    """
    Core scoring engine. Mirrors MatrixScorer.applyScores() in Scala.
    """

    def __init__(
        self,
        score_types: list[MatrixScoreType],
        score_repo: "ScoreRepository",
        message_sender: Optional["MessageSender"] = None,
    ) -> None:
        self._score_types = score_types
        self._score_repo = score_repo
        self._message_sender = message_sender

    def apply_scores(
        self,
        user_id: int,
        global_id: int,
        event: str,
        metric_provider: MetricProvider,
        data: dict[str, Any] | None = None,
        custom_metrics: dict[str, Any] | None = None,
        jurisdiction: str = "",
        on_date: Optional[datetime] = None,
    ) -> list[str]:
        """
        Apply all matching scoring rules and return a list of matrix IDs
        that received new scores.
        """
        data = data or {}
        custom_metrics = custom_metrics or {}
        to_ts = on_date or datetime.now(timezone.utc)
        metrics_cache: dict[str, Any] = {}
        matched_matrices: list[str] = []

        applicable = self._find_applicable_scores(jurisdiction, event)
        log.debug("applicable score types", trigger_event=event, count=len(applicable))

        for mst in applicable:
            result = self._apply_metric(
                mst, user_id, global_id, to_ts,
                metric_provider, data, custom_metrics, metrics_cache
            )
            if result is not None:
                matched_matrices.append(result)

        return matched_matrices

    def _apply_metric(
        self,
        mst: MatrixScoreType,
        user_id: int,
        global_id: int,
        to_ts: datetime,
        metric_provider: MetricProvider,
        data: dict[str, Any],
        custom_metrics: dict[str, Any],
        metrics_cache: dict[str, Any],
    ) -> Optional[str]:
        from datetime import timedelta

        period_key = str(mst.metric_period_seconds)
        used_metrics: dict[str, Any] = {}

        def resolve(name: str) -> Any:
            if name in data:
                used_metrics[name] = data[name]
                return data[name]
            cache_key = f"{period_key}/{name}"
            if cache_key not in metrics_cache:
                from_ts = to_ts - timedelta(seconds=mst.metric_period_seconds)
                value = metric_provider.provide_metric(
                    user_id, global_id, name, from_ts, to_ts, mst.is_global
                )
                metrics_cache[cache_key] = value
                log.debug("resolved metric", name=name, value=value)
            used_metrics[name] = metrics_cache[cache_key]
            return metrics_cache[cache_key]

        # Build a metrics namespace that lazily resolves on access
        namespace = _LazyMetrics(resolve)

        try:
            matched = evaluate_condition(mst.condition, namespace, strict=True)
        except UnresolvedMetricError as exc:
            # A condition referencing a metric no provider recognises (typo,
            # or a metric not yet wired for this event type) must not
            # silently evaluate to "rule doesn't fire". Treat it as a match
            # (fail closed / max-risk) and log loudly so the bad rule gets
            # fixed instead of quietly never triggering.
            log.error(
                "condition.unresolved_metric_fail_closed",
                condition=mst.condition,
                metric=exc.metric,
                score_matrix_id=mst.score_matrix_id,
                score_type_id=mst.id,
            )
            matched = True
        log.debug("condition result", condition=mst.condition, matched=matched)

        if matched:
            metric_string = ", ".join(
                f"{k}={v}" for k, v in {**used_metrics, **custom_metrics}.items()
            )
            score = UserMatrixScore(
                user_id=user_id,
                score_type_id=mst.id,
                comments=metric_string,
                timestamp=to_ts,
            )
            self._score_repo.add_user_score(score)

            params = AlertMessageParams(
                score_type_id=mst.id,
                matrix=mst.score_matrix_id,
                comment=metric_string,
                label=mst.label,
            )
            if mst.propagate_globally and self._message_sender:
                self._message_sender.send_global_score(user_id, mst, metric_string, to_ts)
            if mst.triggered_alert_type and self._message_sender:
                self._message_sender.send_alert(user_id, mst.triggered_alert_type, params)
            if mst.triggered_interaction_type and self._message_sender:
                self._message_sender.send_interaction(user_id, mst.triggered_interaction_type, params)

            return mst.score_matrix_id
        return None

    def _find_applicable_scores(self, jurisdiction: str, event: str) -> list[MatrixScoreType]:
        """
        Find score types for this jurisdiction+event.
        Jurisdiction-specific rules take precedence over generic ones.
        Mirrors MatrixScorer.findApplicableScoresForJurisdiction().
        """
        filtered = [
            st for st in self._score_types
            if st.calculate_on == event
            and (st.jurisdiction is None or st.jurisdiction == jurisdiction)
        ]
        by_id: dict[int, MatrixScoreType] = {}
        for st in filtered:
            if st.id not in by_id or (st.jurisdiction and not by_id[st.id].jurisdiction):
                by_id[st.id] = st
        return list(by_id.values())


class _LazyMetrics(dict):
    """Dict whose __missing__ calls a resolver to fetch metric values on demand."""

    def __init__(self, resolver: Any) -> None:
        super().__init__()
        self._resolver = resolver

    def __missing__(self, key: str) -> Any:
        value = self._resolver(key)
        self[key] = value
        return value

    def get(self, key: str, default: Any = 0) -> Any:  # type: ignore[override]
        """Override get() so it triggers __missing__ and invokes the resolver."""
        try:
            return self[key]
        except KeyError:
            return default


# ---------------------------------------------------------------------------
# Repository and MessageSender stubs
# ---------------------------------------------------------------------------

class ScoreRepository:
    def add_user_score(self, score: UserMatrixScore) -> None:
        log.info("score added", user_id=score.user_id, score_type_id=score.score_type_id)


class MessageSender:
    def send_global_score(self, user_id: int, mst: MatrixScoreType, comments: str, ts: datetime) -> None:
        log.info("global score propagated", user_id=user_id, matrix=mst.score_matrix_id)

    def send_alert(self, user_id: int, alert_type: str, params: AlertMessageParams) -> None:
        log.info("alert sent", user_id=user_id, alert_type=alert_type)

    def send_interaction(self, user_id: int, interaction_type: str, params: AlertMessageParams) -> None:
        log.info("interaction sent", user_id=user_id, interaction_type=interaction_type)
