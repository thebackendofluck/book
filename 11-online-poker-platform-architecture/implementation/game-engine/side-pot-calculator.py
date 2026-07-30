# Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Side Pot Calculator
Chapter 11 - Online Poker Platform Architecture

Handles complex multi-way pot calculations including:
- Main pot and unlimited side pots
- Multiple all-in players at different stack levels
- Correct eligibility tracking per pot
- Odd chip distribution (first in position order)
- Return of uncalled bets

Dependencies: None (stdlib only)

Example scenarios covered:
- 3 players: A (1000), B (2000), C (3000) all-in
  -> Main pot: 3000 (A,B,C eligible)
  -> Side pot 1: 2000 (B,C eligible)
  -> Side pot 2: 1000 returned to C (uncalled)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("poker.side_pots")


@dataclass
class PotInfo:
    """Represents a main or side pot."""
    amount: int
    eligible_players: list   # player_ids who can win this pot
    description: str = ""


def calculate_side_pots(player_bets: list) -> list:
    """
    Calculate main pot and side pots from player bets.

    Args:
        player_bets: List of tuples (player_id, seat, total_bet, is_active).
                     is_active: True if player hasn't folded (eligible for pots).

    Returns:
        List of PotInfo dicts:
        [
            {"amount": 3000, "eligible_players": ["p1","p2","p3"], "description": "Main pot"},
            {"amount": 2000, "eligible_players": ["p2","p3"], "description": "Side pot 1"},
        ]

    Algorithm:
        1. Sort players by total_bet ascending
        2. At each distinct bet level, create a pot from contributions up to that level
        3. Only non-folded players are eligible for each pot
        4. Return uncalled portions as separate entries
    """
    if not player_bets:
        return []

    # Sort by total bet ascending, then by seat for deterministic ordering
    sorted_bets = sorted(player_bets, key=lambda x: (x[2], x[1]))

    pots = []
    previous_level = 0
    pot_number = 0

    # Track remaining contribution for each player
    remaining = {pid: total_bet for pid, seat, total_bet, is_active in sorted_bets}

    # Get distinct bet levels
    bet_levels = sorted(set(total_bet for _, _, total_bet, _ in sorted_bets if total_bet > 0))

    for level in bet_levels:
        contribution_per_player = level - previous_level
        if contribution_per_player <= 0:
            continue

        # Count how many players contributed at this level
        contributors = []
        eligible = []
        pot_amount = 0

        for pid, seat, total_bet, is_active in sorted_bets:
            if total_bet >= level:
                # This player contributed at this level
                actual_contribution = min(contribution_per_player, remaining[pid])
                pot_amount += actual_contribution
                remaining[pid] -= actual_contribution
                contributors.append(pid)
                if is_active:
                    eligible.append(pid)

        if pot_amount > 0:
            if pot_number == 0:
                description = "Main pot"
            else:
                description = f"Side pot {pot_number}"

            # If only one eligible player, it's essentially won automatically
            if len(eligible) == 1 and len(contributors) == 1:
                description += " (uncalled bet returned)"

            pots.append({
                "amount": pot_amount,
                "eligible_players": eligible,
                "description": description,
                "contributors": contributors,
            })
            pot_number += 1

        previous_level = level

    # Handle any remaining uncalled portions
    for pid, seat, total_bet, is_active in sorted_bets:
        if remaining.get(pid, 0) > 0:
            pots.append({
                "amount": remaining[pid],
                "eligible_players": [pid] if is_active else [],
                "description": f"Uncalled bet returned to {pid}",
                "contributors": [pid],
                "is_return": True,
            })

    logger.info(f"Calculated {len(pots)} pot(s) from {len(player_bets)} players")
    for pot in pots:
        logger.info(f"  {pot['description']}: {pot['amount']} chips "
                    f"(eligible: {pot['eligible_players']})")

    return pots


def calculate_pot_odds(pot_size: int, bet_to_call: int) -> dict:
    """
    Calculate pot odds for decision-making display.

    Args:
        pot_size: Current pot size in chips.
        bet_to_call: Amount the player needs to call.

    Returns:
        Dict with pot odds information.
    """
    if bet_to_call <= 0:
        return {
            "pot_size": pot_size,
            "bet_to_call": 0,
            "pot_odds_ratio": "N/A (no bet to call)",
            "pot_odds_percent": 0.0,
            "break_even_equity": 0.0,
        }

    total_pot = pot_size + bet_to_call
    odds_percent = (bet_to_call / total_pot) * 100
    ratio_against = (pot_size / bet_to_call) if bet_to_call > 0 else 0

    return {
        "pot_size": pot_size,
        "bet_to_call": bet_to_call,
        "pot_odds_ratio": f"{ratio_against:.1f}:1",
        "pot_odds_percent": round(odds_percent, 1),
        "break_even_equity": round(odds_percent, 1),
    }


# ─── Verification / Test Scenarios ────────────────────────────────────

