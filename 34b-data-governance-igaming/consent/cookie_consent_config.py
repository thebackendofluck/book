# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cookie Consent Configuration — Jurisdiction-Aware iGaming Implementation.

Regulatory References:
  - EU ePrivacy Directive 2002/58/EC (as amended by 2009/136/EC), Art. 5(3)
  - GDPR (EU) 2016/679, Arts. 4(11), 6(1)(a), 7, 13, 14
  - CCPA / CPRA (California Civil Code §1798.100 et seq.), opt-out model
  - LGPD (Lei 13.709/2018), Arts. 7, 8 — opt-in for non-necessary processing
  - ePrivacy Regulation (draft COM/2017/010) — anticipated EU alignment
  - ICO Cookie Guidance (UK GDPR / PECR SI 2003/2426), Regulation 6
  - Australian Privacy Act 1988 (Cth) + APP 5 — implied consent with notice

Design notes:
  - Opt-in jurisdictions (EU, UK, BR) require prior consent before non-necessary
    cookies are set.
  - Opt-out jurisdictions (US-CA) allow cookies by default but must honour
    opt-out signals (GPC, CCPA opt-out) immediately.
  - Implied-consent jurisdictions (AU) require prominent notice but no banner
    gate; analytics may load on page access if notice is displayed.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConsentModel(str, Enum):
    """Legal basis model that applies to a jurisdiction."""

    OPT_IN = "opt_in"          # Affirmative action required before cookie set
    OPT_OUT = "opt_out"        # Cookies active by default; opt-out must be honoured
    IMPLIED = "implied"        # Notice shown; continued use treated as acceptance


class CookieCategory(str, Enum):
    NECESSARY = "necessary"
    FUNCTIONAL = "functional"
    ANALYTICS = "analytics"
    MARKETING = "marketing"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CookieDefinition:
    name: str
    vendor: str
    purpose: str
    duration_days: int
    cross_site: bool = False
    third_party: bool = False


@dataclass
class CategoryConfig:
    category: CookieCategory
    label: str
    description: str
    always_active: bool
    default_enabled: bool
    cookies: list[CookieDefinition] = field(default_factory=list)


@dataclass
class JurisdictionOverride:
    jurisdiction: str
    model: ConsentModel
    # Categories that must default to disabled regardless of global defaults
    force_disabled_categories: list[CookieCategory] = field(default_factory=list)
    # Re-consent required after this many days (-1 = never)
    reconsent_after_days: int = 180
    # Whether to honour Global Privacy Control signal
    honour_gpc: bool = False


@dataclass
class ConsentConfig:
    jurisdiction: str
    model: ConsentModel
    reconsent_after_days: int
    honour_gpc: bool
    categories: dict[CookieCategory, CategoryConfig] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Global cookie catalogue
# ---------------------------------------------------------------------------

_COOKIE_CATALOGUE: dict[CookieCategory, CategoryConfig] = {
    CookieCategory.NECESSARY: CategoryConfig(
        category=CookieCategory.NECESSARY,
        label="Strictly Necessary",
        description=(
            "Required for the platform to function. Cannot be disabled under "
            "GDPR Art. 5(3) exemption for session management and security."
        ),
        always_active=True,
        default_enabled=True,
        cookies=[
            CookieDefinition("session_id", "Platform", "Authenticated session token", 0),
            CookieDefinition("csrf_token", "Platform", "CSRF protection", 0),
            CookieDefinition("geo_redirect", "Platform", "Jurisdiction routing", 1),
            CookieDefinition("rg_limits", "Platform", "Responsible gambling limits", 30),
            CookieDefinition("cf_clearance", "Cloudflare", "Bot mitigation clearance", 30, cross_site=False, third_party=True),
        ],
    ),
    CookieCategory.FUNCTIONAL: CategoryConfig(
        category=CookieCategory.FUNCTIONAL,
        label="Functional / Preference",
        description=(
            "Enable personalisation features (language, odds format, UI layout). "
            "Legitimate interest may apply in some jurisdictions."
        ),
        always_active=False,
        default_enabled=True,
        cookies=[
            CookieDefinition("lang_pref", "Platform", "Language preference", 365),
            CookieDefinition("odds_format", "Platform", "Odds display format", 365),
            CookieDefinition("theme", "Platform", "UI theme selection", 365),
            CookieDefinition("recently_played", "Platform", "Last-visited game IDs", 90),
        ],
    ),
    CookieCategory.ANALYTICS: CategoryConfig(
        category=CookieCategory.ANALYTICS,
        label="Analytics & Performance",
        description=(
            "Aggregate usage metrics to improve the platform. Data is "
            "pseudonymised. Requires consent under ePrivacy Directive Art. 5(3)."
        ),
        always_active=False,
        default_enabled=False,
        cookies=[
            CookieDefinition("_ga", "Google Analytics 4", "Visitor analytics", 400, third_party=True),
            CookieDefinition("_ga_XXXXXXX", "Google Analytics 4", "Session tracking", 400, third_party=True),
            CookieDefinition("sentry_id", "Sentry", "Error-session correlation", 365, third_party=True),
            CookieDefinition("heap_user", "Heap Analytics", "Funnel analysis", 180, third_party=True),
        ],
    ),
    CookieCategory.MARKETING: CategoryConfig(
        category=CookieCategory.MARKETING,
        label="Marketing & Retargeting",
        description=(
            "Used to deliver targeted promotions and affiliate attribution. "
            "Requires explicit consent (GDPR Art. 6(1)(a))."
        ),
        always_active=False,
        default_enabled=False,
        cookies=[
            CookieDefinition("_fbp", "Meta Pixel", "Facebook retargeting", 90, cross_site=True, third_party=True),
            CookieDefinition("btag", "Platform/Affiliate", "Affiliate click ID", 30),
            CookieDefinition("exacttarget_id", "Salesforce Marketing Cloud", "Email personalisation", 365, third_party=True),
            CookieDefinition("sendgrid_id", "SendGrid / Twilio", "Transactional tracking", 90, third_party=True),
        ],
    ),
}

