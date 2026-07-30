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

"""
Edge Case Generator for iGaming Game Logic Testing
====================================================
Generates exhaustive edge cases per game type to ensure complete
coverage of boundary conditions, rare events, and error states.

Supported game types:
  - Slots: max win, all wilds, empty paylines, scatter edge cases
  - Blackjack: splits, soft 17, multi-ace, 21 combinations
  - Roulette: all bet types, neighbor bets, zero/double-zero
  - Baccarat: natural wins, tie scenarios, dragon bonus
  - Poker: royal flush, split pots, all-in edge cases
  - Craps: all bet resolutions, point/come interactions

Usage:
  python edge_case_generator.py --game slots --output edge_cases.json
  python edge_case_generator.py --game blackjack --format pytest
  python edge_case_generator.py --all --output-dir ./edge_cases/
"""

import itertools
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class EdgeCase:
    """Single edge case test scenario."""
    case_id: str
    game_type: str
    category: str           # e.g., "boundary", "rare_event", "error", "regulatory"
    name: str
    description: str
    input_state: dict        # Initial game state / RNG values to force
    expected_outcome: dict   # Expected results
    priority: str = "high"   # high, medium, low
    regulatory_ref: str = "" # GLI/eCOGRA reference
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "game_type": self.game_type,
            "category": self.category,
            "name": self.name,
            "description": self.description,
            "input_state": self.input_state,
            "expected_outcome": self.expected_outcome,
            "priority": self.priority,
            "regulatory_ref": self.regulatory_ref,
            "tags": self.tags,
        }

    def to_pytest(self) -> str:
        """Generate pytest test function."""
        safe_name = self.name.lower().replace(" ", "_").replace("-", "_")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        return f'''
def test_{safe_name}(game_engine):
    """
    {self.description}
    Category: {self.category} | Priority: {self.priority}
    {f"Regulatory: {self.regulatory_ref}" if self.regulatory_ref else ""}
    """
    # Setup
    state = {json.dumps(self.input_state, indent=4)}

    # Execute
    result = game_engine.play_with_forced_state(state)

    # Assert
    expected = {json.dumps(self.expected_outcome, indent=4)}
    for key, value in expected.items():
        assert result[key] == value, f"{{key}}: expected={{value}}, got={{result[key]}}"
'''


