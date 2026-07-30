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
Tests for the chapter-11 poker state machine: heads-up blind assignment,
blind rotation across hands, multi-way side pots at showdown, and the
odd-chip tie-break rule.

The module under test lives at a hyphenated path (``../state-machine.py``),
so it can't be reached with a normal ``import`` statement -- it's loaded
here via ``importlib``, same as ``test_side_pot_calculator.py``.
"""

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "state-machine.py"
_spec = importlib.util.spec_from_file_location("state_machine", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
state_machine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_machine)

HandState = state_machine.HandState
Phase = state_machine.Phase
Player = state_machine.Player
PlayerStatus = state_machine.PlayerStatus
PokerStateMachine = state_machine.PokerStateMachine


def _deck():
    suits = ["h", "d", "c", "s"]
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    return [f"{r}{s}" for s in suits for r in ranks]


def _players(seats_and_stacks):
    return [Player(player_id=f"p{seat}", seat=seat, stack=stack) for seat, stack in seats_and_stacks]


class TestHeadsUpBlinds:
    def test_dealer_posts_small_blind_and_acts_first_preflop(self):
        state = HandState(hand_id="H1", table_id="T1")
        sm = PokerStateMachine(state)
        sm.start_hand(_players([(0, 1000), (1, 1000)]), dealer_seat=0, small_blind=10, big_blind=20, deck=_deck())

        assert state.small_blind_seat == 0
        assert state.big_blind_seat == 1
        assert state.players[0].bet_this_round == 10
        assert state.players[1].bet_this_round == 20
        # Heads-up: the dealer (small blind) acts first preflop.
        assert state.action_on_seat == 0

    def test_three_handed_blinds_are_left_of_dealer_and_utg_acts_first(self):
        state = HandState(hand_id="H2", table_id="T1")
        sm = PokerStateMachine(state)
        sm.start_hand(
            _players([(0, 1000), (1, 1000), (2, 1000)]),
            dealer_seat=0,
            small_blind=10,
            big_blind=20,
            deck=_deck(),
        )

        assert state.small_blind_seat == 1
        assert state.big_blind_seat == 2
        # Preflop action starts left of the big blind (seat 0 here), not the dealer.
        assert state.action_on_seat == 0


class TestBlindRotation:
    def test_button_moving_one_seat_reassigns_both_blinds(self):
        specs = [(0, 1000), (1, 1000), (2, 1000)]

        state_a = HandState(hand_id="H1", table_id="T1")
        PokerStateMachine(state_a).start_hand(_players(specs), dealer_seat=0, small_blind=10, big_blind=20, deck=_deck())
        assert (state_a.small_blind_seat, state_a.big_blind_seat) == (1, 2)

        state_b = HandState(hand_id="H2", table_id="T1")
        PokerStateMachine(state_b).start_hand(_players(specs), dealer_seat=1, small_blind=10, big_blind=20, deck=_deck())
        assert (state_b.small_blind_seat, state_b.big_blind_seat) == (2, 0)

        state_c = HandState(hand_id="H3", table_id="T1")
        PokerStateMachine(state_c).start_hand(_players(specs), dealer_seat=2, small_blind=10, big_blind=20, deck=_deck())
        assert (state_c.small_blind_seat, state_c.big_blind_seat) == (0, 1)


def _rank_evaluator(rank_by_pid):
    """Deterministic evaluate_func: hole_cards[0] is the player_id marker,
    rank_by_pid gives its score (lower is better, matching the module's
    convention)."""

    def _evaluate(hole_cards, community):
        pid = hole_cards[0]
        return (rank_by_pid[pid], f"rank-{rank_by_pid[pid]}")

    return _evaluate


def _all_in_player(pid, seat, total_bet):
    return Player(
        player_id=pid,
        seat=seat,
        stack=0,
        status=PlayerStatus.ALL_IN,
        total_bet=total_bet,
        hole_cards=[pid],
    )


class TestShowdownSidePots:
    def test_unequal_allins_distribute_each_pot_to_its_own_best_hand(self):
        # Mirrors the classic 3-way scenario: A short-stacks at 1000, B at
        # 2000, C at 3000. A wins the main pot, B wins side pot 1 (the only
        # layer it's still eligible for), C wins side pot 2 alone.
        state = HandState(hand_id="H1", table_id="T1", dealer_seat=5)
        state.players = {
            1: _all_in_player("A", 1, 1000),
            3: _all_in_player("B", 3, 2000),
            5: _all_in_player("C", 5, 3000),
        }
        state.pot = 6000
        state.community_cards = ["2h", "3d", "4c", "5s", "9h"]
        state.phase = Phase.SHOWDOWN  # _resolve_showdown is normally reached from here
        sm = PokerStateMachine(state, evaluate_func=_rank_evaluator({"A": 1, "B": 50, "C": 500}))

        result = sm._resolve_showdown()

        amounts = {w["player_id"]: w["amount"] for w in result["winners"]}
        assert amounts == {"A": 3000, "B": 2000, "C": 1000}
        assert state.players[1].stack == 3000
        assert state.players[3].stack == 2000
        assert state.players[5].stack == 1000

    def test_odd_chip_goes_to_first_eligible_seat_left_of_dealer(self):
        # Dealer is seat 1, so the first seat to its left is seat 3 (seats
        # wrap 1 -> 3 -> 5 -> 1). p1 and p3 tie for the best hand; the naive
        # "first in eligible list" order would hand the extra chip to p1
        # (lowest seat), which is the bug this test guards against.
        state = HandState(hand_id="H2", table_id="T1", dealer_seat=1)
        state.players = {
            1: _all_in_player("p1", 1, 101),
            3: _all_in_player("p3", 3, 101),
            5: _all_in_player("p5", 5, 101),
        }
        state.pot = 303
        state.community_cards = ["2h", "3d", "4c", "5s", "9h"]
        state.phase = Phase.SHOWDOWN
        sm = PokerStateMachine(state, evaluate_func=_rank_evaluator({"p1": 10, "p3": 10, "p5": 500}))

        result = sm._resolve_showdown()

        amounts = {w["player_id"]: w["amount"] for w in result["winners"]}
        assert "p5" not in amounts
        assert amounts["p3"] == 152  # first left of the dealer gets the odd chip
        assert amounts["p1"] == 151

    def test_folded_player_is_excluded_from_showdown_pot(self):
        state = HandState(hand_id="H3", table_id="T1", dealer_seat=1)
        state.players = {
            1: _all_in_player("p1", 1, 500),
            3: Player(player_id="p3", seat=3, stack=0, status=PlayerStatus.FOLDED, total_bet=500, hole_cards=["p3"]),
            5: _all_in_player("p5", 5, 500),
        }
        state.pot = 1500
        state.community_cards = ["2h", "3d", "4c", "5s", "9h"]
        state.phase = Phase.SHOWDOWN
        sm = PokerStateMachine(state, evaluate_func=_rank_evaluator({"p1": 1, "p5": 2}))

        result = sm._resolve_showdown()

        amounts = {w["player_id"]: w["amount"] for w in result["winners"]}
        assert "p3" not in amounts
        assert amounts == {"p1": 1500}
