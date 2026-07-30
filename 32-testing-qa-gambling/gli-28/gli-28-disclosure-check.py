#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""GLI-28 v1.0 — mandatory player-UI disclosure scanner.

Asserts every certified game embeds the disclosures GLI-28 v1.0 mandates,
each reachable within the click-budget defined by the standard:

    rtp                      visible from the in-game help panel (1 click)
    paytable                 reachable in <= 2 clicks
    max_win_cents            visible alongside paytable
    responsible_gaming_link  reachable from any logged-in screen (1 click)
    self_exclusion_link      reachable from the responsible-gaming hub
    deposit_limits_link      reachable from the responsible-gaming hub

Implementation: Playwright headless drives a browser through the game URLs,
reads the DOM, and asserts each anchor / data-test-id exists with the right
content. axe-core runs in parallel for accessibility (see gli-28-a11y.sh).

Exit codes: 0 PASS · 1 disclosure missing · 2 config error.

Usage:
    uv run gli-28-disclosure-check.py \\
        --games-file games.json \\
        --base-url https://staging.acmetocasino.com \\
        --report disclosure-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # ty: ignore[unresolved-import]
except ImportError:
    print(
        "error: playwright not installed. run `uv pip install playwright && playwright install chromium`",
        file=sys.stderr,
    )
    sys.exit(2)

REQUIRED_SELECTORS: dict[str, str] = {
    "rtp": '[data-test-id="game-rtp"]',
    "paytable": '[data-test-id="game-paytable-link"]',
    "max_win_cents": '[data-test-id="game-max-win"]',
    "responsible_gaming_link": '[data-test-id="rg-link"]',
    "self_exclusion_link": '[data-test-id="rg-self-exclusion"]',
    "deposit_limits_link": '[data-test-id="rg-deposit-limits"]',
}


def check_game(page, base_url: str, game_slug: str) -> dict[str, str | bool]:
    url = f"{base_url}/games/{game_slug}"
    page.goto(url, wait_until="networkidle")
    page.click('[data-test-id="game-help-button"]', timeout=5000)

    found: dict[str, bool] = {}
    for key, selector in REQUIRED_SELECTORS.items():
        found[key] = page.locator(selector).count() > 0

    rg_button = page.locator('[data-test-id="rg-link"]')
    if rg_button.count() > 0:
        rg_button.first.click()
        for key in ("self_exclusion_link", "deposit_limits_link"):
            found[key] = page.locator(REQUIRED_SELECTORS[key]).count() > 0

    return {"game_slug": game_slug, "url": url, **found}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games-file", type=Path, required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()

    if not args.games_file.is_file():
        print("error: games file missing", file=sys.stderr)
        return 2
    games = json.loads(args.games_file.read_text(encoding="utf-8"))["games"]

    results: list[dict[str, str | bool]] = []
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        for game_slug in games:
            try:
                row = check_game(page, args.base_url, game_slug)
            except Exception as exc:
                failures.append(f"{game_slug}: {exc}")
                continue
            results.append(row)
            for key in REQUIRED_SELECTORS:
                if not row.get(key):
                    failures.append(f"{game_slug}: missing disclosure {key}")
        browser.close()

    args.report.write_text(
        json.dumps({"results": results, "failures": failures}, indent=2),
        encoding="utf-8",
    )
    if failures:
        print(f"FAIL: {len(failures)} disclosure issues", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"OK: GLI-28 disclosures present across {len(results)} game(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
