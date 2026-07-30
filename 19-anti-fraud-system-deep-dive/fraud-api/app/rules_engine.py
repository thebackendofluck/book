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
AcmeToCasino Fraud Detection API — Rules Engine

Implements the deterministic fraud-detection rules described in Chapter 19.
Rules provide the *explainable* layer that sits alongside the ML ensemble.
Each rule:
  - is independently auditable (required for MGA/UKGC licence conditions)
  - carries a jurisdiction scope (MGA thresholds differ from UKGC)
  - returns a structured RuleResult that feeds into the ensemble score
  - is registered in the RulesRegistry and exposed via GET /fraud/rules

Rule catalogue (all rule IDs reference the detection taxonomy in Chapter 19):

  RULE-VEL-001  Deposit velocity — too many deposits per hour
  RULE-VEL-002  Bet velocity — inhuman bet rate (bot signal)
  RULE-AMT-001  Amount anomaly — deposit far above player baseline
  RULE-GEO-001  Geo anomaly — login from a new country
  RULE-GEO-002  Impossible travel — two logins from different countries < 2 h apart
  RULE-DEV-001  Device sharing — multiple distinct player accounts per device
  RULE-STR-001  Structuring — repeated deposits just below AML reporting threshold
  RULE-BON-001  Bonus abuse — multiple accounts per device/IP claiming welcome bonus
  RULE-CRD-001  Card testing — rapid small deposits with distinct card BINs
  RULE-COL-001  Collusion signal — one-directional fund flow between player pair

Compliance references:
  - AMLD6 (EU 2018/1673) — structuring / layering typologies
  - FATF R.10 — transaction monitoring thresholds
  - FATF R.20 — suspicious transaction reporting triggers
  - PCI DSS Req. 10 — audit trail for every rule evaluation
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .models import DetectionRule, FraudTypology, Jurisdiction, RuleStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AML reporting thresholds per jurisdiction (EUR-equivalent minor units)
# AMLD6 Article 18 / FATF R.10 — operators must define jurisdiction-specific
# thresholds; these defaults match common regulatory guidance.
# ---------------------------------------------------------------------------
AML_THRESHOLD_EUR_CENTS: Dict[str, int] = {
    Jurisdiction.MGA: 200_000,    # EUR 2,000 (MGA Player Protection Directive)
    Jurisdiction.UKGC: 200_000,   # GBP 2,000 equivalent — UKGC LCCP SR Code 3.4.1
    Jurisdiction.SGA: 150_000,    # SEK ~16,000 equivalent
    Jurisdiction.DGE: 1_000_000,  # USD 10,000 — BSA CTR threshold
    Jurisdiction.IGCB: 1_000_000,
    Jurisdiction.ARJEL: 200_000,
    Jurisdiction.UNKNOWN: 1_000_000,
}

# Structuring detection window: flag deposits within N% below reporting threshold
STRUCTURING_BAND_PCT = 0.10   # flag amounts between (threshold * 0.90) and threshold


# ---------------------------------------------------------------------------
# Rule result
# ---------------------------------------------------------------------------

@dataclass
class RuleResult:
    """
    The output of a single rule evaluation.

    `score_contribution` is the delta added to the ensemble pre-score when
    this rule fires.  Multiple rules can fire on the same transaction; their
    contributions are summed (capped at 1.0 by the engine).
    """
    rule_id: str
    rule_name: str
    fired: bool
    score_contribution: float          # 0.0 if not fired
    typology: FraudTypology
    evidence: Dict[str, Any] = field(default_factory=dict)
    # Human-readable explanation — satisfies UKGC/MGA explainability obligation
    explanation: str = ""
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Base rule
# ---------------------------------------------------------------------------

