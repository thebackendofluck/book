// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package main

import (
	"testing"
)

// TestSettleAccumulator_HalfLostMatrix_item_9 exhaustively pins the
// settlement contract across the full grid of leg-result combinations:
// won, half-won, half-lost, lost, void, and pending. Each row's expected
// payout/odds was computed by hand from the rules in SettleAccumulator:
//
//   - won            -> activeOdds *= legOdds
//   - half-won       -> activeOdds *= (legOdds-1)/2 + 1
//   - half-lost      -> activeOdds *= 0.5
//   - void           -> activeOdds *= 1.0 (contributes nothing)
//   - lost (any)     -> result=Lost, payout=0, odds=0
//   - pending (any)  -> result=Pending, payout=0, odds=0
//   - all void       -> result=Void,  payout=stake, odds=1.0
//
// This is the rollout DoD for item 9 (BREAKING FINANCEIRO): every
// permutation that affects payout magnitude is locked here, so a
// regression in accumulator.go fails loudly.
func TestSettleAccumulator_HalfLostMatrix_item_9(t *testing.T) {
	cases := []struct {
		name       string
		legs       []BetSelection
		stake      float64
		wantState  SelectionResult
		wantPayout float64
		wantOdds   float64
	}{
		// --- Single-leg cases ---
		// activeOdds = 2.00; payout = 100 * 2.00 = 200.00.
		{
			"single-won",
			[]BetSelection{{OddsValue: 2.00, Result: SelectionResultWon}},
			100, SelectionResultWon, 200.00, 2.00,
		},
		// activeOdds = (2-1)/2 + 1 = 1.50; payout = 150.00.
		{
			"single-half-won",
			[]BetSelection{{OddsValue: 2.00, Result: SelectionResultHalfWon}},
			100, SelectionResultWon, 150.00, 1.50,
		},
		// activeOdds = 0.5; payout = 50.00 (half stake refunded).
		{
			"single-half-lost",
			[]BetSelection{{OddsValue: 2.00, Result: SelectionResultHalfLost}},
			100, SelectionResultWon, 50.00, 0.50,
		},
		// Any fully-lost leg busts: state=Lost, payout=0, odds=0.
		{
			"single-lost",
			[]BetSelection{{OddsValue: 2.00, Result: SelectionResultLost}},
			100, SelectionResultLost, 0.00, 0.00,
		},
		// All-void path: stake returned at unit odds.
		{
			"single-void",
			[]BetSelection{{OddsValue: 2.00, Result: SelectionResultVoid}},
			100, SelectionResultVoid, 100.00, 1.00,
		},

		// --- 2-leg permutations ---
		// 2.00 * 3.00 = 6.00; payout = 600.00.
		{
			"2leg-won-won",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultWon},
				{OddsValue: 3.00, Result: SelectionResultWon},
			},
			100, SelectionResultWon, 600.00, 6.00,
		},
		// 2.00 * 0.5 = 1.00; payout = 100.00 (stake recovered).
		{
			"2leg-won-halflost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultWon},
				{OddsValue: 3.00, Result: SelectionResultHalfLost},
			},
			100, SelectionResultWon, 100.00, 1.00,
		},
		// half-won factor for 2.00 = 1.5; half-lost factor = 0.5.
		// activeOdds = 1.5 * 0.5 = 0.75; payout = 75.00.
		{
			"2leg-halfwon-halflost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultHalfWon},
				{OddsValue: 3.00, Result: SelectionResultHalfLost},
			},
			100, SelectionResultWon, 75.00, 0.75,
		},
		// Any lost leg busts entire accumulator.
		{
			"2leg-won-lost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultWon},
				{OddsValue: 3.00, Result: SelectionResultLost},
			},
			100, SelectionResultLost, 0.00, 0.00,
		},
		// 0.5 * 0.5 = 0.25; payout = 25.00.
		{
			"2leg-halflost-halflost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultHalfLost},
				{OddsValue: 3.00, Result: SelectionResultHalfLost},
			},
			100, SelectionResultWon, 25.00, 0.25,
		},

		// --- 3-leg permutation ---
		// won(2.00)=2.00, halfwon(2.00)=1.50, halflost(2.00)=0.5.
		// activeOdds = 2.00 * 1.50 * 0.5 = 1.50; payout = 150.00.
		{
			"3leg-won-halfwon-halflost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultWon},
				{OddsValue: 2.00, Result: SelectionResultHalfWon},
				{OddsValue: 2.00, Result: SelectionResultHalfLost},
			},
			100, SelectionResultWon, 150.00, 1.50,
		},

		// --- Edge: half-lost + void (no win) ---
		// half-lost(2.00)=0.5, void=1.0. activeOdds = 0.5; payout = 50.00.
		// voidCount=1 < len(legs)=2 so state stays Won (partial payout path).
		{
			"halflost-void",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultHalfLost},
				{OddsValue: 3.00, Result: SelectionResultVoid},
			},
			100, SelectionResultWon, 50.00, 0.50,
		},

		// --- All-void: stake returned. ---
		{
			"all-void",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultVoid},
				{OddsValue: 3.00, Result: SelectionResultVoid},
			},
			100, SelectionResultVoid, 100.00, 1.00,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			state, payout, odds := SettleAccumulator(c.legs, c.stake)
			if state != c.wantState {
				t.Errorf("state: got %v, want %v", state, c.wantState)
			}
			if payout != c.wantPayout {
				t.Errorf("payout: got %.2f, want %.2f", payout, c.wantPayout)
			}
			if odds != c.wantOdds {
				t.Errorf("odds: got %.2f, want %.2f", odds, c.wantOdds)
			}
		})
	}
}

// TestSettleAccumulator_PendingLegBlocksSettlement_item_9 pins the
// short-circuit: if any leg is still Pending the accumulator MUST NOT
// settle, regardless of the other legs' results. Any other behavior
// would prematurely credit (or debit) a player on a half-settled bet.
func TestSettleAccumulator_PendingLegBlocksSettlement_item_9(t *testing.T) {
	cases := []struct {
		name string
		legs []BetSelection
	}{
		{
			"pending-with-won",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultWon},
				{OddsValue: 3.00, Result: SelectionResultPending},
			},
		},
		{
			"pending-with-halfwon",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultHalfWon},
				{OddsValue: 3.00, Result: SelectionResultPending},
			},
		},
		{
			"pending-with-halflost",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultHalfLost},
				{OddsValue: 3.00, Result: SelectionResultPending},
			},
		},
		{
			"pending-with-void",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultVoid},
				{OddsValue: 3.00, Result: SelectionResultPending},
			},
		},
		{
			"pending-only",
			[]BetSelection{
				{OddsValue: 2.00, Result: SelectionResultPending},
			},
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			state, payout, odds := SettleAccumulator(c.legs, 100.00)
			if state != SelectionResultPending {
				t.Errorf("state: got %v, want pending", state)
			}
			if payout != 0 {
				t.Errorf("payout: got %.2f, want 0.00 (pending blocks settlement)", payout)
			}
			if odds != 0 {
				t.Errorf("odds: got %.2f, want 0.00 (pending blocks settlement)", odds)
			}
		})
	}
}
