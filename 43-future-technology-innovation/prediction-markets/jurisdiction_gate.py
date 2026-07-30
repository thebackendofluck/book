#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Jurisdiction and product gating for prediction-market distribution.

Chapter 43c reference implementation. Prediction markets are the most
jurisdiction-fragmented product in iGaming as of mid-2026: the same
contract is a CFTC-regulated derivative in the US, a licensed betting
product in Gibraltar, a prohibited product in Germany, France and the
Netherlands, and a partner-only sports product in Brazil.

This module encodes that fragmentation as data: a policy table keyed by
jurisdiction, evaluated by a pure function that returns an auditable
decision (allowed / mode / reasons / required partner). The policy table
is a snapshot of the publicly reported state as of July 2026 -- treat it
as a template to keep current, not as legal advice.

Distribution modes
------------------
DIRECT            operator's own licence covers B2C in the jurisdiction
PARTNER_EMBEDDED  product surfaces inside a locally licensed partner
                  (the Matchbook / Fanatics Markets pattern)
BLOCKED           no lawful route; geofence and refuse
PENDING_FRAMEWORK jurisdiction is drafting a dedicated regime; block
                  until the framework lands (the Malta pattern)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple

from market_lifecycle import MarketCategory


class AccessMode(str, Enum):
    DIRECT = "DIRECT"
    PARTNER_EMBEDDED = "PARTNER_EMBEDDED"
    BLOCKED = "BLOCKED"
    PENDING_FRAMEWORK = "PENDING_FRAMEWORK"


ALL_CATEGORIES: FrozenSet[MarketCategory] = frozenset(MarketCategory)

SPORTS_ONLY: FrozenSet[MarketCategory] = frozenset(
    {MarketCategory.FOOTBALL, MarketCategory.OTHER_SPORTS}
)


@dataclass(frozen=True)
class JurisdictionPolicy:
    jurisdiction: str                       # ISO 3166-1 alpha-2
    mode: AccessMode
    allowed_categories: FrozenSet[MarketCategory]
    partner: Optional[str] = None           # required local partner for PARTNER_EMBEDDED
    legal_basis: str = ""                   # public citation, reviewed by compliance
    notes: str = ""


@dataclass(frozen=True)
class GateDecision:
    jurisdiction: str
    category: MarketCategory
    allowed: bool
    mode: AccessMode
    partner: Optional[str]
    reasons: Tuple[str, ...]


class UnknownJurisdiction(Exception):
    pass