class SlotsEdgeCaseGenerator:
    """Generate edge cases for slot machines."""

    def __init__(self, config: dict = None):  # ty:ignore[invalid-parameter-default]
        self.config = config or {
            "reels": 5,
            "rows": 3,
            "max_win_multiplier": 5000,
            "paylines": 20,
        }

    def generate(self) -> List[EdgeCase]:
        cases = []
        reels = self.config["reels"]
        rows = self.config["rows"]

        # --- Boundary Cases ---
        cases.append(EdgeCase(
            case_id="SLOTS-BOUND-001",
            game_type="slots",
            category="boundary",
            name="All wilds on all reels",
            description="Every position on every reel shows WILD symbol. Should award maximum payline wins.",
            input_state={
                "forced_grid": [["WILD"] * rows for _ in range(reels)],
                "bet": 1.0,
            },
            expected_outcome={
                "total_win_gte": 0,
                "all_paylines_winning": True,
                "win_capped_at_max": True,
            },
            priority="high",
            regulatory_ref="GLI-11 3.1 - Maximum payout verification",
            tags=["max_win", "wild", "boundary"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-BOUND-002",
            game_type="slots",
            category="boundary",
            name="All lowest symbols - zero win",
            description="Grid filled with non-paying symbol combination. Total win must be exactly zero.",
            input_state={
                "forced_grid": [
                    ["LOW3", "LOW2", "LOW1"] if rows == 3
                    else ["LOW3"] * rows
                    for _ in range(reels)
                ],
                "bet": 1.0,
            },
            expected_outcome={
                "total_win": 0.0,
                "winning_lines": 0,
            },
            priority="high",
            tags=["zero_win", "boundary"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-BOUND-003",
            game_type="slots",
            category="boundary",
            name="Max win cap enforcement",
            description="Combination that would exceed max win multiplier. Win must be capped.",
            input_state={
                "forced_grid": [["WILD"] * rows for _ in range(reels)],
                "bet": 100.0,
                "free_spins_active": True,
                "free_spins_multiplier": 10,
            },
            expected_outcome={
                "win_capped": True,
                "max_win": 100.0 * self.config["max_win_multiplier"],
            },
            priority="high",
            regulatory_ref="GLI-11 - Maximum win cap must be enforced",
            tags=["max_win", "regulatory"],
        ))

        # --- Scatter Edge Cases ---
        cases.append(EdgeCase(
            case_id="SLOTS-SCAT-001",
            game_type="slots",
            category="feature",
            name="Exact scatter trigger threshold",
            description=f"Exactly 3 scatters (trigger threshold). Free spins must activate.",
            input_state={
                "forced_symbols": {"SCATTER": [(0, 0), (2, 1), (4, 2)]},
                "bet": 1.0,
            },
            expected_outcome={
                "free_spins_triggered": True,
                "free_spins_count_gte": 1,
            },
            priority="high",
            tags=["scatter", "free_spins"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-SCAT-002",
            game_type="slots",
            category="feature",
            name="Scatters below trigger threshold",
            description="Only 2 scatters (below trigger). Free spins must NOT activate.",
            input_state={
                "forced_symbols": {"SCATTER": [(0, 0), (4, 2)]},
                "bet": 1.0,
            },
            expected_outcome={
                "free_spins_triggered": False,
            },
            priority="high",
            tags=["scatter", "negative_test"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-SCAT-003",
            game_type="slots",
            category="feature",
            name="Maximum scatters on all positions",
            description="All positions show SCATTER. Maximum free spins and scatter pay.",
            input_state={
                "forced_grid": [["SCATTER"] * rows for _ in range(reels)],
                "bet": 1.0,
            },
            expected_outcome={
                "free_spins_triggered": True,
                "scatter_count": reels * rows,
            },
            priority="medium",
            tags=["scatter", "max_scatter"],
        ))

        # --- Free Spins Edge Cases ---
        cases.append(EdgeCase(
            case_id="SLOTS-FS-001",
            game_type="slots",
            category="feature",
            name="Free spins retrigger",
            description="Scatters land during free spins to retrigger additional free spins.",
            input_state={
                "free_spins_active": True,
                "remaining_free_spins": 1,
                "forced_symbols": {"SCATTER": [(0, 0), (2, 1), (4, 2)]},
            },
            expected_outcome={
                "free_spins_retriggered": True,
                "remaining_free_spins_gte": 1,
            },
            priority="high",
            tags=["free_spins", "retrigger"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-FS-002",
            game_type="slots",
            category="feature",
            name="Free spins with zero wins",
            description="All free spins result in zero wins. Balance should not go negative.",
            input_state={
                "free_spins_active": True,
                "free_spins_count": 10,
                "force_zero_wins": True,
            },
            expected_outcome={
                "total_free_spins_win": 0.0,
                "balance_non_negative": True,
            },
            priority="medium",
            tags=["free_spins", "zero_win"],
        ))

        # --- Minimum Bet Edge Cases ---
        cases.append(EdgeCase(
            case_id="SLOTS-BET-001",
            game_type="slots",
            category="boundary",
            name="Minimum bet with maximum win",
            description="Minimum possible bet (0.01) with maximum win combination.",
            input_state={
                "forced_grid": [["WILD"] * rows for _ in range(reels)],
                "bet": 0.01,
            },
            expected_outcome={
                "win_is_positive": True,
                "win_correctly_scaled": True,
            },
            priority="high",
            regulatory_ref="GLI-11 - Minimum bet must be honored",
            tags=["bet", "boundary", "min_bet"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-BET-002",
            game_type="slots",
            category="error",
            name="Zero bet rejection",
            description="Bet of 0.00 should be rejected.",
            input_state={"bet": 0.0},
            expected_outcome={"error": "INVALID_BET", "round_played": False},
            priority="high",
            tags=["bet", "error", "validation"],
        ))

        cases.append(EdgeCase(
            case_id="SLOTS-BET-003",
            game_type="slots",
            category="error",
            name="Negative bet rejection",
            description="Negative bet should be rejected.",
            input_state={"bet": -1.0},
            expected_outcome={"error": "INVALID_BET", "round_played": False},
            priority="high",
            tags=["bet", "error", "validation"],
        ))

        # --- Precision Edge Cases ---
        cases.append(EdgeCase(
            case_id="SLOTS-PREC-001",
            game_type="slots",
            category="boundary",
            name="Floating point precision in win calculation",
            description="Win calculation with values that could cause floating point errors (e.g., 0.1 + 0.2).",
            input_state={
                "bet": 0.30,
                "bet_per_line": 0.015,
                "forced_win_multiplier": 3,
            },
            expected_outcome={
                "win_amount": 0.045,
                "precision_correct": True,
            },
            priority="high",
            regulatory_ref="GLI-11 - Win amounts must be precisely calculated",
            tags=["precision", "floating_point"],
        ))

        return cases


class BlackjackEdgeCaseGenerator:
    """Generate edge cases for blackjack."""

    def generate(self) -> List[EdgeCase]:
        cases = []

        # --- Natural Blackjack Scenarios ---
        cases.append(EdgeCase(
            case_id="BJ-NAT-001",
            game_type="blackjack",
            category="boundary",
            name="Player natural blackjack",
            description="Player gets A+K, dealer does not have blackjack. Player wins 3:2.",
            input_state={
                "player_cards": ["A", "K"],
                "dealer_cards": ["7", "9"],
            },
            expected_outcome={
                "outcome": "player_blackjack",
                "payout_multiplier": 1.5,
            },
            priority="high",
            tags=["blackjack", "natural", "payout"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-NAT-002",
            game_type="blackjack",
            category="boundary",
            name="Both player and dealer natural blackjack",
            description="Both have natural 21. Should be a push.",
            input_state={
                "player_cards": ["A", "Q"],
                "dealer_cards": ["A", "J"],
            },
            expected_outcome={
                "outcome": "push",
                "payout": 0.0,
            },
            priority="high",
            tags=["blackjack", "push"],
        ))

        # --- Soft Hand Edge Cases ---
        cases.append(EdgeCase(
            case_id="BJ-SOFT-001",
            game_type="blackjack",
            category="boundary",
            name="Four aces hand value",
            description="Player dealt four aces. Value should be 14 (one 11 + three 1s).",
            input_state={
                "player_cards": ["A", "A", "A", "A"],
            },
            expected_outcome={
                "hand_value": 14,
                "is_soft": True,
            },
            priority="high",
            tags=["ace", "soft_hand", "value_calculation"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-SOFT-002",
            game_type="blackjack",
            category="boundary",
            name="Ace transition soft to hard",
            description="A+5 (soft 16) -> hit 8 -> hard 14. Ace value must change from 11 to 1.",
            input_state={
                "player_cards": ["A", "5"],
                "hit_cards": ["8"],
            },
            expected_outcome={
                "hand_value": 14,
                "is_soft": False,
                "is_bust": False,
            },
            priority="high",
            tags=["ace", "soft_to_hard"],
        ))

        # --- 21 Combinations (Not Blackjack) ---
        cases.append(EdgeCase(
            case_id="BJ-21-001",
            game_type="blackjack",
            category="boundary",
            name="Three card 21 is not blackjack",
            description="Player gets 7+7+7=21. This is NOT a natural blackjack, pays 1:1.",
            input_state={
                "player_cards": ["7", "7", "7"],
                "dealer_cards": ["10", "8"],
            },
            expected_outcome={
                "is_blackjack": False,
                "payout_multiplier": 1.0,
                "outcome": "player_wins",
            },
            priority="high",
            tags=["21", "not_blackjack"],
        ))

        # --- Dealer Soft 17 ---
        cases.append(EdgeCase(
            case_id="BJ-D17-001",
            game_type="blackjack",
            category="rule_variant",
            name="Dealer hits soft 17 (H17 rule)",
            description="Dealer has A+6 (soft 17). With H17 rule, dealer must hit.",
            input_state={
                "dealer_cards": ["A", "6"],
                "player_stands_at": 18,
                "rule_dealer_hits_soft_17": True,
            },
            expected_outcome={
                "dealer_hit_on_soft_17": True,
                "dealer_cards_count_gte": 3,
            },
            priority="high",
            tags=["dealer", "soft_17", "h17"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-D17-002",
            game_type="blackjack",
            category="rule_variant",
            name="Dealer stands soft 17 (S17 rule)",
            description="Dealer has A+6 (soft 17). With S17 rule, dealer must stand.",
            input_state={
                "dealer_cards": ["A", "6"],
                "player_stands_at": 18,
                "rule_dealer_hits_soft_17": False,
            },
            expected_outcome={
                "dealer_stood_at": 17,
                "dealer_cards_count": 2,
            },
            priority="high",
            tags=["dealer", "soft_17", "s17"],
        ))

        # --- Split Edge Cases ---
        cases.append(EdgeCase(
            case_id="BJ-SPLIT-001",
            game_type="blackjack",
            category="feature",
            name="Split aces receive one card each",
            description="Split aces typically only get one additional card per hand.",
            input_state={
                "player_cards": ["A", "A"],
                "action": "split",
                "split_cards": [["10"], ["K"]],
            },
            expected_outcome={
                "hand_1_value": 21,
                "hand_2_value": 21,
                "hand_1_is_blackjack": False,  # Split aces getting 21 is NOT blackjack
                "one_card_only": True,
            },
            priority="high",
            tags=["split", "aces"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-SPLIT-002",
            game_type="blackjack",
            category="feature",
            name="Split to maximum hands",
            description="Player splits to maximum allowed hands (typically 4).",
            input_state={
                "player_cards": ["8", "8"],
                "subsequent_cards": ["8", "8", "8", "8", "10", "10", "10", "10"],
                "max_splits": 4,
            },
            expected_outcome={
                "total_hands": 4,
                "total_bet_multiplier": 4,
            },
            priority="medium",
            tags=["split", "max_split"],
        ))

        # --- Double Down Edge Cases ---
        cases.append(EdgeCase(
            case_id="BJ-DBL-001",
            game_type="blackjack",
            category="feature",
            name="Double down and bust",
            description="Player doubles on 11, receives a face card, gets 21. Then verify dealer plays.",
            input_state={
                "player_cards": ["5", "6"],
                "action": "double",
                "double_card": "K",
                "dealer_cards": ["7", "10"],
            },
            expected_outcome={
                "player_value": 21,
                "bet_doubled": True,
                "outcome": "player_wins",
            },
            priority="high",
            tags=["double", "21"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-DBL-002",
            game_type="blackjack",
            category="feature",
            name="Double down and bust",
            description="Player doubles on 10, receives K (bust with 20? No, 20 is valid). Test with bust: double on hard 12.",
            input_state={
                "player_cards": ["5", "7"],
                "action": "double",
                "double_card": "K",
            },
            expected_outcome={
                "player_value": 22,
                "is_bust": True,
                "loss_amount_is_double": True,
            },
            priority="high",
            tags=["double", "bust"],
        ))

        # --- Insurance Edge Cases ---
        cases.append(EdgeCase(
            case_id="BJ-INS-001",
            game_type="blackjack",
            category="feature",
            name="Insurance when dealer has blackjack",
            description="Dealer shows A, player takes insurance, dealer has blackjack.",
            input_state={
                "player_cards": ["10", "8"],
                "dealer_cards": ["A", "K"],
                "action": "insurance",
            },
            expected_outcome={
                "insurance_pays": True,
                "insurance_payout_multiplier": 2.0,
                "main_bet_lost": True,
                "net_result": 0.0,  # Insurance covers the loss
            },
            priority="medium",
            tags=["insurance", "dealer_blackjack"],
        ))

        # --- Bust Boundary ---
        cases.append(EdgeCase(
            case_id="BJ-BUST-001",
            game_type="blackjack",
            category="boundary",
            name="Hard 21 is not bust",
            description="Player has exactly 21 (not from first two cards). Must not be treated as bust.",
            input_state={
                "player_cards": ["5", "6", "K"],
            },
            expected_outcome={
                "hand_value": 21,
                "is_bust": False,
                "is_blackjack": False,
            },
            priority="high",
            tags=["bust", "boundary", "21"],
        ))

        cases.append(EdgeCase(
            case_id="BJ-BUST-002",
            game_type="blackjack",
            category="boundary",
            name="Minimum bust value",
            description="Player has exactly 22. This is the minimum bust value.",
            input_state={
                "player_cards": ["10", "5", "7"],
            },
            expected_outcome={
                "hand_value": 22,
                "is_bust": True,
            },
            priority="high",
            tags=["bust", "boundary"],
        ))

        return cases


class RouletteEdgeCaseGenerator:
    """Generate edge cases for roulette."""

    def generate(self) -> List[EdgeCase]:
        cases = []

        # All bet types
        bet_types = [
            ("straight", 17, 35, "Straight up bet on 17"),
            ("split", [17, 18], 17, "Split bet on 17/18"),
            ("street", [1, 2, 3], 11, "Street bet on 1-2-3"),
            ("corner", [1, 2, 4, 5], 8, "Corner bet on 1/2/4/5"),
            ("six_line", [1, 2, 3, 4, 5, 6], 5, "Six line bet"),
            ("column_1", "col_1", 2, "First column"),
            ("dozen_1", "doz_1", 2, "First dozen (1-12)"),
            ("red", "red", 1, "Red"),
            ("black", "black", 1, "Black"),
            ("even", "even", 1, "Even numbers"),
            ("odd", "odd", 1, "Odd numbers"),
            ("low", "1-18", 1, "Low (1-18)"),
            ("high", "19-36", 1, "High (19-36)"),
        ]

        for i, (bet_name, selection, payout_ratio, desc) in enumerate(bet_types):
            cases.append(EdgeCase(
                case_id=f"ROUL-BET-{i+1:03d}",
                game_type="roulette",
                category="bet_type",
                name=f"Winning {bet_name} bet",
                description=f"{desc} - winning scenario. Payout should be {payout_ratio}:1.",
                input_state={
                    "bet_type": bet_name,
                    "selection": selection,
                    "forced_number": 17 if isinstance(selection, int) else (
                        selection[0] if isinstance(selection, list) else 1
                    ),
                    "bet": 10.0,
                },
                expected_outcome={
                    "win": True,
                    "payout": 10.0 * payout_ratio,
                    "total_return": 10.0 * (payout_ratio + 1),
                },
                priority="high",
                tags=["bet_type", bet_name],
            ))

        # Zero edge cases
        cases.append(EdgeCase(
            case_id="ROUL-ZERO-001",
            game_type="roulette",
            category="boundary",
            name="Zero result - all outside bets lose",
            description="When 0 lands, all outside bets (red/black, even/odd, etc.) lose.",
            input_state={
                "forced_number": 0,
                "bets": [
                    {"type": "red", "amount": 10},
                    {"type": "even", "amount": 10},
                    {"type": "low", "amount": 10},
                    {"type": "column_1", "amount": 10},
                ],
            },
            expected_outcome={
                "all_outside_bets_lose": True,
                "total_loss": 40.0,
            },
            priority="high",
            tags=["zero", "outside_bets"],
        ))

        cases.append(EdgeCase(
            case_id="ROUL-ZERO-002",
            game_type="roulette",
            category="boundary",
            name="Straight bet on zero wins",
            description="Straight up bet on 0 when 0 lands. Must pay 35:1.",
            input_state={
                "forced_number": 0,
                "bet_type": "straight",
                "selection": 0,
                "bet": 10.0,
            },
            expected_outcome={
                "win": True,
                "payout": 350.0,
            },
            priority="high",
            tags=["zero", "straight"],
        ))

        # American roulette double zero
        cases.append(EdgeCase(
            case_id="ROUL-00-001",
            game_type="roulette",
            category="boundary",
            name="Double zero in American roulette",
            description="00 lands in American roulette. Must be distinct from 0.",
            input_state={
                "variant": "american",
                "forced_number": "00",
                "bet_type": "straight",
                "selection": "00",
                "bet": 10.0,
            },
            expected_outcome={
                "win": True,
                "payout": 350.0,
                "distinct_from_zero": True,
            },
            priority="high",
            tags=["double_zero", "american"],
        ))

        # Multiple bets on same spin
        cases.append(EdgeCase(
            case_id="ROUL-MULTI-001",
            game_type="roulette",
            category="feature",
            name="Multiple winning bets on single number",
            description="Player bets straight on 17, red, odd, and high. All win on 17.",
            input_state={
                "forced_number": 17,
                "bets": [
                    {"type": "straight", "selection": 17, "amount": 10},
                    {"type": "red", "amount": 10},
                    {"type": "odd", "amount": 10},
                    {"type": "high", "amount": 10},
                ],
            },
            expected_outcome={
                "total_payout": 350 + 10 + 10 + 10,
                "all_bets_resolved": True,
            },
            priority="high",
            tags=["multiple_bets"],
        ))

        return cases


class PokerEdgeCaseGenerator:
    """Generate edge cases for poker hand evaluation."""

    def generate(self) -> List[EdgeCase]:
        cases = []

        hand_rankings = [
            ("POKER-HAND-001", "Royal flush", ["AS", "KS", "QS", "JS", "10S"], "royal_flush", 1),
            ("POKER-HAND-002", "Straight flush", ["9H", "8H", "7H", "6H", "5H"], "straight_flush", 2),
            ("POKER-HAND-003", "Four of a kind", ["KS", "KH", "KD", "KC", "3S"], "four_of_a_kind", 3),
            ("POKER-HAND-004", "Full house", ["QS", "QH", "QD", "7C", "7S"], "full_house", 4),
            ("POKER-HAND-005", "Flush", ["AS", "JS", "8S", "5S", "3S"], "flush", 5),
            ("POKER-HAND-006", "Straight", ["10H", "9S", "8D", "7C", "6H"], "straight", 6),
            ("POKER-HAND-007", "Three of a kind", ["8S", "8H", "8D", "KS", "3C"], "three_of_a_kind", 7),
            ("POKER-HAND-008", "Two pair", ["JS", "JH", "4D", "4C", "AS"], "two_pair", 8),
            ("POKER-HAND-009", "One pair", ["10S", "10H", "AS", "8D", "5C"], "one_pair", 9),
            ("POKER-HAND-010", "High card", ["AS", "JH", "8D", "5C", "3S"], "high_card", 10),
        ]

        for case_id, name, cards, ranking, rank_num in hand_rankings:
            cases.append(EdgeCase(
                case_id=case_id,
                game_type="poker",
                category="hand_evaluation",
                name=f"Hand evaluation - {name}",
                description=f"Verify {name} is correctly identified and ranked (rank {rank_num}).",
                input_state={"cards": cards},
                expected_outcome={"hand_ranking": ranking, "rank": rank_num},
                priority="high",
                tags=["hand_evaluation", ranking],
            ))

        # Edge cases for straights
        cases.append(EdgeCase(
            case_id="POKER-STR-001",
            game_type="poker",
            category="boundary",
            name="Ace-low straight (wheel)",
            description="A-2-3-4-5 is a valid straight (the wheel). Ace counts as low.",
            input_state={"cards": ["AH", "2S", "3D", "4C", "5H"]},
            expected_outcome={"hand_ranking": "straight", "high_card": "5"},
            priority="high",
            tags=["straight", "ace_low", "wheel"],
        ))

        cases.append(EdgeCase(
            case_id="POKER-STR-002",
            game_type="poker",
            category="boundary",
            name="Ace-high straight (broadway)",
            description="10-J-Q-K-A is a valid straight. Ace counts as high.",
            input_state={"cards": ["10H", "JS", "QD", "KC", "AH"]},
            expected_outcome={"hand_ranking": "straight", "high_card": "A"},
            priority="high",
            tags=["straight", "ace_high", "broadway"],
        ))

        cases.append(EdgeCase(
            case_id="POKER-STR-003",
            game_type="poker",
            category="boundary",
            name="Wraparound is NOT a straight",
            description="Q-K-A-2-3 is NOT a valid straight (no wraparound).",
            input_state={"cards": ["QH", "KS", "AD", "2C", "3H"]},
            expected_outcome={"hand_ranking": "high_card", "is_straight": False},
            priority="high",
            tags=["straight", "wraparound", "negative"],
        ))

        # Tiebreaker edge cases
        cases.append(EdgeCase(
            case_id="POKER-TIE-001",
            game_type="poker",
            category="boundary",
            name="Same two pair - kicker decides",
            description="Both players have JJ-44. Winner decided by 5th card kicker.",
            input_state={
                "player1_cards": ["JS", "JH", "4D", "4C", "AS"],
                "player2_cards": ["JD", "JC", "4H", "4S", "KS"],
            },
            expected_outcome={
                "winner": "player1",
                "reason": "higher_kicker",
                "winning_kicker": "A",
            },
            priority="high",
            tags=["tiebreaker", "kicker"],
        ))

        return cases


def generate_all_edge_cases() -> Dict[str, List[dict]]:
    """Generate all edge cases for all game types."""
    generators = {
        "slots": SlotsEdgeCaseGenerator(),
        "blackjack": BlackjackEdgeCaseGenerator(),
        "roulette": RouletteEdgeCaseGenerator(),
        "poker": PokerEdgeCaseGenerator(),
    }

    all_cases = {}
    total = 0
    for game_type, generator in generators.items():
        cases = generator.generate()
        all_cases[game_type] = [c.to_dict() for c in cases]
        total += len(cases)
        print(f"  {game_type}: {len(cases)} edge cases")

    print(f"\nTotal: {total} edge cases across {len(generators)} game types")
    return all_cases


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Edge Case Generator for iGaming")
    parser.add_argument("--game", choices=["slots", "blackjack", "roulette", "poker", "all"], default="all")
    parser.add_argument("--format", choices=["json", "pytest"], default="json")
    parser.add_argument("--output", help="Output file")
    parser.add_argument("--output-dir", help="Output directory (for --all)")

    args = parser.parse_args()

    print("Edge Case Generator for iGaming\n")

    if args.game == "all":
        all_cases = generate_all_edge_cases()
        if args.output:
            with open(args.output, "w") as f:
                json.dump(all_cases, f, indent=2)
            print(f"\nSaved to {args.output}")
        elif args.output_dir:
            import os
            os.makedirs(args.output_dir, exist_ok=True)
            for game_type, cases in all_cases.items():
                path = os.path.join(args.output_dir, f"{game_type}_edge_cases.json")
                with open(path, "w") as f:
                    json.dump(cases, f, indent=2)
            print(f"\nSaved to {args.output_dir}/")
        else:
            print(json.dumps(all_cases, indent=2))
    else:
        generators = {
            "slots": SlotsEdgeCaseGenerator,
            "blackjack": BlackjackEdgeCaseGenerator,
            "roulette": RouletteEdgeCaseGenerator,
            "poker": PokerEdgeCaseGenerator,
        }
        gen = generators[args.game]()
        cases = gen.generate()
        print(f"  {args.game}: {len(cases)} edge cases\n")

        if args.format == "pytest":
            output = "\n".join(c.to_pytest() for c in cases)
        else:
            output = json.dumps([c.to_dict() for c in cases], indent=2)

        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Saved to {args.output}")
        else:
            print(output)


if __name__ == "__main__":
    main()
