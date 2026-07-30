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
Tests for the chapter-11 side pot calculator.

The module under test lives at a hyphenated path
(``../side-pot-calculator.py``), so it can't be reached with a normal
``import`` statement -- it's loaded here via ``importlib``.
"""

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "side-pot-calculator.py"
_spec = importlib.util.spec_from_file_location("side_pot_calculator", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
side_pot_calculator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(side_pot_calculator)

calculate_side_pots = side_pot_calculator.calculate_side_pots
calculate_pot_odds = side_pot_calculator.calculate_pot_odds


class TestUnequalAllIns:
    def test_three_way_allin_creates_main_pot_plus_two_side_pots(self):
        bets = [
            ("Alice", 1, 1000, True),
            ("Bob", 3, 2000, True),
            ("Charlie", 5, 3000, True),
        ]
        pots = calculate_side_pots(bets)

        assert [pot["amount"] for pot in pots] == [3000, 2000, 1000]
        assert set(pots[0]["eligible_players"]) == {"Alice", "Bob", "Charlie"}
        assert set(pots[1]["eligible_players"]) == {"Bob", "Charlie"}
        assert pots[2]["eligible_players"] == ["Charlie"]

    def test_four_way_two_distinct_allin_levels(self):
        bets = [
            ("Alice", 1, 300, True),
            ("Bob", 3, 800, True),
            ("Charlie", 5, 1500, True),
            ("Diana", 7, 1500, True),
        ]
        pots = calculate_side_pots(bets)

        assert [pot["amount"] for pot in pots] == [1200, 1500, 1400]
        assert set(pots[0]["eligible_players"]) == {"Alice", "Bob", "Charlie", "Diana"}
        assert set(pots[1]["eligible_players"]) == {"Bob", "Charlie", "Diana"}
        assert set(pots[2]["eligible_players"]) == {"Charlie", "Diana"}

    def test_folded_short_stack_contributes_but_is_not_eligible(self):
        bets = [
            ("Alice", 1, 500, False),  # folded after betting 500
            ("Bob", 3, 1000, True),
            ("Charlie", 5, 1000, True),
        ]
        pots = calculate_side_pots(bets)

        assert pots[0]["amount"] == 1500
        assert "Alice" not in pots[0]["eligible_players"]
        assert set(pots[0]["eligible_players"]) == {"Bob", "Charlie"}

    def test_equal_bets_produce_a_single_pot(self):
        bets = [
            ("Alice", 1, 200, True),
            ("Bob", 3, 200, True),
            ("Carol", 5, 200, True),
        ]
        pots = calculate_side_pots(bets)

        assert len(pots) == 1
        assert pots[0]["amount"] == 600
        assert set(pots[0]["eligible_players"]) == {"Alice", "Bob", "Carol"}

    def test_uncalled_raise_is_returned_not_pooled(self):
        bets = [
            ("Alice", 1, 500, True),
            ("Bob", 3, 1000, True),
        ]
        pots = calculate_side_pots(bets)

        # Called portion goes into the main pot; the uncalled 500 comes back
        # to Bob alone as its own single-contributor layer.
        assert pots[0]["amount"] == 1000
        assert set(pots[0]["eligible_players"]) == {"Alice", "Bob"}
        assert pots[1]["amount"] == 500
        assert pots[1]["eligible_players"] == ["Bob"]
        assert "uncalled" in pots[1]["description"].lower()

    def test_empty_bets_returns_no_pots(self):
        assert calculate_side_pots([]) == []


class TestPotOdds:
    def test_pot_odds_ratio_and_percent(self):
        odds = calculate_pot_odds(1000, 200)

        assert odds["pot_odds_ratio"] == "5.0:1"
        assert abs(odds["pot_odds_percent"] - 16.7) < 0.1

    def test_no_bet_to_call_is_not_applicable(self):
        odds = calculate_pot_odds(1000, 0)

        assert odds["pot_odds_percent"] == 0.0
        assert odds["pot_odds_ratio"] == "N/A (no bet to call)"