# Snapshot of the publicly reported distribution map, July 2026.
# Sources are cited in chapter 43c; update this table with every
# regulatory change (it is versioned data, not code).
DEFAULT_POLICIES: Dict[str, JurisdictionPolicy] = {
    "GI": JurisdictionPolicy(
        jurisdiction="GI",
        mode=AccessMode.DIRECT,
        allowed_categories=frozenset({
            MarketCategory.FOOTBALL, MarketCategory.OTHER_SPORTS,
            MarketCategory.ENTERTAINMENT, MarketCategory.CULTURE,
            MarketCategory.WEATHER, MarketCategory.POLITICS,
        }),
        legal_basis="Gibraltar betting intermediary licence (Licence 167, "
                    "granted 2026-03-26; category expansion approved "
                    "2026-07-02)",
        notes="Politics limited to 'selected political events' per the "
              "expansion approval.",
    ),
    "US": JurisdictionPolicy(
        jurisdiction="US",
        mode=AccessMode.PARTNER_EMBEDDED,
        allowed_categories=SPORTS_ONLY,
        partner="fanatics-markets",
        legal_basis="No CFTC designation of its own; access via co-branded "
                    "hub with a CFTC-regulated venue (23 states at launch)",
        notes="State-level availability is resolved upstream by the "
              "partner's own geofencing.",
    ),
    "GB": JurisdictionPolicy(
        jurisdiction="GB",
        mode=AccessMode.PARTNER_EMBEDDED,
        allowed_categories=SPORTS_ONLY,
        partner="matchbook",
        legal_basis="Embedded hub inside Matchbook's UKGC-licensed "
                    "betting exchange",
    ),
    "IE": JurisdictionPolicy(
        jurisdiction="IE",
        mode=AccessMode.PARTNER_EMBEDDED,
        allowed_categories=SPORTS_ONLY,
        partner="matchbook",
        legal_basis="Embedded hub inside Matchbook's Irish-licensed "
                    "betting exchange",
    ),
    "BR": JurisdictionPolicy(
        jurisdiction="BR",
        mode=AccessMode.PARTNER_EMBEDDED,
        allowed_categories=SPORTS_ONLY,
        partner="matchbook",
        legal_basis="Lei 14.790/2023 fixed-odds framework; non-financial "
                    "prediction wagers outside it are prohibited (SPA/MF "
                    "guidance, April 2026). Financial event contracts are "
                    "B3-only, professional investors.",
        notes="Access restricted to sports betting through the partner "
              "platform, per the national framework.",
    ),
    "DE": JurisdictionPolicy(
        jurisdiction="DE",
        mode=AccessMode.BLOCKED,
        allowed_categories=frozenset(),
        legal_basis="GlueStV 2021: product unlicensed; GGL opened a formal "
                    "probe into World Cup prediction-market advertising "
                    "(July 2026)",
    ),
    "FR": JurisdictionPolicy(
        jurisdiction="FR",
        mode=AccessMode.BLOCKED,
        allowed_categories=frozenset(),
        legal_basis="ANJ enforcement against prediction-market platforms; "
                    "signatory of the nine-regulator joint statement",
    ),
    "NL": JurisdictionPolicy(
        jurisdiction="NL",
        mode=AccessMode.BLOCKED,
        allowed_categories=frozenset(),
        legal_basis="KSA enforcement action; signatory of the "
                    "nine-regulator joint statement",
    ),
    "NZ": JurisdictionPolicy(
        jurisdiction="NZ",
        mode=AccessMode.BLOCKED,
        allowed_categories=frozenset(),
        legal_basis="Online Casino Gambling Bill (2025) declares prediction "
                    "markets illegal in its carve-out",
    ),
    "MT": JurisdictionPolicy(
        jurisdiction="MT",
        mode=AccessMode.PENDING_FRAMEWORK,
        allowed_categories=frozenset(),
        legal_basis="MGA exploring a statutory prediction-market framework; "
                    "no licences granted yet",
    ),
}


class JurisdictionGate:
    """Evaluates whether a market category may be offered somewhere."""

    def __init__(self, policies: Optional[Dict[str, JurisdictionPolicy]] = None):
        self.policies = dict(DEFAULT_POLICIES if policies is None else policies)
        self.audit_log: List[GateDecision] = []

    def evaluate(self, jurisdiction: str,
                 category: MarketCategory) -> GateDecision:
        policy = self.policies.get(jurisdiction.upper())
        if policy is None:
            raise UnknownJurisdiction(
                f"no policy for {jurisdiction!r}; default-deny applies"
            )

        reasons: List[str] = []
        allowed = True

        if policy.mode in (AccessMode.BLOCKED, AccessMode.PENDING_FRAMEWORK):
            allowed = False
            reasons.append(f"mode={policy.mode.value}")
        elif category not in policy.allowed_categories:
            allowed = False
            reasons.append(
                f"category {category.value} not in allowed set for "
                f"{policy.jurisdiction}"
            )
        if policy.mode == AccessMode.PARTNER_EMBEDDED and allowed:
            reasons.append(f"route via partner {policy.partner}")
        if policy.legal_basis:
            reasons.append(policy.legal_basis)

        decision = GateDecision(
            jurisdiction=policy.jurisdiction,
            category=category,
            allowed=allowed,
            mode=policy.mode,
            partner=policy.partner if allowed else None,
            reasons=tuple(reasons),
        )
        self.audit_log.append(decision)
        return decision

    def reachable_jurisdictions(
            self, category: MarketCategory) -> List[GateDecision]:
        """All jurisdictions where a category can lawfully be offered."""
        return [
            d for d in (
                self.evaluate(code, category) for code in sorted(self.policies)
            ) if d.allowed
        ]
