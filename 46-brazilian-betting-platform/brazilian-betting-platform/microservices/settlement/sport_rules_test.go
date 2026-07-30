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

func TestSettleFootball1X2_HomeWin(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "home", OddsValue: 2.10},
		{SelectionID: "draw", OddsValue: 3.20},
		{SelectionID: "away", OddsValue: 3.50},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 1,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["home"] != "won" {
		t.Errorf("home = %s, want won", outcomes["home"])
	}
	if outcomes["draw"] != "lost" {
		t.Errorf("draw = %s, want lost", outcomes["draw"])
	}
	if outcomes["away"] != "lost" {
		t.Errorf("away = %s, want lost", outcomes["away"])
	}
}

func TestSettleFootball1X2_Draw(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "home", OddsValue: 2.10},
		{SelectionID: "draw", OddsValue: 3.20},
		{SelectionID: "away", OddsValue: 3.50},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 1,
		AwayScore: 1,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["home"] != "lost" {
		t.Errorf("home = %s, want lost", outcomes["home"])
	}
	if outcomes["draw"] != "won" {
		t.Errorf("draw = %s, want won", outcomes["draw"])
	}
	if outcomes["away"] != "lost" {
		t.Errorf("away = %s, want lost", outcomes["away"])
	}
}

func TestSettleFootball1X2_AwayWin(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "home", OddsValue: 2.10},
		{SelectionID: "draw", OddsValue: 3.20},
		{SelectionID: "away", OddsValue: 3.50},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 0,
		AwayScore: 3,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["home"] != "lost" {
		t.Errorf("home = %s, want lost", outcomes["home"])
	}
	if outcomes["away"] != "won" {
		t.Errorf("away = %s, want won", outcomes["away"])
	}
}

func TestSettleFootball1X2_SingleLegSlip_GradesByIdentityNotIndex(t *testing.T) {
	// A real bet slip only carries the one leg the player picked, not the
	// market's full canonical [home, draw, away] ordering. Index-based
	// grading would treat this "draw" selection as index 0 ("home") and
	// wrongly grade it won; identity-based grading must grade it lost.
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "draw", OddsValue: 3.20},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 1, // home win, not draw
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["draw"] != "lost" {
		t.Errorf("draw = %s, want lost (home won, not draw)", outcomes["draw"])
	}
}

func TestSettleFootballDoubleChance_SingleLegSlip_GradesByIdentityNotIndex(t *testing.T) {
	// Same slip-carries-one-leg scenario for double chance: "x2" (away or
	// draw) at index 0 must not be graded as "1x" (home or draw).
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "x2", OddsValue: 1.40},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 0, // home win: x2 (away-or-draw) should lose
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementDoubleChance, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["x2"] != "lost" {
		t.Errorf("x2 = %s, want lost (home win)", outcomes["x2"])
	}
}

func TestSettleFootballOverUnder_Over(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "mais-2.5", OddsValue: 1.85},
		{SelectionID: "menos-2.5", OddsValue: 1.95},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 1,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementOverUnder, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["mais-2.5"] != "won" {
		t.Errorf("over 2.5 = %s, want won (3 goals)", outcomes["mais-2.5"])
	}
	if outcomes["menos-2.5"] != "lost" {
		t.Errorf("under 2.5 = %s, want lost", outcomes["menos-2.5"])
	}
}

func TestSettleFootballOverUnder_Under(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "mais-2.5", OddsValue: 1.85},
		{SelectionID: "menos-2.5", OddsValue: 1.95},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 1,
		AwayScore: 0,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementOverUnder, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["mais-2.5"] != "lost" {
		t.Errorf("over 2.5 = %s, want lost (1 goal)", outcomes["mais-2.5"])
	}
	if outcomes["menos-2.5"] != "won" {
		t.Errorf("under 2.5 = %s, want won", outcomes["menos-2.5"])
	}
}

func TestSettleFootballBothScore_Yes(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "sim", OddsValue: 1.75},
		{SelectionID: "nao", OddsValue: 2.05},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 1,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementBothScore, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["sim"] != "won" {
		t.Errorf("sim = %s, want won", outcomes["sim"])
	}
	if outcomes["nao"] != "lost" {
		t.Errorf("nao = %s, want lost", outcomes["nao"])
	}
}