# ---------------------------------------------------------------------------
# Jurisdiction overrides
# ---------------------------------------------------------------------------

_JURISDICTION_OVERRIDES: dict[str, JurisdictionOverride] = {
    "EU": JurisdictionOverride(
        jurisdiction="EU",
        model=ConsentModel.OPT_IN,
        force_disabled_categories=[CookieCategory.ANALYTICS, CookieCategory.MARKETING, CookieCategory.FUNCTIONAL],
        reconsent_after_days=180,
        honour_gpc=True,
    ),
    "GB": JurisdictionOverride(
        jurisdiction="GB",
        model=ConsentModel.OPT_IN,
        force_disabled_categories=[CookieCategory.ANALYTICS, CookieCategory.MARKETING, CookieCategory.FUNCTIONAL],
        reconsent_after_days=180,
        honour_gpc=True,
    ),
    "US-CA": JurisdictionOverride(
        jurisdiction="US-CA",
        model=ConsentModel.OPT_OUT,
        force_disabled_categories=[CookieCategory.MARKETING],
        reconsent_after_days=365,
        honour_gpc=True,
    ),
    "BR": JurisdictionOverride(
        jurisdiction="BR",
        model=ConsentModel.OPT_IN,
        force_disabled_categories=[CookieCategory.ANALYTICS, CookieCategory.MARKETING],
        reconsent_after_days=365,
        honour_gpc=False,
    ),
    "AU": JurisdictionOverride(
        jurisdiction="AU",
        model=ConsentModel.IMPLIED,
        force_disabled_categories=[],
        reconsent_after_days=-1,
        honour_gpc=False,
    ),
}

_DEFAULT_OVERRIDE = JurisdictionOverride(
    jurisdiction="DEFAULT",
    model=ConsentModel.OPT_IN,
    force_disabled_categories=[CookieCategory.ANALYTICS, CookieCategory.MARKETING],
    reconsent_after_days=180,
    honour_gpc=False,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_consent_config(jurisdiction: str) -> ConsentConfig:
    """Return merged cookie consent configuration for the given jurisdiction.

    Args:
        jurisdiction: ISO 3166-1 alpha-2 country code or region code (e.g.
            "EU", "GB", "US-CA", "AU", "BR"). Case-insensitive.

    Returns:
        :class:`ConsentConfig` with category defaults adjusted for the
        jurisdiction.
    """
    override = _JURISDICTION_OVERRIDES.get(jurisdiction.upper(), _DEFAULT_OVERRIDE)
    logger.debug("Consent model for %s: %s", jurisdiction, override.model)

    merged_categories: dict[CookieCategory, CategoryConfig] = {}
    for cat, cfg in _COOKIE_CATALOGUE.items():
        # Deep-copy to avoid mutating the global catalogue
        enabled = cfg.default_enabled
        if cat in override.force_disabled_categories:
            enabled = False
        merged_categories[cat] = CategoryConfig(
            category=cat,
            label=cfg.label,
            description=cfg.description,
            always_active=cfg.always_active,
            default_enabled=enabled,
            cookies=list(cfg.cookies),
        )

    return ConsentConfig(
        jurisdiction=jurisdiction.upper(),
        model=override.model,
        reconsent_after_days=override.reconsent_after_days,
        honour_gpc=override.honour_gpc,
        categories=merged_categories,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print cookie consent configuration for a jurisdiction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "jurisdiction",
        help="Jurisdiction code, e.g. EU, GB, US-CA, AU, BR",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    logging.basicConfig(level=args.log_level)

    config = get_consent_config(args.jurisdiction)

    output: dict[str, object] = {
        "jurisdiction": config.jurisdiction,
        "model": config.model.value,
        "reconsent_after_days": config.reconsent_after_days,
        "honour_gpc": config.honour_gpc,
        "categories": {
            cat.value: {
                "label": cfg.label,
                "always_active": cfg.always_active,
                "default_enabled": cfg.default_enabled,
                "cookie_count": len(cfg.cookies),
            }
            for cat, cfg in config.categories.items()
        },
    }
    print(json.dumps(output, indent=2))
