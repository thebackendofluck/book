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
platform.feature_flags — Feature Flag System
=============================================

Feature flags (also called feature toggles) allow operators to enable or
disable platform functionality at runtime without a code deploy.

Common use cases in iGaming platforms:

* **Gradual rollouts** — enable a new responsible-gaming feature for 10%
  of players before a full launch.
* **Brand/jurisdiction scoping** — enable a feature only for UKGC-licensed
  players on the ``acme_uk`` brand.
* **Kill switches** — instantly disable a broken supplier integration
  without a rollback deployment.
* **A/B testing** — serve different lobby layouts to different player
  segments and measure conversion.

Architecture
------------
A :class:`FeatureFlag` is a named boolean condition that can be evaluated
against a :class:`FlagContext`.  The :class:`FeatureFlagRegistry` holds all
registered flags and evaluates them in priority order:

1. **Explicit override** — an exact match on ``(brand_id, jurisdiction, player_id)``.
2. **Brand + jurisdiction** — enabled for the brand and jurisdiction.
3. **Brand-only** — enabled for all players on the brand.
4. **Jurisdiction-only** — enabled for the jurisdiction across all brands.
5. **Global** — enabled for all players everywhere.

If no rule matches, the flag returns its ``default_enabled`` value.

Design decisions
----------------
* All state lives in-memory.  For production, persist flags in a database
  or a config store (e.g. LaunchDarkly, Unleash) and use an adapter.
* Flag evaluation is always synchronous and O(1) to avoid latency on every
  game API call.
* Flag definitions are immutable :class:`FeatureFlag` objects; rules are
  mutable and can be added/removed at runtime.

Example::

    registry = FeatureFlagRegistry()
    registry.register(FeatureFlag("new_bonus_ui", default_enabled=False))
    registry.enable_for_brand("new_bonus_ui", brand_id="acme_uk")

    ctx = FlagContext(brand_id="acme_uk", jurisdiction="UKGC")
    assert registry.is_enabled("new_bonus_ui", ctx) is True
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlagContext:
    """Evaluation context for a feature flag check.

    Attributes
    ----------
    brand_id:
        The white-label brand being evaluated.
    jurisdiction:
        The regulatory jurisdiction of the player's session.
    player_id:
        Optional; used for player-specific overrides or percentage rollouts.
    extra:
        Additional arbitrary context (e.g. game category, channel).
    """

    brand_id: str = ""
    jurisdiction: str = ""
    player_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Flag definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureFlag:
    """An immutable feature flag definition.

    Attributes
    ----------
    name:
        Unique flag identifier (e.g. ``"new_bonus_ui"``, ``"reality_check_v2"``).
    description:
        Human-readable description of what this flag controls.
    default_enabled:
        Whether the feature is enabled when no applicable rule is found.
    """

    name: str
    description: str = ""
    default_enabled: bool = False