func TestSettleFootballBothScore_No(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "sim", OddsValue: 1.75},
		{SelectionID: "nao", OddsValue: 2.05},
	}
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 1,
		AwayScore: 0,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementBothScore, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["sim"] != "lost" {
		t.Errorf("sim = %s, want lost", outcomes["sim"])
	}
	if outcomes["nao"] != "won" {
		t.Errorf("nao = %s, want won", outcomes["nao"])
	}
}

func TestSettleFootballDoubleChance(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "1x", OddsValue: 1.25},
		{SelectionID: "12", OddsValue: 1.15},
		{SelectionID: "x2", OddsValue: 1.40},
	}

	// Home win: 1X wins, 12 wins, X2 loses.
	result := EventResult{
		Sport:     SportTypeFootball,
		HomeScore: 2,
		AwayScore: 0,
		Status:    "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementDoubleChance, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if outcomes["1x"] != "won" {
		t.Errorf("1x = %s, want won (home win)", outcomes["1x"])
	}
	if outcomes["12"] != "won" {
		t.Errorf("12 = %s, want won (home win)", outcomes["12"])
	}
	if outcomes["x2"] != "lost" {
		t.Errorf("x2 = %s, want lost (home win)", outcomes["x2"])
	}
}

func TestSettleFootballHalfTime(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "ht-home", OddsValue: 2.50},
		{SelectionID: "ht-draw", OddsValue: 2.20},
		{SelectionID: "ht-away", OddsValue: 3.80},
	}
	result := EventResult{
		Sport:        SportTypeFootball,
		HomeScore:    2,
		AwayScore:    1,
		HalfTimeHome: 0,
		HalfTimeAway: 0,
		Status:       "finished",
	}

	outcomes, err := engine.SettleMarket(MarketSettlementHalfTime, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Half-time was 0-0 = draw.
	if outcomes["ht-draw"] != "won" {
		t.Errorf("ht-draw = %s, want won (0-0 at half time)", outcomes["ht-draw"])
	}
}

func TestSettleCancelledEvent_AllVoid(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "home", OddsValue: 2.10},
		{SelectionID: "draw", OddsValue: 3.20},
		{SelectionID: "away", OddsValue: 3.50},
	}
	result := EventResult{
		Sport:  SportTypeFootball,
		Status: "cancelled",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for selID, outcome := range outcomes {
		if outcome != "void" {
			t.Errorf("selection %s = %s, want void (cancelled event)", selID, outcome)
		}
	}
}

func TestSettleAbandonedEvent_AllVoid(t *testing.T) {
	engine := NewSportSettlementEngine()
	selections := []BetSelectionRecord{
		{SelectionID: "home"},
		{SelectionID: "away"},
	}
	result := EventResult{
		Sport:  SportTypeFootball,
		Status: "abandoned",
	}

	outcomes, err := engine.SettleMarket(MarketSettlement1X2, selections, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for selID, outcome := range outcomes {
		if outcome != "void" {
			t.Errorf("selection %s = %s, want void (abandoned event)", selID, outcome)
		}
	}
}

func TestParseHandicap(t *testing.T) {
	tests := []struct {
		input string
		want  string
		found bool
	}{
		{"home+1.5", "+1.5", true},
		{"away-0.5", "-0.5", true},
		{"draw+2", "+2", true},
		{"noline", "", false},
	}
	for _, tc := range tests {
		got, ok := parseHandicap(tc.input)
		if ok != tc.found {
			t.Errorf("parseHandicap(%q) found = %v, want %v", tc.input, ok, tc.found)
		}
		if got != tc.want {
			t.Errorf("parseHandicap(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

func TestVoidReasonConstants(t *testing.T) {
	reasons := []VoidReason{
		VoidReasonEventCancelled,
		VoidReasonEventAbandoned,
		VoidReasonHandicapPush,
		VoidReasonResultDisputed,
		VoidReasonTraderOverride,
		VoidReasonRegulatory,
	}
	for _, r := range reasons {
		if string(r) == "" {
			t.Error("void reason constant is empty")
		}
	}
}
