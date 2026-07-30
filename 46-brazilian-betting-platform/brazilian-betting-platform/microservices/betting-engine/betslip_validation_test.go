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
	"context"
	"testing"
)

// noopLogger and nopWriter are defined in betting_test.go.

func TestValidateBetslip_ValidSingle(t *testing.T) {
	// Use nil store to test non-DB validation paths.
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if !result.Valid {
		t.Errorf("expected valid result, got errors: %v", result.Errors)
	}
}

func TestValidateBetslip_MissingCPF(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for missing CPF")
	}
}

func TestValidateBetslip_SingleWithMultipleSelections(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
			{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 1.80},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for single bet with 2 selections")
	}
}

func TestValidateBetslip_StakeBelowMinimum(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     0.50,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for stake below minimum")
	}
}

func TestValidateBetslip_StakeAboveMaximum(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     10000.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for stake above maximum")
	}
}

func TestValidateBetslip_DuplicateSelection(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeMultiple,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
			{EventID: "ev2", MarketID: "m2", SelectionID: "s1", OddsValue: 1.80},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for duplicate selection")
	}
}

func TestValidateBetslip_SameMarketConflict(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeMultiple,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.50},
			{EventID: "ev1", MarketID: "m1", SelectionID: "s2", OddsValue: 3.00},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for conflicting selections from same market")
	}
}

func TestValidateBetslip_LowOdds(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     50.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 0.80},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result for odds below 1.01")
	}
}

func TestValidateBetslip_MaxReturnExceeded(t *testing.T) {
	limits := DefaultStakeLimits()
	limits.MaxPotentialReturn = 1000.00
	validator := &BetslipValidator{
		limits: limits,
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeSingle,
		Stake:     500.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 5.00},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if result.Valid {
		t.Error("expected invalid result when potential return exceeds max")
	}
}

func TestValidateBetslip_ValidAccumulator(t *testing.T) {
	validator := &BetslipValidator{
		limits: DefaultStakeLimits(),
		rules:  DefaultCompatibilityRules(),
		logger: noopLogger(),
	}

	req := &PlaceBetRequest{
		CPF:       "12345678901",
		SessionID: "sess-001",
		Type:      BetTypeMultiple,
		Stake:     100.00,
		Selections: []SelectionReq{
			{EventID: "ev1", MarketID: "m1", SelectionID: "s1", OddsValue: 2.00},
			{EventID: "ev2", MarketID: "m2", SelectionID: "s2", OddsValue: 1.80},
			{EventID: "ev3", MarketID: "m3", SelectionID: "s3", OddsValue: 2.20},
		},
	}

	result := validator.ValidateBetslip(context.Background(), req)
	if !result.Valid {
		t.Errorf("expected valid accumulator, got errors: %v", result.Errors)
	}
}

func TestDefaultStakeLimits(t *testing.T) {
	limits := DefaultStakeLimits()
	if limits.MinStakeSingle != 1.00 {
		t.Errorf("min single stake = %.2f, want 1.00", limits.MinStakeSingle)
	}
	if limits.MaxAccumulatorLegs != 20 {
		t.Errorf("max accumulator legs = %d, want 20", limits.MaxAccumulatorLegs)
	}
	if limits.MinAccumulatorLegs != 2 {
		t.Errorf("min accumulator legs = %d, want 2", limits.MinAccumulatorLegs)
	}
}

func TestDefaultCompatibilityRules(t *testing.T) {
	rules := DefaultCompatibilityRules()
	if len(rules) == 0 {
		t.Error("expected non-empty compatibility rules")
	}
	for _, rule := range rules {
		if rule.MarketA == "" || rule.MarketB == "" {
			t.Error("rule has empty market type")
		}
	}
}
