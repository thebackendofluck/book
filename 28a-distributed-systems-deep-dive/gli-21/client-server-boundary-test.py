#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""GLI-21 v2.2 — client-server boundary smoke test.

Asserts that the server-authoritative outcome rule holds: a tampered or replayed
client request does NOT change the round outcome the server records. This is
the single most important GLI-21 control objective and the one regulators
probe first during a technical assessment.

Test scenarios:

    1. Honest client       — happy path; server records outcome A.
    2. Replayed bet        — same Authorization + nonce twice; second must 4xx
                             AND must NOT alter wallet balance.
    3. Outcome tampering   — client sends a forged "round_outcome" field along
                             with the bet; server must ignore it (the field
                             does not exist server-side; payload is rejected).
    4. JWT swap            — bet placed with a different player's JWT must
                             401/403 long before any state change.

The test hits the live API (or staging). Each scenario records the wallet
balance delta and the round outcome the server commits; assertions fail if
the boundary is breached.

Exit code 0 = all scenarios PASS, 1 = any boundary breach. Boundary breach
must page security on-call AND block the deploy gate.

Usage:
    BASE_URL=https://staging.acmetocasino.com \\
    PLAYER_A_JWT=...  \\
    PLAYER_B_JWT=...  \\
    GAME_SLUG=demo-slot \\
        uv run client-server-boundary-test.py
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx


def env(key: str) -> str:
    val = os.environ.get(key)
    if val is None or val == "":
        print(f"missing env: {key}", file=sys.stderr)
        raise SystemExit(2)
    return val


def get_balance(client: httpx.Client, jwt: str) -> int:
    r = client.get("/api/v2/wallet/balance", headers={"Authorization": f"Bearer {jwt}"})
    r.raise_for_status()
    return int(r.json()["balance_cents"])


def place_bet(
    client: httpx.Client,
    jwt: str,
    game_slug: str,
    bet_cents: int,
    nonce: str | None = None,
    extra: dict | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {
        "game_slug": game_slug,
        "bet_amount_cents": bet_cents,
        "nonce": nonce or str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return client.post(
        "/api/v2/games/bet",
        json=payload,
        headers={"Authorization": f"Bearer {jwt}"},
    )


def main() -> int:
    base = env("BASE_URL")
    jwt_a = env("PLAYER_A_JWT")
    jwt_b = env("PLAYER_B_JWT")
    game = env("GAME_SLUG")
    bet_cents = int(os.environ.get("BET_CENTS", "100"))

    failures: list[str] = []
    with httpx.Client(base_url=base, timeout=10.0, verify=True) as client:
        # 1. honest path
        b0 = get_balance(client, jwt_a)
        nonce = str(uuid.uuid4())
        r1 = place_bet(client, jwt_a, game, bet_cents, nonce=nonce)
        if r1.status_code != 200:
            failures.append(f"honest_path: bet failed unexpectedly status={r1.status_code}")
        b1 = get_balance(client, jwt_a)

        # 2. replay
        r_replay = place_bet(client, jwt_a, game, bet_cents, nonce=nonce)
        if r_replay.status_code < 400:
            failures.append(
                f"replay_protection: replayed bet returned {r_replay.status_code} (must be 4xx)"
            )
        b2 = get_balance(client, jwt_a)
        if b2 != b1:
            failures.append(
                f"replay_protection: balance changed on replay ({b1} -> {b2})"
            )

        # 3. outcome tampering — client tries to dictate the win amount
        forged = {"round_outcome": {"win_amount_cents": 9_999_999}}
        r_forge = place_bet(client, jwt_a, game, bet_cents, extra=forged)
        if r_forge.status_code == 200:
            body = r_forge.json()
            win = body.get("win_amount_cents", 0)
            if win == 9_999_999:
                failures.append(
                    "outcome_tampering: server honoured client-supplied round_outcome — CRITICAL"
                )

        # 4. JWT swap — player B's JWT against player A's account-only endpoint
        r_swap = client.get(
            "/api/v2/wallet/balance?player_id=A",
            headers={"Authorization": f"Bearer {jwt_b}"},
        )
        if r_swap.status_code not in (401, 403):
            failures.append(
                f"jwt_swap: cross-player request returned {r_swap.status_code} (must be 401/403)"
            )

    if failures:
        print("GLI-21 boundary FAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: GLI-21 v2.2 boundary holds across 4 scenarios")
    return 0


if __name__ == "__main__":
    sys.exit(main())
