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
New York State Gaming Commission collector.

NY publishes revenue reports under predictable Drupal URL slugs:

    https://gaming.ny.gov/{operator-slug}-{cadence}-report-{format}

Hitting one of those URLs directly returns the binary file (server-side
redirect on the regulator's CDN). There is no intermediate landing page
per file; the URL itself is the download.

NY currently permits:
  - Commercial casinos (4 operators)         — weekly + monthly, PDF + Excel
  - Mobile sports wagering                  — weekly + monthly, PDF + Excel
  - Video gaming machine operators (VLT)    — weekly + monthly, PDF + Excel

NY does NOT currently permit online casino (iCasino). Mobile sports
wagering only — which is why this collector reports `vertical = "sports-wagering"`
or `"video-gaming"`, not `"igaming"`.
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://gaming.ny.gov"

COMMERCIAL_CASINOS = [
    ("Del Lago Resort and Casino",       "del-lago-resort-and-casino"),
    ("Resorts World Catskills",          "resorts-world-catskills"),
    ("Rivers Casino and Resort",         "rivers-casino-and-resort"),
    ("Tioga Downs",                      "tioga-downs"),
    ("Statewide Commercial Casinos",     "commercial-casinos-statewide"),
]

SPORTS_WAGERING = [
    ("Bally Bet",                "bally-bet"),
    ("BetMGM",                   "betmgm"),
    ("Caesars Sport Book",       "caesars-sport-book"),
    ("DraftKings Sport Book",    "draftkings-sport-book"),
    ("theScore Bet",             "thescore-bet"),
    ("Fanatics",                 "fanatics"),
    ("FanDuel",                  "fanduel"),
    ("Resorts World Bet",        "resorts-world-bet"),
    ("Rush Street Interactive",  "rush-street-interactive"),
    ("Statewide Sports Wagering", "sports-wagering-statewide"),
]

VIDEO_GAMING = [
    ("Batavia Downs Gaming",                 "batavia-downs-gaming"),
    ("Empire City Casino",                   "empire-city-casino"),
    ("Finger Lakes Gaming & Racetrack",      "finger-lakes-gaming-racetrack"),
    ("Hamburg Gaming",                       "hamburg-gaming"),
    ("Jakes 58",                             "jakes-58"),
    ("Monticello Casino & Raceway",          "monticello-casino-raceway"),
    ("Nassau OTB",                           "nassau-otb"),
    ("Resorts World Casino NYC",             "resorts-world-casino-nyc"),
    ("Resorts World Hudson Valley",          "resorts-world-hudson-valley"),
    ("Saratoga Casino",                      "saratoga-casino"),
    ("Tioga Downs Casino",                   "tioga-downs-casino"),
    ("Vernon Downs Casino",                  "vernon-downs-casino"),
    ("Statewide Video Gaming",               "video-gaming-statewide"),
]

CADENCES = ["weekly", "monthly"]
FORMATS = ["pdf", "excel"]


def _format_to_ext(fmt: str) -> str:
    return "xlsx" if fmt == "excel" else "pdf"


class NewYorkCollector(StateCollector):
    state = "NY"
    regulator = "NY State Gaming Commission"
    source_url = "https://gaming.ny.gov/revenue-reports"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        reports: list[ReportFile] = []
        catalog = (
            [(name, slug, "commercial-casino") for name, slug in COMMERCIAL_CASINOS]
            + [(name, slug, "sports-wagering")  for name, slug in SPORTS_WAGERING]
            + [(name, slug, "video-gaming")     for name, slug in VIDEO_GAMING]
        )
        for name, slug, vertical in catalog:
            for cadence in CADENCES:
                for fmt in FORMATS:
                    reports.append(ReportFile(
                        operator=name,
                        vertical=vertical,
                        cadence=cadence,
                        format=_format_to_ext(fmt),
                        source_url=f"{BASE}/{slug}-{cadence}-report-{fmt}",
                    ))
        return reports
