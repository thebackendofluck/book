#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Fetch the MGCB monthly-revenue Excel files with a real Chromium.

michigan.gov sits behind Akamai and returns 403 to plain HTTP clients
(httpx/curl), so the collector cannot download these files directly.  A
headless Chromium with a stock TLS fingerprint passes, which is what this
script provides.  It is run from rrc-weekly.sh inside the Playwright
container BEFORE the backfill step, refreshing the two per-year files that
backfill_mi_excel() parses from its cache directory:

    Internet-Gaming---2026.xlsx        -> mi_igaming_2026.xlsx
    Internet-Sports-Betting---2026.xlsx -> mi_sports_2026.xlsx

The per-year files are updated in place by MGCB each month and contain the
full year-to-date history plus the prior year on a second sheet, so one
successful fetch per month keeps Michigan current.  On any failure the
previous month's cached copy is left untouched and the pipeline degrades
gracefully (Michigan just stays one month behind until the next run).
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = ("https://www.michigan.gov/mgcb/-/media/Project/Websites/mgcb"
        "/Detroit-Casino-Revenue-Files")
# (remote filename, cache filename)
FILES = [
    ("Internet-Gaming---2026.xlsx", "mi_igaming_2026.xlsx"),
    ("Internet-Sports-Betting---2026.xlsx", "mi_sports_2026.xlsx"),
]
# XLSX zip magic; Akamai block pages are HTML and must never be cached.
XLSX_MAGIC = b"PK\x03\x04"

def main(cache_dir: str) -> int:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    failures = 0
    with sync_playwright() as pw:
        # Headless Chromium advertises "HeadlessChrome" and trips Akamai's
        # bot fingerprinting (403 even on the warm-up page).  Run HEADED
        # under xvfb (the wrapper uses xvfb-run) with a stock desktop UA.
        browser = pw.chromium.launch(headless=False)
        # Default headed-Chromium UA passes; a spoofed UA string was
        # observed to hang the Akamai edge, so don't override it.
        ctx = browser.new_context(locale="en-US", accept_downloads=True)
        # Warm the Akamai cookies by visiting the human listing page first.
        page = ctx.new_page()
        warm = page.goto("https://www.michigan.gov/mgcb",
                         wait_until="domcontentloaded", timeout=60_000)
        print(f"warm-up status={warm.status if warm else 'none'}", flush=True)
        for remote, local in FILES:
            try:
                # Navigate straight at the file and let Chromium's own
                # downloader fetch it — the same path a human click takes.
                # goto() raises "Download is starting" for direct file
                # navigations; the download handle carries the payload.
                with page.expect_download(timeout=60_000) as dl_info:
                    try:
                        page.goto(f"{BASE}/{remote}", timeout=60_000)
                    except Exception:
                        pass  # "Download is starting" — expected
                dl = dl_info.value
                tmp = cache / (local + ".part")
                dl.save_as(tmp)
                body = tmp.read_bytes()
                if not body.startswith(XLSX_MAGIC):
                    print(f"FAIL {remote}: not xlsx ({body[:4]!r})",
                          file=sys.stderr, flush=True)
                    tmp.unlink(missing_ok=True)
                    failures += 1
                    continue
                tmp.rename(cache / local)
                print(f"OK {remote} -> {local} ({len(body)} bytes)",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {remote}: {exc}", file=sys.stderr, flush=True)
                failures += 1
        browser.close()
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/cache"))
