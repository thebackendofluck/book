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
	"math"
	"testing"
)

func TestPriceAccumulator_ValidTwoLegs(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 3.00},
	}
	quote := ap.PriceAccumulator(selections, 100.00)

	if !quote.Valid {
		t.Errorf("expected valid quote, got errors: %v", quote.Errors)
	}
	if quote.CombinedOdds != 6.00 {
		t.Errorf("combined odds = %.2f, want 6.00", quote.CombinedOdds)
	}
	if quote.PotentialReturn != 600.00 {
		t.Errorf("potential return = %.2f, want 600.00", quote.PotentialReturn)
	}
	if quote.LegCount != 2 {
		t.Errorf("leg count = %d, want 2", quote.LegCount)
	}
}

func TestPriceAccumulator_ThreeLegs(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 1.50},
		{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 2.00},
		{EventID: "ev3", MarketID: "m3", SelectionID: "s3", OddsValue: 2.50},
	}
	quote := ap.PriceAccumulator(selections, 50.00)

	if !quote.Valid {
		t.Errorf("expected valid quote, got errors: %v", quote.Errors)
	}
	expected := math.Round(1.50*2.00*2.50*100) / 100
	if quote.CombinedOdds != expected {
		t.Errorf("combined odds = %.2f, want %.2f", quote.CombinedOdds, expected)
	}
}

func TestPriceAccumulator_TooFewLegs(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.00},
	}
	quote := ap.PriceAccumulator(selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for single leg accumulator")
	}
}

func TestPriceAccumulator_TooManyLegs(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := make([]SelectionReq, 25)
	for i := range selections {
		selections[i] = SelectionReq{
			EventID:     "ev" + string(rune('A'+i)),
			MarketID:    "m" + string(rune('A'+i)),
			SelectionID: "s" + string(rune('A'+i)),
			OddsValue:   1.50,
		}
	}
	quote := ap.PriceAccumulator(selections, 10.00)

	if quote.Valid {
		t.Error("expected invalid quote for 25 legs")
	}
}

func TestPriceAccumulator_SameEventBlocked(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev1", MarketID: "m2", SelectionID: "s2", OddsValue: 3.00},
	}
	quote := ap.PriceAccumulator(selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for same-event legs")
	}
}

func TestPriceAccumulator_MaxReturnExceeded(t *testing.T) {
	limits := DefaultStakeLimits()
	limits.MaxPotentialReturn = 500.00
	ap := NewAccumulatorPricing(limits)

	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 10.00},
		{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 10.00},
	}
	quote := ap.PriceAccumulator(selections, 10.00)

	if quote.Valid {
		t.Error("expected invalid quote when return exceeds max")
	}
}

func TestPriceAccumulator_LowOddsBlocked(t *testing.T) {
	ap := NewAccumulatorPricing(DefaultStakeLimits())
	selections := []SelectionReq{
		{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 0.50},
		{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 2.00},
	}
	quote := ap.PriceAccumulator(selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for odds below 1.01")
	}
}

// --- SettleAccumulator tests ---

func TestSettleAccumulator_AllWon(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 3.00, Result: SelectionResultWon},
	}
	result, payout, odds := SettleAccumulator(legs, 100.00)

	if result != SelectionResultWon {
		t.Errorf("result = %s, want won", result)
	}
	if payout != 600.00 {
		t.Errorf("payout = %.2f, want 600.00", payout)
	}
	if odds != 6.00 {
		t.Errorf("odds = %.2f, want 6.00", odds)
	}
}

func TestSettleAccumulator_OneLost(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 3.00, Result: SelectionResultLost},
	}
	result, payout, _ := SettleAccumulator(legs, 100.00)

	if result != SelectionResultLost {
		t.Errorf("result = %s, want lost", result)
	}
	if payout != 0 {
		t.Errorf("payout = %.2f, want 0", payout)
	}
}

func TestSettleAccumulator_VoidLegRepricing(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 3.00, Result: SelectionResultVoid},
		{OddsValue: 1.50, Result: SelectionResultWon},
	}
	result, payout, adjustedOdds := SettleAccumulator(legs, 100.00)

	if result != SelectionResultWon {
		t.Errorf("result = %s, want won", result)
	}
	// Void leg treated as odds 1.0, so effective odds = 2.00 * 1.50 = 3.00
	if adjustedOdds != 3.00 {
		t.Errorf("adjusted odds = %.2f, want 3.00", adjustedOdds)
	}
	if payout != 300.00 {
		t.Errorf("payout = %.2f, want 300.00", payout)
	}
}

