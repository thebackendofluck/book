# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 32 — Game Logic Test Framework."""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "implementation", "game-logic")
)

from game_test_framework import (
    DeterministicRNG,
    GameRound,
    GameType,
    SimulationResult,
    SlotsEngine,
    TestAssertion,
    TestSeverity,
)


class TestDeterministicRNG:
    """Validate deterministic RNG produces reproducible, bounded output."""

    def test_same_seed_produces_same_sequence(self):
        rng1 = DeterministicRNG(seed=123)
        rng2 = DeterministicRNG(seed=123)
        seq1 = [rng1.random_int(0, 100) for _ in range(20)]
        seq2 = [rng2.random_int(0, 100) for _ in range(20)]
        assert seq1 == seq2

    def test_reset_replays_sequence(self):
        rng = DeterministicRNG(seed=42)
        first_run = [rng.random_float() for _ in range(10)]
        rng.reset()
        second_run = [rng.random_float() for _ in range(10)]
        assert first_run == second_run

    def test_random_int_within_bounds(self):
        rng = DeterministicRNG(seed=99)
        for _ in range(200):
            val = rng.random_int(5, 15)
            assert 5 <= val <= 15


class TestSlotsEngine:
    """Validate slots engine game round mechanics."""

    def _make_engine(self, seed=42):
        rng = DeterministicRNG(seed=seed)
        config = {"min_bet": 0.10, "max_bet": 500.0}
        return SlotsEngine(rng, config)

    def test_play_round_returns_game_round(self):
        engine = self._make_engine()
        result = engine.play_round(1.0)
        assert isinstance(result, GameRound)
        assert result.bet_amount == 1.0
        assert result.win_amount >= 0

    def test_validate_bet_rejects_out_of_range(self):
        engine = self._make_engine()
        assert engine.validate_bet(1.0) is True
        assert engine.validate_bet(0.01) is False  # below min_bet 0.10
        assert engine.validate_bet(1000.0) is False  # above max_bet 500

    def test_theoretical_rtp_is_valid_percentage(self):
        engine = self._make_engine()
        rtp = engine.get_theoretical_rtp()
        # RTP should be between 0% and 100%
        assert 0.0 < rtp <= 1.0


class TestSimulationResult:
    """Validate SimulationResult data integrity."""

    def test_all_passed_when_no_assertions(self):
        result = SimulationResult(
            game_type=GameType.SLOTS,
            total_rounds=100,
            total_wagered=100.0,
            total_won=95.0,
            rtp=0.95,
            variance=1.5,
            hit_frequency=0.3,
            max_win=50.0,
            max_win_multiplier=50.0,
        )
        assert result.all_passed is True

    def test_all_passed_false_with_failed_assertion(self):
        result = SimulationResult(
            game_type=GameType.BLACKJACK,
            total_rounds=10,
            total_wagered=10.0,
            total_won=8.0,
            rtp=0.80,
            variance=1.0,
            hit_frequency=0.5,
            max_win=5.0,
            max_win_multiplier=5.0,
            assertions=[
                TestAssertion("check", False, 0.96, 0.80, TestSeverity.HIGH)
            ],
        )
        assert result.all_passed is False
