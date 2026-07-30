#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Seed deterministic development players for local Kubernetes loops."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DevPlayer:
    player_id: str
    email: str
    jurisdiction: str
    balance_cents: int


def build_players(count: int, jurisdiction: str) -> list[DevPlayer]:
    return [
        DevPlayer(
            player_id=f"dev-player-{index:04d}",
            email=f"dev-player-{index:04d}@example.test",
            jurisdiction=jurisdiction,
            balance_cents=100_00,
        )
        for index in range(1, count + 1)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic local seed data")
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--jurisdiction", default="BR")
    args = parser.parse_args()

    players = build_players(args.count, args.jurisdiction)
    print(json.dumps([asdict(player) for player in players], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