class BaseRule:
    """
    Abstract base for all fraud rules.

    Sub-classes must implement `evaluate(context)` and return a `RuleResult`.
    The `jurisdiction_scope` list controls which jurisdictions the rule is
    active for — an empty list means all jurisdictions.
    """

    rule_id: str = ""
    name: str = ""
    description: str = ""
    typology: FraudTypology = FraudTypology.UNKNOWN
    status: RuleStatus = RuleStatus.ACTIVE
    jurisdiction_scope: List[Jurisdiction] = []
    base_score_contribution: float = 0.1
    created_by: str = "system"

    def is_applicable(self, jurisdiction: Jurisdiction) -> bool:
        """Return True if this rule should run for the given jurisdiction."""
        if self.status != RuleStatus.ACTIVE:
            return False
        if not self.jurisdiction_scope:
            return True
        return jurisdiction in self.jurisdiction_scope

    def evaluate(self, context: "RuleContext") -> RuleResult:
        raise NotImplementedError

    def _not_fired(self) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_name=self.name,
            fired=False,
            score_contribution=0.0,
            typology=self.typology,
        )

    def _fired(self, evidence: Dict[str, Any], explanation: str) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_name=self.name,
            fired=True,
            score_contribution=self.base_score_contribution,
            typology=self.typology,
            evidence=evidence,
            explanation=explanation,
        )

    def to_detection_rule(self) -> DetectionRule:
        """Serialise this rule into the API model for GET /fraud/rules."""
        return DetectionRule(
            rule_id=self.rule_id,
            name=self.name,
            description=self.description,
            typology=self.typology,
            status=self.status,
            jurisdiction_scope=self.jurisdiction_scope,
            parameters=self._parameters(),
            base_score_contribution=self.base_score_contribution,
            created_by=self.created_by,
        )

    def _parameters(self) -> Dict[str, Any]:
        """Override in sub-classes to expose threshold values via the API."""
        return {}


# ---------------------------------------------------------------------------
# Rule context — the data envelope passed to every rule
# ---------------------------------------------------------------------------

@dataclass
class RuleContext:
    """
    Snapshot of all information available at scoring time.

    Fields mirror the `AnalyzeTransactionRequest` model plus enriched
    historical aggregates fetched from Redis / Elasticsearch before the
    rules engine is invoked.

    `player_history` is a dict of pre-computed aggregates (populated by the
    Redis scoring cache layer before this context is constructed):
        deposit_count_1h      — deposits in the last 60 minutes
        deposit_count_24h     — deposits in the last 24 hours
        deposit_amount_24h    — total deposited (minor units) in last 24 h
        deposit_count_7d
        deposit_amount_7d
        bet_count_1m          — bets in the last 60 seconds (bot detection)
        last_login_country    — ISO 3166-1 alpha-2 of the previous login
        last_login_at         — datetime of the previous login
        known_device_fps      — set of known device fingerprints for player
        device_player_count   — number of distinct players on current device
        open_alerts           — count of unresolved fraud alerts
        deposit_amounts_24h   — list of individual deposit amounts (minor units)
        card_bins_1h          — list of card BINs used in last hour
        bonus_claimed         — bool: has player claimed welcome bonus?
    """
    correlation_id: str
    player_id: str
    brand_id: int
    jurisdiction: Jurisdiction

    transaction_type: str
    amount: float          # minor units
    currency: str
    payment_method: Optional[str] = None
    deposit_number: Optional[int] = None

    ip_address: Optional[str] = None
    country_code: Optional[str] = None
    device_fingerprint: Optional[str] = None
    user_agent: Optional[str] = None

    player_history: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.player_history.get(key, default)


# ---------------------------------------------------------------------------
# RULE-VEL-001  Deposit velocity
# ---------------------------------------------------------------------------