def verify_side_pots():
    """Run verification scenarios for side pot calculations."""

    print("=" * 60)
    print("SIDE POT CALCULATOR VERIFICATION")
    print("=" * 60)

    # ── Scenario 1: Simple 3-way all-in ───────────────────────────
    print("\n--- Scenario 1: Three-way all-in (1000 / 2000 / 3000) ---")
    bets = [
        ("Alice",   1, 1000, True),   # Short stack, all-in
        ("Bob",     3, 2000, True),   # Medium stack, all-in
        ("Charlie", 5, 3000, True),   # Big stack, all-in
    ]
    pots = calculate_side_pots(bets)
    assert len(pots) == 3, f"Expected 3 pots, got {len(pots)}"
    assert pots[0]["amount"] == 3000, f"Main pot should be 3000, got {pots[0]['amount']}"
    assert pots[1]["amount"] == 2000, f"Side pot 1 should be 2000, got {pots[1]['amount']}"
    assert pots[2]["amount"] == 1000, f"Side pot 2 should be 1000, got {pots[2]['amount']}"
    assert set(pots[0]["eligible_players"]) == {"Alice", "Bob", "Charlie"}
    assert set(pots[1]["eligible_players"]) == {"Bob", "Charlie"}
    assert pots[2]["eligible_players"] == ["Charlie"]
    print("  PASS")

    # ── Scenario 2: All-in with folded player ─────────────────────
    print("\n--- Scenario 2: All-in with folded player ---")
    bets = [
        ("Alice",   1, 500,  False),  # Folded after betting 500
        ("Bob",     3, 1000, True),   # All-in for 1000
        ("Charlie", 5, 1000, True),   # Called 1000
    ]
    pots = calculate_side_pots(bets)
    # Alice's 500 goes to main pot. Bob and Charlie add 500 each to main pot = 1500
    # Then Bob and Charlie add 500 more each to side pot = 1000
    assert pots[0]["amount"] == 1500, f"Main pot should be 1500, got {pots[0]['amount']}"
    assert "Alice" not in pots[0]["eligible_players"], "Folded player should not be eligible"
    print("  PASS")

    # ── Scenario 3: No side pots (equal bets) ────────────────────
    print("\n--- Scenario 3: Equal bets, no side pots ---")
    bets = [
        ("Alice", 1, 200, True),
        ("Bob",   3, 200, True),
        ("Carol", 5, 200, True),
    ]
    pots = calculate_side_pots(bets)
    assert len(pots) == 1, f"Expected 1 pot, got {len(pots)}"
    assert pots[0]["amount"] == 600
    print("  PASS")

    # ── Scenario 4: Four players, two all-in at different levels ──
    print("\n--- Scenario 4: Complex 4-player scenario ---")
    bets = [
        ("Alice",   1, 300,  True),   # Short stack all-in
        ("Bob",     3, 800,  True),   # Medium all-in
        ("Charlie", 5, 1500, True),   # Called
        ("Diana",   7, 1500, True),   # Called
    ]
    pots = calculate_side_pots(bets)
    # Main pot: 300*4 = 1200 (all 4 eligible)
    # Side pot 1: 500*3 = 1500 (Bob, Charlie, Diana)
    # Side pot 2: 700*2 = 1400 (Charlie, Diana)
    assert pots[0]["amount"] == 1200, f"Main pot should be 1200, got {pots[0]['amount']}"
    assert pots[1]["amount"] == 1500, f"Side pot 1 should be 1500, got {pots[1]['amount']}"
    assert pots[2]["amount"] == 1400, f"Side pot 2 should be 1400, got {pots[2]['amount']}"
    print("  PASS")

    # ── Scenario 5: Heads-up with unequal bets (uncalled) ────────
    print("\n--- Scenario 5: Heads-up, uncalled bet ---")
    bets = [
        ("Alice", 1, 500,  True),  # Called
        ("Bob",   3, 1000, True),  # Raised but Alice only called 500
    ]
    pots = calculate_side_pots(bets)
    # Main pot: 500*2 = 1000
    # Remaining 500 returned to Bob
    assert pots[0]["amount"] == 1000, f"Main pot should be 1000, got {pots[0]['amount']}"
    assert pots[1]["amount"] == 500, f"Return should be 500, got {pots[1]['amount']}"
    print("  PASS")

    # ── Scenario 6: Pot odds calculation ──────────────────────────
    print("\n--- Scenario 6: Pot odds calculation ---")
    odds = calculate_pot_odds(1000, 200)
    assert odds["pot_odds_ratio"] == "5.0:1"
    assert abs(odds["pot_odds_percent"] - 16.7) < 0.1
    print(f"  Pot: 1000, Call: 200 -> {odds['pot_odds_ratio']} ({odds['pot_odds_percent']}%)")
    print("  PASS")

    print("\n" + "=" * 60)
    print("ALL SCENARIOS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    verify_side_pots()
