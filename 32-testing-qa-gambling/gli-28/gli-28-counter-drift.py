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

"""GLI-28 v1.0 — session timer / loss-counter drift check.

Holds a long-running session open and samples the on-screen session-time and
net P&L counters once per minute. Asserts that:

    1. The session-time counter advances by 60 +/- 2 seconds each minute.
    2. The loss-counter never decreases unless a settled WIN was rendered.
    3. Neither counter freezes for more than 5 consecutive samples.

A counter that stalls or jumps is the regulator's pet GLI-28 finding. The
test runs at least 30 minutes; longer is better. Output is a CSV plus a
one-line PASS/FAIL summary.

Exit code 0 = PASS, 1 = drift detected, 2 = config error.

Usage:
    BASE_URL=https://staging.acmetocasino.com PLAYER_JWT=... GAME_SLUG=demo-slot \\
        uv run gli-28-counter-drift.py --duration-min 30 --out drift.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # ty: ignore[unresolved-import]
except ImportError:
    print("error: playwright not installed", file=sys.stderr)
    sys.exit(2)


def env(key: str) -> str:
    val = os.environ.get(key)
    if val is None or val == "":
        print(f"missing env: {key}", file=sys.stderr)
        raise SystemExit(2)
    return val


def parse_int_text(s: str) -> int:
    digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
    return int(digits) if digits else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration-min", type=int, default=30)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    base = env("BASE_URL")
    jwt = env("PLAYER_JWT")
    game = env("GAME_SLUG")

    samples: list[dict[str, int | float]] = []
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            extra_http_headers={"Authorization": f"Bearer {jwt}"}
        )
        page = ctx.new_page()
        page.goto(f"{base}/games/{game}", wait_until="networkidle")

        end = time.monotonic() + args.duration_min * 60
        prev_session_seconds: int | None = None
        prev_loss_cents: int | None = None
        frozen_streak = 0
        while time.monotonic() < end:
            t = time.time()
            session_seconds = parse_int_text(
                page.locator('[data-test-id="session-timer"]').first.text_content() or "0"
            )
            loss_cents = parse_int_text(
                page.locator('[data-test-id="session-net-pnl-cents"]').first.text_content() or "0"
            )
            samples.append(
                {"ts": t, "session_seconds": session_seconds, "loss_cents": loss_cents}
            )

            if prev_session_seconds is not None:
                delta = session_seconds - prev_session_seconds
                if delta == 0:
                    frozen_streak += 1
                else:
                    frozen_streak = 0
                if not (58 <= delta <= 62) and delta != 0:
                    failures.append(
                        f"session_timer drift: delta={delta}s (expected 60+-2)"
                    )
                if frozen_streak >= 5:
                    failures.append(
                        f"session_timer frozen for {frozen_streak} samples"
                    )
            if prev_loss_cents is not None and loss_cents < prev_loss_cents:
                failures.append(
                    f"loss_counter decreased without WIN render: {prev_loss_cents} -> {loss_cents}"
                )
            prev_session_seconds = session_seconds
            prev_loss_cents = loss_cents
            time.sleep(60)
        browser.close()

    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", "session_seconds", "loss_cents"])
        writer.writeheader()
        for s in samples:
            writer.writerow(s)

    if failures:
        print(f"FAIL: {len(failures)} drift events", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"OK: {len(samples)} samples over {args.duration_min}min — counters stable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