class DepositVelocityRule(BaseRule):
    """
    Flag when a player makes an unusually high number of deposits within a
    short window.  High deposit velocity combined with high-RTP game play
    is a classic money-laundering layering signal (FATF R.10).

    Thresholds are jurisdiction-aware:
      - MGA/UKGC: 5 deposits per hour trigger investigation
      - US jurisdictions: 10 deposits per hour (higher volume market)
    """

    rule_id = "RULE-VEL-001"
    name = "Deposit Velocity — High Frequency"
    description = (
        "Player made an unusually high number of deposits within 60 minutes. "
        "Signal for structuring, money laundering layering, or automated bot activity."
    )
    typology = FraudTypology.VELOCITY_ANOMALY
    base_score_contribution = 0.25

    # Thresholds: (jurisdiction) → max deposits per hour before rule fires
    _thresholds: Dict[str, int] = {
        Jurisdiction.MGA: 5,
        Jurisdiction.UKGC: 5,
        Jurisdiction.SGA: 4,
        Jurisdiction.DGE: 10,
        Jurisdiction.IGCB: 10,
        Jurisdiction.ARJEL: 5,
        Jurisdiction.UNKNOWN: 6,
    }

    def _parameters(self) -> Dict[str, Any]:
        return {"thresholds_by_jurisdiction": self._thresholds, "window_minutes": 60}

    def evaluate(self, context: RuleContext) -> RuleResult:
        if context.transaction_type != "deposit":
            return self._not_fired()

        threshold = self._thresholds.get(context.jurisdiction, 6)
        deposit_count_1h = int(context.get("deposit_count_1h", 0))

        if deposit_count_1h < threshold:
            return self._not_fired()

        return self._fired(
            evidence={
                "deposit_count_1h": deposit_count_1h,
                "threshold": threshold,
                "jurisdiction": context.jurisdiction,
            },
            explanation=(
                f"Player made {deposit_count_1h} deposits in the last 60 minutes "
                f"(threshold for {context.jurisdiction}: {threshold})."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-VEL-002  Bet velocity (bot signal)
# ---------------------------------------------------------------------------

class BetVelocityRule(BaseRule):
    """
    Detect inhuman bet rates — a primary bot-detection signal.

    Human players cannot consistently place more than 10–15 bets per minute
    across most casino game formats.  Rates above 30/min are virtually
    impossible without automation.

    Reference: Chapter 19 — Bot Detection, 'inhuman reaction times' signal.
    """

    rule_id = "RULE-VEL-002"
    name = "Bet Velocity — Inhuman Rate"
    description = (
        "Bet placement rate exceeds humanly possible thresholds. "
        "Strong indicator of automated bot activity (Chapter 19: Bot Detection)."
    )
    typology = FraudTypology.BOT_ACTIVITY
    base_score_contribution = 0.40

    BOT_THRESHOLD_BETS_PER_MINUTE = 30

    def _parameters(self) -> Dict[str, Any]:
        return {"max_bets_per_minute": self.BOT_THRESHOLD_BETS_PER_MINUTE}

    def evaluate(self, context: RuleContext) -> RuleResult:
        if context.transaction_type != "bet":
            return self._not_fired()

        bet_count_1m = int(context.get("bet_count_1m", 0))

        if bet_count_1m < self.BOT_THRESHOLD_BETS_PER_MINUTE:
            return self._not_fired()

        return self._fired(
            evidence={
                "bet_count_1m": bet_count_1m,
                "threshold": self.BOT_THRESHOLD_BETS_PER_MINUTE,
            },
            explanation=(
                f"Player placed {bet_count_1m} bets in the last 60 seconds "
                f"(bot threshold: {self.BOT_THRESHOLD_BETS_PER_MINUTE}/min)."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-AMT-001  Amount anomaly
# ---------------------------------------------------------------------------

class AmountAnomalyRule(BaseRule):
    """
    Flag a single deposit that is significantly larger than the player's
    historical average.

    A player who typically deposits EUR 20 suddenly depositing EUR 2,000
    is a strong signal for account takeover or first-use of a stolen card.
    The multiplier threshold is set conservatively to avoid false positives
    on legitimate high-roller sessions.

    Reference: Chapter 19 — 'unusual deposit amounts' signal.
    """

    rule_id = "RULE-AMT-001"
    name = "Amount Anomaly — Deposit Far Above Baseline"
    description = (
        "Current deposit amount is a large multiple of the player's 30-day average. "
        "Signal for account takeover, stolen card first use, or layering."
    )
    typology = FraudTypology.AMOUNT_ANOMALY
    base_score_contribution = 0.30

    MULTIPLIER_THRESHOLD = 10.0   # 10× the 30-day average triggers the rule
    MIN_HISTORY_DEPOSITS = 5      # require at least 5 prior deposits for baseline

    def _parameters(self) -> Dict[str, Any]:
        return {
            "multiplier_threshold": self.MULTIPLIER_THRESHOLD,
            "min_history_deposits": self.MIN_HISTORY_DEPOSITS,
        }

    def evaluate(self, context: RuleContext) -> RuleResult:
        if context.transaction_type != "deposit":
            return self._not_fired()

        # Need enough history to compute a meaningful baseline
        deposit_count_30d = int(context.get("deposit_count_7d", 0))
        if deposit_count_30d < self.MIN_HISTORY_DEPOSITS:
            return self._not_fired()

        deposit_amount_30d = float(context.get("deposit_amount_7d", 0))
        if deposit_amount_30d <= 0:
            return self._not_fired()

        avg_deposit = deposit_amount_30d / max(deposit_count_30d, 1)
        multiplier = context.amount / avg_deposit if avg_deposit > 0 else 0.0

        if multiplier < self.MULTIPLIER_THRESHOLD:
            return self._not_fired()

        return self._fired(
            evidence={
                "current_amount": context.amount,
                "player_avg_deposit": avg_deposit,
                "multiplier": round(multiplier, 2),
                "threshold": self.MULTIPLIER_THRESHOLD,
            },
            explanation=(
                f"Deposit of {context.amount} {context.currency} is "
                f"{multiplier:.1f}× the player's historical average "
                f"({avg_deposit:.0f} {context.currency})."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-GEO-001  Geo anomaly — new country login
# ---------------------------------------------------------------------------

class GeoAnomalyRule(BaseRule):
    """
    Flag when a financial transaction originates from a country the player
    has never used before.

    New-country deposits (especially combined with large amounts or new devices)
    are the canonical account-takeover detection signal.

    This rule integrates with the four-layer geo-fencing architecture from
    Chapter 24 — the `country_code` in context is already verified by the
    application-layer MaxMind check before reaching the fraud API.
    """

    rule_id = "RULE-GEO-001"
    name = "Geo Anomaly — New Country"
    description = (
        "Transaction originated from a country not previously seen for this player. "
        "Account takeover and credential-stuffing signal."
    )
    typology = FraudTypology.GEO_ANOMALY
    base_score_contribution = 0.20

    def evaluate(self, context: RuleContext) -> RuleResult:
        if not context.country_code:
            return self._not_fired()

        known_countries: List[str] = context.get("known_countries", [])
        if not known_countries:
            # No history — cannot determine anomaly; treat as low risk
            return self._not_fired()

        if context.country_code in known_countries:
            return self._not_fired()

        return self._fired(
            evidence={
                "current_country": context.country_code,
                "known_countries": known_countries,
            },
            explanation=(
                f"Transaction from {context.country_code}, "
                f"not in player's known countries: {known_countries}."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-GEO-002  Impossible travel
# ---------------------------------------------------------------------------

class ImpossibleTravelRule(BaseRule):
    """
    Detect two logins/transactions from geographically distant countries
    within a time window that makes physical travel impossible.

    Classic account-takeover and credential-sharing signal.
    Threshold: two different countries within 2 hours = impossible travel.
    """

    rule_id = "RULE-GEO-002"
    name = "Impossible Travel"
    description = (
        "Two transactions from different countries within 2 hours — "
        "physically impossible travel time. Account compromise signal."
    )
    typology = FraudTypology.ACCOUNT_TAKEOVER
    base_score_contribution = 0.45

    IMPOSSIBLE_TRAVEL_WINDOW_HOURS = 2

    def _parameters(self) -> Dict[str, Any]:
        return {"window_hours": self.IMPOSSIBLE_TRAVEL_WINDOW_HOURS}

    def evaluate(self, context: RuleContext) -> RuleResult:
        if not context.country_code:
            return self._not_fired()

        last_login_country: Optional[str] = context.get("last_login_country")
        last_login_at_raw = context.get("last_login_at")

        if not last_login_country or not last_login_at_raw:
            return self._not_fired()

        if last_login_country == context.country_code:
            return self._not_fired()

        # Parse last_login_at — may arrive as ISO string or datetime
        if isinstance(last_login_at_raw, str):
            try:
                last_login_at = datetime.fromisoformat(last_login_at_raw)
            except ValueError:
                return self._not_fired()
        else:
            last_login_at = last_login_at_raw

        if last_login_at.tzinfo is None:
            last_login_at = last_login_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        hours_since = (now - last_login_at).total_seconds() / 3600

        if hours_since > self.IMPOSSIBLE_TRAVEL_WINDOW_HOURS:
            return self._not_fired()

        return self._fired(
            evidence={
                "current_country": context.country_code,
                "previous_country": last_login_country,
                "hours_between_logins": round(hours_since, 2),
                "window_hours": self.IMPOSSIBLE_TRAVEL_WINDOW_HOURS,
            },
            explanation=(
                f"Transaction from {context.country_code} only {hours_since:.1f}h "
                f"after previous login from {last_login_country} — impossible travel."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-DEV-001  Device fingerprint sharing
# ---------------------------------------------------------------------------

class DeviceSharingRule(BaseRule):
    """
    Detect multiple distinct player accounts operating from the same device
    fingerprint.  This is the primary multi-accounting detection signal for
    bonus abuse and synthetic identity fraud.

    The device_player_count comes from a Redis SET keyed on device fingerprint
    hash — maintained by the Kafka consumer as events arrive.

    Reference: Chapter 19 — 'multiple accounts per device' signal.
    """

    rule_id = "RULE-DEV-001"
    name = "Device Sharing — Multiple Accounts per Device"
    description = (
        "Device fingerprint is shared across multiple player accounts. "
        "Primary signal for multi-accounting, bonus abuse, and synthetic identity fraud."
    )
    typology = FraudTypology.DEVICE_SHARING
    base_score_contribution = 0.35

    MAX_PLAYERS_PER_DEVICE = 2   # allow 2 to tolerate family/household devices

    def _parameters(self) -> Dict[str, Any]:
        return {"max_players_per_device": self.MAX_PLAYERS_PER_DEVICE}

    def evaluate(self, context: RuleContext) -> RuleResult:
        if not context.device_fingerprint:
            return self._not_fired()

        device_player_count = int(context.get("device_player_count", 0))

        if device_player_count <= self.MAX_PLAYERS_PER_DEVICE:
            return self._not_fired()

        return self._fired(
            evidence={
                "device_fingerprint": context.device_fingerprint[:16] + "...",
                "player_count_on_device": device_player_count,
                "threshold": self.MAX_PLAYERS_PER_DEVICE,
            },
            explanation=(
                f"Device fingerprint is associated with {device_player_count} "
                f"distinct player accounts (max allowed: {self.MAX_PLAYERS_PER_DEVICE})."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-STR-001  Structuring (smurfing)
# ---------------------------------------------------------------------------

class StructuringRule(BaseRule):
    """
    Detect deposit structuring — making repeated deposits just below the AML
    cash transaction reporting threshold to avoid regulatory scrutiny.

    This is a FATF typology explicitly referenced in AMLD6 Annex III as a
    higher-risk factor.  The rule fires when:
      1. The current deposit falls within 10% below the jurisdiction's
         AML reporting threshold, AND
      2. The player has made >= 3 similar deposits in the last 24 hours.

    Reference: Chapter 19 — 'EUR 10K problem' / structuring detection.
    """

    rule_id = "RULE-STR-001"
    name = "Structuring — Deposits Near AML Reporting Threshold"
    description = (
        "Repeated deposits just below the AML reporting threshold. "
        "Classic 'smurfing' / structuring pattern (FATF typology, AMLD6 Annex III)."
    )
    typology = FraudTypology.STRUCTURING
    base_score_contribution = 0.50
    # Structuring is a predicate offence under AMLD6 — high base score

    MIN_REPETITIONS = 3   # minimum similar deposits before rule fires

    def _parameters(self) -> Dict[str, Any]:
        return {
            "structuring_band_pct": STRUCTURING_BAND_PCT,
            "min_repetitions": self.MIN_REPETITIONS,
            "thresholds": AML_THRESHOLD_EUR_CENTS,
        }

    def evaluate(self, context: RuleContext) -> RuleResult:
        if context.transaction_type != "deposit":
            return self._not_fired()

        threshold = AML_THRESHOLD_EUR_CENTS.get(
            context.jurisdiction, AML_THRESHOLD_EUR_CENTS[Jurisdiction.UNKNOWN]
        )
        lower_bound = threshold * (1 - STRUCTURING_BAND_PCT)

        # Is current deposit in the structuring band?
        if not (lower_bound <= context.amount < threshold):
            return self._not_fired()

        # Count how many of today's deposits were also in the structuring band
        deposit_amounts_24h: List[float] = context.get("deposit_amounts_24h", [])
        band_deposits = [
            amt for amt in deposit_amounts_24h
            if lower_bound <= amt < threshold
        ]

        if len(band_deposits) < self.MIN_REPETITIONS:
            return self._not_fired()

        return self._fired(
            evidence={
                "current_amount": context.amount,
                "aml_threshold": threshold,
                "lower_bound": lower_bound,
                "band_deposits_24h": len(band_deposits),
                "min_repetitions": self.MIN_REPETITIONS,
                "jurisdiction": context.jurisdiction,
            },
            explanation=(
                f"Deposit of {context.amount} falls within {STRUCTURING_BAND_PCT*100:.0f}% "
                f"below the AML reporting threshold ({threshold} minor units) for "
                f"{context.jurisdiction}. Player has made {len(band_deposits)} similar "
                f"deposits in the last 24 hours — structuring pattern detected."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-BON-001  Bonus abuse / multi-accounting
# ---------------------------------------------------------------------------

class BonusAbuseRule(BaseRule):
    """
    Detect multi-accounting for welcome bonus harvesting.

    Welcome bonuses are the most frequently abused promotional mechanic.
    The rule combines device sharing with bonus claim status — if a device
    fingerprint is associated with a player who has already claimed a welcome
    bonus, a new account registration or first deposit from the same device
    is almost certainly a bonus farming attempt.

    Reference: Chapter 19 — 'Bonus abuse (multi-accounting for welcome bonuses)'.
    """

    rule_id = "RULE-BON-001"
    name = "Bonus Abuse — Multi-Accounting"
    description = (
        "New account or first deposit from a device already associated with "
        "a welcome-bonus-claiming account. Multi-accounting / bonus farming signal."
    )
    typology = FraudTypology.BONUS_ABUSE
    base_score_contribution = 0.40

    def evaluate(self, context: RuleContext) -> RuleResult:
        # Only relevant for first deposits or new player registrations
        if context.deposit_number not in (None, 1):
            return self._not_fired()

        if not context.device_fingerprint:
            return self._not_fired()

        device_player_count = int(context.get("device_player_count", 0))
        bonus_claimed_on_device = bool(context.get("bonus_claimed_on_device", False))

        if device_player_count <= 1 or not bonus_claimed_on_device:
            return self._not_fired()

        return self._fired(
            evidence={
                "device_fingerprint": context.device_fingerprint[:16] + "...",
                "device_player_count": device_player_count,
                "bonus_claimed_on_device": bonus_claimed_on_device,
                "deposit_number": context.deposit_number,
            },
            explanation=(
                f"First deposit from a device already used by {device_player_count} "
                f"players, at least one of whom claimed a welcome bonus. "
                f"Bonus farming / multi-accounting pattern."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-CRD-001  Card testing
# ---------------------------------------------------------------------------

class CardTestingRule(BaseRule):
    """
    Detect card testing — rapid small deposits using multiple distinct card
    BINs to verify stolen card validity before larger fraud.

    Card testing typically looks like:
      - 3+ deposits in < 1 hour
      - Each using a different card BIN
      - Amounts are small (common test amounts: EUR 1, EUR 0.99, EUR 1.50)

    The `card_bins_1h` field in context is populated by the Kafka consumer
    tracking distinct BINs per player in a 1-hour Redis sliding window.

    Reference: Chapter 19 — 'Card testing (rapid small deposits with different cards)'.
    PCI DSS Req. 10.2.1: Log all access to cardholder data and related decisions.
    """

    rule_id = "RULE-CRD-001"
    name = "Card Testing — Multiple BINs in Short Window"
    description = (
        "Multiple small deposits using distinct card BINs within 60 minutes. "
        "Stolen card validation / card testing pattern."
    )
    typology = FraudTypology.CARD_TESTING
    base_score_contribution = 0.45

    MIN_DISTINCT_BINS = 3
    MAX_AMOUNT_THRESHOLD = 500    # minor units — small test deposits

    def _parameters(self) -> Dict[str, Any]:
        return {
            "min_distinct_bins": self.MIN_DISTINCT_BINS,
            "max_amount_threshold": self.MAX_AMOUNT_THRESHOLD,
        }

    def evaluate(self, context: RuleContext) -> RuleResult:
        if context.transaction_type != "deposit":
            return self._not_fired()
        if context.payment_method not in ("card", "credit_card", "debit_card", None):
            return self._not_fired()
        if context.amount > self.MAX_AMOUNT_THRESHOLD:
            return self._not_fired()

        card_bins_1h: List[str] = context.get("card_bins_1h", [])
        distinct_bins = len(set(card_bins_1h))

        if distinct_bins < self.MIN_DISTINCT_BINS:
            return self._not_fired()

        return self._fired(
            evidence={
                "current_amount": context.amount,
                "distinct_bins_1h": distinct_bins,
                "threshold_bins": self.MIN_DISTINCT_BINS,
                "max_amount_threshold": self.MAX_AMOUNT_THRESHOLD,
            },
            explanation=(
                f"Player made deposits with {distinct_bins} distinct card BINs "
                f"in the last hour. Current deposit amount ({context.amount}) is "
                f"below {self.MAX_AMOUNT_THRESHOLD} — card testing pattern."
            ),
        )


# ---------------------------------------------------------------------------
# RULE-COL-001  Collusion signal
# ---------------------------------------------------------------------------

class CollusionRule(BaseRule):
    """
    Detect one-directional fund flow between a player pair — the primary
    signal for poker/multiplayer game collusion.

    Collusion: player A consistently loses to player B at the same table.
    The colluding pair transfers real money without triggering withdrawal
    alerts — the 'laundering through gameplay' method.

    The `collusion_score` in context is pre-computed by the Elasticsearch
    aggregation pipeline that tracks win/loss patterns between player pairs
    over 30-day rolling windows.

    Reference: Chapter 19 — 'Collusion Rings' section.
    """

    rule_id = "RULE-COL-001"
    name = "Collusion — Directional Fund Flow Pattern"
    description = (
        "Player exhibits persistent one-directional fund flow with specific counterparties. "
        "Poker / multiplayer game collusion signal."
    )
    typology = FraudTypology.COLLUSION
    base_score_contribution = 0.35

    COLLUSION_SCORE_THRESHOLD = 0.70   # pre-computed pair-collusion score

    def _parameters(self) -> Dict[str, Any]:
        return {"collusion_score_threshold": self.COLLUSION_SCORE_THRESHOLD}

    def evaluate(self, context: RuleContext) -> RuleResult:
        collusion_score = float(context.get("collusion_score", 0.0))
        colluding_partner = context.get("colluding_partner_id")

        if collusion_score < self.COLLUSION_SCORE_THRESHOLD:
            return self._not_fired()

        return self._fired(
            evidence={
                "collusion_score": collusion_score,
                "colluding_partner_id": colluding_partner,
                "threshold": self.COLLUSION_SCORE_THRESHOLD,
            },
            explanation=(
                f"Player has a collusion score of {collusion_score:.2f} with "
                f"player {colluding_partner} (threshold: {self.COLLUSION_SCORE_THRESHOLD}). "
                f"Persistent one-directional fund flow detected over 30-day window."
            ),
        )


# ---------------------------------------------------------------------------
# Rules Registry
# ---------------------------------------------------------------------------

class RulesRegistry:
    """
    Central registry of all active fraud detection rules.

    The registry is the single source of truth for:
      - which rules are loaded (GET /fraud/rules)
      - rule execution order (rules run in registration order)
      - score accumulation and capping

    Design decision: rules are instantiated once at startup and reused across
    requests.  This is safe because each `evaluate()` call is stateless —
    all state lives in the `RuleContext` passed to it.
    """

    def __init__(self) -> None:
        self._rules: List[BaseRule] = []
        self._register_default_rules()

    def _register_default_rules(self) -> None:
        """Register the full rule catalogue at startup."""
        self._rules = [
            DepositVelocityRule(),
            BetVelocityRule(),
            AmountAnomalyRule(),
            GeoAnomalyRule(),
            ImpossibleTravelRule(),
            DeviceSharingRule(),
            StructuringRule(),
            BonusAbuseRule(),
            CardTestingRule(),
            CollusionRule(),
        ]
        logger.info(
            "Rules registry initialised",
            extra={"rule_count": len(self._rules)},
        )

    def get_all(self) -> List[DetectionRule]:
        """Return all rules as API models (for GET /fraud/rules)."""
        return [r.to_detection_rule() for r in self._rules]

    def get_active_count(self) -> int:
        return sum(1 for r in self._rules if r.status == RuleStatus.ACTIVE)

    def evaluate_all(
        self, context: RuleContext
    ) -> tuple[float, List[RuleResult]]:
        """
        Run every applicable rule against `context`.

        Returns:
            (aggregate_score, results_list)

        `aggregate_score` is the sum of all fired rule contributions, capped
        at 1.0.  The ML ensemble layer adds its own component on top of this
        in `app/main.py`.

        PCI DSS Req. 10.2: Each evaluation is logged with the correlation_id
        so every scoring decision has a complete audit trail.
        """
        results: List[RuleResult] = []
        aggregate_score = 0.0

        for rule in self._rules:
            if not rule.is_applicable(context.jurisdiction):
                continue
            try:
                result = rule.evaluate(context)
                results.append(result)
                if result.fired:
                    aggregate_score += result.score_contribution
                    logger.debug(
                        "Rule fired",
                        extra={
                            "rule_id": rule.rule_id,
                            "player_id": context.player_id,
                            "correlation_id": context.correlation_id,
                            "contribution": result.score_contribution,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Rule evaluation error",
                    extra={
                        "rule_id": rule.rule_id,
                        "player_id": context.player_id,
                        "correlation_id": context.correlation_id,
                        "error": str(exc),
                    },
                )

        return min(aggregate_score, 1.0), results
