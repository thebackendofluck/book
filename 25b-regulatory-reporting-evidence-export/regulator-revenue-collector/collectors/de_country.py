# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Germany Gemeinsame Glücksspielbehörde der Länder (GGL).

GGL renders quarterly stakes (Spieleinsätze) as inline HTML tables on the
Marktmonitor page — no file downloads. Verticals federally licensed under
GlüStV 2021: virtuelle-automatenspiele, online-poker, sportwetten.
Live/table casino is state monopoly (not federal).

Numbers are Spieleinsätze (gross stakes), not GGR — downstream parser must
flag and not aggregate with UKGC/MGA GGR figures.
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://www.gluecksspiel-behoerde.de"
MARKTMONITOR = BASE + "/de/forschung-und-publikationen/publikationen-der-ggl/marktmonitor"
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

VERTICAL_MAP = {
    "Sportwetten":               "sports-wagering",
    "Virtuelle Automatenspiele": "igaming",
    "Online-Poker":              "online-poker",
}


class GermanyCollector(StateCollector):
    state = "GE"  # Germany — "DE" already taken by Delaware in this catalog.
    regulator = "Gemeinsame Glücksspielbehörde der Länder (GGL)"
    source_url = MARKTMONITOR

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(MARKTMONITOR, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        out: list[ReportFile] = []
        for label, vertical in VERTICAL_MAP.items():
            if label not in r.text:
                continue
            out.append(ReportFile(
                operator="DE Statewide",
                vertical=vertical,
                cadence="quarterly",
                format="html",
                source_url=MARKTMONITOR,
            ))
        return out