func TestSettleAccumulator_AllVoid(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultVoid},
		{OddsValue: 3.00, Result: SelectionResultVoid},
	}
	result, payout, _ := SettleAccumulator(legs, 100.00)

	if result != SelectionResultVoid {
		t.Errorf("result = %s, want void", result)
	}
	if payout != 100.00 {
		t.Errorf("payout = %.2f, want 100.00 (stake returned)", payout)
	}
}

func TestSettleAccumulator_Pending(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 3.00, Result: SelectionResultPending},
	}
	result, _, _ := SettleAccumulator(legs, 100.00)

	if result != SelectionResultPending {
		t.Errorf("result = %s, want pending", result)
	}
}

// TestSettleAccumulator_HalfWon documents the expected behavior of an
// accumulator with one half-won leg (Asian-handicap quarter-line winning
// half). Half-won contributes effective decimal odds of (odds-1)/2 + 1.
//
// Legs: 2.00 (won) and 2.00 (half-won) → activeOdds = 2.00 * 1.50 = 3.00.
// With stake 100, expected payout = 300.00.
func TestSettleAccumulator_HalfWon(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 2.00, Result: SelectionResultHalfWon},
	}
	result, payout, odds := SettleAccumulator(legs, 100.00)

	if result != SelectionResultWon {
		t.Errorf("result = %s, want won", result)
	}
	if odds != 3.00 {
		t.Errorf("adjusted odds = %.2f, want 3.00", odds)
	}
	if payout != 300.00 {
		t.Errorf("payout = %.2f, want 300.00", payout)
	}
}

// TestSettleAccumulator_HalfLost documents the behavior of an accumulator
// containing a half-lost leg (Asian-handicap quarter-line losing half).
//
// Legs 2.00 (won) and 2.00 (half-lost), activeOdds = 2.00 * 0.5 = 1.00 →
// payout = 100.00 (stake recovered). Result is SelectionResultWon — the
// player did not forfeit the stake, so it's not "Lost"; the load-bearing
// field is the payout, not the label.
func TestSettleAccumulator_HalfLost(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 2.00, Result: SelectionResultHalfLost},
	}
	result, payout, odds := SettleAccumulator(legs, 100.00)

	// Expected: a non-lost result with a partial payout based on adjusted odds.
	if result == SelectionResultLost {
		t.Errorf("result = lost, want a partial/won outcome for half-lost accumulator")
	}
	if odds != 1.00 {
		t.Errorf("adjusted odds = %.2f, want 1.00 (2.00 * 0.5)", odds)
	}
	if payout != 100.00 {
		t.Errorf("payout = %.2f, want 100.00 (stake returned)", payout)
	}
}

// TestSettleAccumulator_MixedHalfWonHalfLost documents the behavior when
// both half-won and half-lost legs are present.
//
// Legs: 2.00 (won), 2.00 (half-won, factor 1.5), 2.00 (half-lost, factor 0.5).
// Combined activeOdds = 2.00 * 1.5 * 0.5 = 1.50. Payout with stake 100 = 150.00.
// Result is SelectionResultWon.
func TestSettleAccumulator_MixedHalfWonHalfLost(t *testing.T) {
	legs := []BetSelection{
		{OddsValue: 2.00, Result: SelectionResultWon},
		{OddsValue: 2.00, Result: SelectionResultHalfWon},
		{OddsValue: 2.00, Result: SelectionResultHalfLost},
	}
	result, payout, odds := SettleAccumulator(legs, 100.00)

	if result == SelectionResultLost {
		t.Errorf("result = lost, want partial/won for mixed half-won + half-lost")
	}
	if odds != 1.50 {
		t.Errorf("adjusted odds = %.2f, want 1.50 (2.00 * 1.5 * 0.5)", odds)
	}
	if payout != 150.00 {
		t.Errorf("payout = %.2f, want 150.00", payout)
	}
}
