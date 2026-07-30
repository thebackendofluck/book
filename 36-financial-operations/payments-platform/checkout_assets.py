# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Hosted checkout asset generator.

This utility replaces chapter-level skin tooling with a Python workflow that
generates player-facing branding assets and localized payment labels for hosted
checkout pages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class LocaleLabels:
    locale: str
    payment_labels: dict[str, str]
    footer_links: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CheckoutBranding:
    brand_code: str
    display_name: str
    logo_url: str
    accent_color: str
    support_url: str
    locales: list[LocaleLabels]


def render_manifest(branding: CheckoutBranding) -> dict[str, object]:
    return {
        "brand_code": branding.brand_code,
        "display_name": branding.display_name,
        "logo_url": branding.logo_url,
        "accent_color": branding.accent_color,
        "support_url": branding.support_url,
        "locales": [
            {
                "locale": locale.locale,
                "payment_labels": locale.payment_labels,
                "footer_links": locale.footer_links,
            }
            for locale in branding.locales
        ],
    }


def write_manifest(branding: CheckoutBranding, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{branding.brand_code}_checkout_manifest.json"
    target.write_text(json.dumps(render_manifest(branding), indent=2), encoding="utf-8")
    return target
