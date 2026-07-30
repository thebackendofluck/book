# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Game Aggregation Layer service.
Handles game session lifecycle, bet placement, and round settlement.
"""

import logging
import uuid
from decimal import Decimal
from typing import Any

from app.database import get_cursor
from app.events.publisher import CHANNELS, publish_event
from app.gal.rng import ServerRNG
from app.metrics import game_rounds_bet_amount, game_rounds_total, rng_calls_total
from app.wallet.service import create_event as wallet_event

logger = logging.getLogger(__name__)
rng = ServerRNG()

DEFAULT_RTP = Decimal("96.00")


def get_recent_rounds(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve the most recent game rounds across all players."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, session_id, player_id, game_slug, bet_amount,
                   win_amount, rng_seed_hash, target_rtp, created_at
            FROM game_rounds
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def _get_target_rtp(game_slug: str) -> Decimal:
    """Fetch the configured RTP for a game, falling back to default."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT target_rtp FROM rtp_configs WHERE game_slug = %s",
            (game_slug,),
        )
        row = cur.fetchone()
    return Decimal(str(row["target_rtp"])) if row else DEFAULT_RTP


def launch_game(player_id: uuid.UUID, game_slug: str) -> dict[str, Any]:
    """
    Create a new game session.
    Validates the player exists and is active.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, status, kyc_status FROM players WHERE id = %s",
            (str(player_id),),
        )
        player = cur.fetchone()

    if player is None:
        raise ValueError("Player not found")
    if player["status"] != "active":
        raise ValueError(f"Player account is {player['status']}")
    if player["kyc_status"] != "verified":
        raise ValueError(f"KYC verification required before playing (current: {player['kyc_status']})")

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_sessions (player_id, game_slug)
            VALUES (%s, %s)
            RETURNING id, player_id, game_slug, status, rounds_played,
                      total_bet, total_win, created_at, closed_at
            """,
            (str(player_id), game_slug),
        )
        session = dict(cur.fetchone())

    publish_event(
        CHANNELS["game"],
        "game.session_started",
        {
            "session_id": str(session["id"]),
            "player_id": str(player_id),
            "game_slug": game_slug,
        },
    )
    logger.info("Game session started: %s for player %s on %s", session["id"], player_id, game_slug)
    return session


def get_session(session_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a game session by ID."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, game_slug, status, rounds_played,
                   total_bet, total_win, created_at, closed_at
            FROM game_sessions WHERE id = %s
            """,
            (str(session_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def place_bet(session_id: uuid.UUID, bet_amount: Decimal) -> dict[str, Any]:
    """
    Full bet lifecycle:
      1. Validate session is active
      2. Debit wallet (BET event)
      3. Generate outcome via server-side CSPRNG
      4. Credit wallet if win (WIN event)
      5. Record game round for audit
      6. Update session aggregates
    """
    session = get_session(session_id)
    if session is None:
        raise ValueError("Game session not found")
    if session["status"] != "active":
        raise ValueError(f"Session is {session['status']}")

    player_id = session["player_id"]
    game_slug = session["game_slug"]
    round_id = uuid.uuid4()

    # 1. Debit the bet from wallet
    wallet_event(
        player_id=player_id,
        event_type="BET",
        amount=bet_amount,
        reference_id=round_id,
        metadata={"session_id": str(session_id), "game_slug": game_slug},
    )

    # 2. Generate outcome using CSPRNG
    target_rtp = _get_target_rtp(game_slug)
    rng_calls_total.inc()
    outcome = rng.generate_outcome(target_rtp, bet_amount)
    win_amount = outcome["win_amount"]

    # 3. Credit winnings (if any)
    if win_amount > 0:
        wallet_event(
            player_id=player_id,
            event_type="WIN",
            amount=win_amount,
            reference_id=round_id,
            metadata={"session_id": str(session_id), "game_slug": game_slug},
        )

    # 4. Record the round for audit
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_rounds
                (id, session_id, player_id, game_slug, bet_amount, win_amount,
                 rng_seed_hash, rng_output, target_rtp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, session_id, player_id, game_slug, bet_amount,
                      win_amount, rng_seed_hash, target_rtp, created_at
            """,
            (
                str(round_id),
                str(session_id),
                str(player_id),
                game_slug,
                str(bet_amount),
                str(win_amount),
                outcome["rng_seed_hash"],
                outcome["rng_output"],
                str(target_rtp),
            ),
        )
        game_round = dict(cur.fetchone())

        # 5. Update session aggregates
        cur.execute(
            """
            UPDATE game_sessions
            SET rounds_played = rounds_played + 1,
                total_bet = total_bet + %s,
                total_win = total_win + %s
            WHERE id = %s
            """,
            (str(bet_amount), str(win_amount), str(session_id)),
        )

    # 6. Update Prometheus metrics
    game_rounds_total.labels(game_slug=game_slug).inc()
    game_rounds_bet_amount.labels(game_slug=game_slug).observe(float(bet_amount))

    # 7. Fetch updated balance for response
    from app.wallet.service import get_balance as _get_bal
    bal_info = _get_bal(player_id)
    game_round["new_balance"] = bal_info.get("balance", 0)
    game_round["outcome"] = {
        "multiplier": str(outcome["multiplier"]),
        "rng_output": outcome["rng_output"],
    }

    publish_event(
        CHANNELS["game"],
        "game.round_completed",
        {
            "round_id": str(round_id),
            "session_id": str(session_id),
            "player_id": str(player_id),
            "game_slug": game_slug,
            "bet": str(bet_amount),
            "win": str(win_amount),
        },
    )

    return game_round