# ---------------------------------------------------------------------------
# Rule types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FlagRule:
    """Internal evaluation rule for a flag."""

    brand_id: str | None  # None = any brand
    jurisdiction: str | None  # None = any jurisdiction
    player_id: str | None  # None = any player
    enabled: bool  # The value to return when this rule matches


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class FeatureFlagRegistry:
    """Holds registered feature flags and evaluates them against context.

    All public methods are thread-safe.

    Parameters
    ----------
    flags:
        Optional initial list of :class:`FeatureFlag` objects to register.
    """

    def __init__(self, flags: list[FeatureFlag] | None = None) -> None:
        self._flags: dict[str, FeatureFlag] = {}
        self._rules: dict[str, list[_FlagRule]] = {}
        self._lock = threading.Lock()

        if flags:
            for flag in flags:
                self.register(flag)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, flag: FeatureFlag) -> None:
        """Register a feature flag.

        Parameters
        ----------
        flag:
            The flag definition to register.

        Raises
        ------
        ValueError
            If a flag with the same name is already registered.
        """
        with self._lock:
            if flag.name in self._flags:
                raise ValueError(
                    f"Feature flag {flag.name!r} is already registered. "
                    f"Use replace() to update it."
                )
            self._flags[flag.name] = flag
            self._rules[flag.name] = []

    def replace(self, flag: FeatureFlag) -> None:
        """Register or update a feature flag, replacing any existing one.

        Parameters
        ----------
        flag:
            The flag definition to register or update.
        """
        with self._lock:
            self._flags[flag.name] = flag
            if flag.name not in self._rules:
                self._rules[flag.name] = []

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def enable_globally(self, flag_name: str) -> None:
        """Enable *flag_name* for all players everywhere."""
        self._add_rule(flag_name, _FlagRule(None, None, None, enabled=True))

    def disable_globally(self, flag_name: str) -> None:
        """Disable *flag_name* for all players everywhere."""
        self._add_rule(flag_name, _FlagRule(None, None, None, enabled=False))

    def enable_for_brand(self, flag_name: str, brand_id: str) -> None:
        """Enable *flag_name* for all players on *brand_id*."""
        self._add_rule(flag_name, _FlagRule(brand_id, None, None, enabled=True))

    def disable_for_brand(self, flag_name: str, brand_id: str) -> None:
        """Disable *flag_name* for all players on *brand_id*."""
        self._add_rule(flag_name, _FlagRule(brand_id, None, None, enabled=False))

    def enable_for_jurisdiction(self, flag_name: str, jurisdiction: str) -> None:
        """Enable *flag_name* for a specific jurisdiction."""
        self._add_rule(flag_name, _FlagRule(None, jurisdiction, None, enabled=True))

    def disable_for_jurisdiction(self, flag_name: str, jurisdiction: str) -> None:
        """Disable *flag_name* for a specific jurisdiction."""
        self._add_rule(flag_name, _FlagRule(None, jurisdiction, None, enabled=False))

    def enable_for_player(self, flag_name: str, player_id: str) -> None:
        """Enable *flag_name* for a specific player (highest priority override)."""
        self._add_rule(flag_name, _FlagRule(None, None, player_id, enabled=True))

    def disable_for_player(self, flag_name: str, player_id: str) -> None:
        """Disable *flag_name* for a specific player."""
        self._add_rule(flag_name, _FlagRule(None, None, player_id, enabled=False))

    def enable_for_brand_and_jurisdiction(
        self,
        flag_name: str,
        brand_id: str,
        jurisdiction: str,
    ) -> None:
        """Enable *flag_name* for a brand+jurisdiction combination."""
        self._add_rule(
            flag_name, _FlagRule(brand_id, jurisdiction, None, enabled=True)
        )

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def is_enabled(self, flag_name: str, ctx: FlagContext) -> bool:
        """Evaluate *flag_name* against *ctx*.

        Evaluation priority (highest to lowest):
        1. Player-specific override (player_id matches)
        2. Brand + jurisdiction match
        3. Brand-only match
        4. Jurisdiction-only match
        5. Global rule (no brand, no jurisdiction, no player)
        6. Flag's ``default_enabled`` value

        Parameters
        ----------
        flag_name:
            The flag to evaluate.
        ctx:
            The evaluation context.

        Returns
        -------
        bool
            Whether the feature is enabled for this context.

        Raises
        ------
        KeyError
            If *flag_name* is not registered.
        """
        with self._lock:
            flag = self._flags.get(flag_name)
            if flag is None:
                raise KeyError(f"Feature flag {flag_name!r} is not registered.")
            rules = list(self._rules.get(flag_name, []))
            default = flag.default_enabled

        # Evaluate rules in priority buckets.
        # Each bucket is tested in turn; the first matching rule wins.
        priority_tests: list[tuple[_FlagRule, bool]] = []
        for rule in reversed(rules):  # latest-added rule wins within a bucket
            match_brand = rule.brand_id is None or rule.brand_id == ctx.brand_id
            match_jurisdiction = rule.jurisdiction is None or rule.jurisdiction == ctx.jurisdiction
            match_player = rule.player_id is None or rule.player_id == ctx.player_id

            # Specificity score: player > brand+jurisdiction > brand > jurisdiction > global
            if match_player and rule.player_id is not None:
                return rule.enabled
            if match_brand and match_jurisdiction and rule.brand_id and rule.jurisdiction:
                priority_tests.append((rule, True))  # brand+jurisdiction
            elif match_brand and rule.brand_id and not rule.jurisdiction:
                priority_tests.append((rule, False))  # brand only
            elif match_jurisdiction and rule.jurisdiction and not rule.brand_id:
                priority_tests.append((rule, False))  # jurisdiction only
            elif not rule.brand_id and not rule.jurisdiction and not rule.player_id:
                priority_tests.append((rule, False))  # global

        # Return first result from most specific bucket.
        for rule, _ in priority_tests:
            return rule.enabled

        return default

    def registered_flags(self) -> list[str]:
        """Return the names of all registered flags."""
        with self._lock:
            return list(self._flags.keys())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_rule(self, flag_name: str, rule: _FlagRule) -> None:
        with self._lock:
            if flag_name not in self._flags:
                raise KeyError(
                    f"Feature flag {flag_name!r} is not registered. "
                    f"Call register() first."
                )
            self._rules[flag_name].append(rule)


__all__ = ["FeatureFlag", "FeatureFlagRegistry", "FlagContext"]
