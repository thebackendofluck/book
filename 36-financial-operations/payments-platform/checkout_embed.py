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
Embeddable cashier manifest generator.

This utility replaces chapter-level launcher examples with a Python
configuration generator for iframe, modal, or full-page cashier embedding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CheckoutEmbedConfig:
    brand_code: str
    session_token: str
    locale: str
    currency: str
    mode: str
    cashier_url: str
    jurisdiction: str
    theme: dict[str, str]


def build_embed_manifest(config: CheckoutEmbedConfig) -> dict[str, object]:
    return {
        "brand_code": config.brand_code,
        "session_token": config.session_token,
        "locale": config.locale,
        "currency": config.currency,
        "mode": config.mode,
        "cashier_url": config.cashier_url,
        "jurisdiction": config.jurisdiction,
        "theme": config.theme,
    }


def write_embed_manifest(config: CheckoutEmbedConfig, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{config.brand_code}_embed_manifest.json"
    target.write_text(json.dumps(build_embed_manifest(config), indent=2), encoding="utf-8")
    return target
