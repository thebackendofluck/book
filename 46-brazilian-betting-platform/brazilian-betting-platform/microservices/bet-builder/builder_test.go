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

func newTestBuilderEngine() *BuilderEngine {
	graph := DefaultFootballCorrelationGraph()
	catalogue := DefaultFootballCompatibility()
	config := DefaultBuilderConfig()
	pricing := NewBuilderPricingEngine(graph, catalogue, config)
	return NewBuilderEngine(pricing, catalogue, config)
}

func TestNewBetslip(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	if slip.EventID != "ev1" {
		t.Errorf("event_id = %s, want ev1", slip.EventID)
	}
	if slip.Sport != SportFootball {
		t.Errorf("sport = %s, want football", slip.Sport)
	}
	if len(slip.Selections) != 0 {
		t.Errorf("new betslip should have 0 selections, got %d", len(slip.Selections))
	}
	if slip.ID == "" {
		t.Error("betslip ID should not be empty")
	}
}

func TestAddSelection_Valid(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:       "ev1",
		Sport:         SportFootball,
		MarketID:      "m1",
		MarketType:    MarketMatch1X2,
		SelectionID:   "s1",
		SelectionName: "Home Win",
		OddsValue:     1.80,
	})
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(slip.Selections) != 1 {
		t.Errorf("selections = %d, want 1", len(slip.Selections))
	}
}

func TestAddSelection_WrongEvent(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:    "ev2",
		MarketType: MarketMatch1X2,
		OddsValue:  1.80,
	})
	if err == nil {
		t.Error("expected error for wrong event")
	}
}

func TestAddSelection_DuplicateMarketType(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s1",
		OddsValue:   1.80,
	})

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s2",
		OddsValue:   3.50,
	})
	if err == nil {
		t.Error("expected error for duplicate market type")
	}
}

func TestAddSelection_DuplicateSelectionID(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s1",
		OddsValue:   1.80,
	})

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketTotalGoals,
		SelectionID: "s1",
		OddsValue:   2.10,
	})
	if err == nil {
		t.Error("expected error for duplicate selection ID")
	}
}

func TestAddSelection_IncompatibleMarkets(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s1",
		OddsValue:   1.80,
	})

	// 1X2 + Double Chance is blocked.
	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketDoubleChance,
		SelectionID: "s2",
		OddsValue:   1.40,
	})
	if err == nil {
		t.Error("expected error for incompatible markets (1X2 + Double Chance)")
	}
}

func TestAddSelection_MaxSelectionsReached(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	// Fill to max (6).
	marketTypes := []MarketType{MarketMatch1X2, MarketTotalGoals, MarketBTTS, MarketFirstHalf1X2, MarketPlayerProps, MarketType("custom_1")}
	for i, mt := range marketTypes {
		engine.AddSelection(slip, AddSelectionRequest{
			EventID:     "ev1",
			MarketType:  mt,
			SelectionID: "s" + string(rune('a'+i)),
			OddsValue:   1.50,
		})
	}

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketType("custom_2"),
		SelectionID: "s_extra",
		OddsValue:   1.50,
	})
	if err == nil {
		t.Error("expected error when max selections reached")
	}
}

func TestAddSelection_LowOdds(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	err := engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s1",
		OddsValue:   1.01, // Below 1.05 minimum
	})
	if err == nil {
		t.Error("expected error for odds below minimum")
	}
}

func TestRemoveSelection_Valid(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketMatch1X2,
		SelectionID: "s1",
		OddsValue:   1.80,
	})
	engine.AddSelection(slip, AddSelectionRequest{
		EventID:     "ev1",
		MarketType:  MarketTotalGoals,
		SelectionID: "s2",
		OddsValue:   2.10,
	})

	err := engine.RemoveSelection(slip, "s1")
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(slip.Selections) != 1 {
		t.Errorf("selections = %d, want 1", len(slip.Selections))
	}
	if slip.Selections[0].SelectionID != "s2" {
		t.Errorf("remaining selection = %s, want s2", slip.Selections[0].SelectionID)
	}
}

func TestRemoveSelection_NotFound(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	err := engine.RemoveSelection(slip, "nonexistent")
	if err == nil {
		t.Error("expected error for non-existent selection")
	}
}

func TestRemoveSelection_ClearsQuote(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.80,
	})
	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.10,
	})

	// Get a quote first.
	engine.GetQuote(slip, 100.00)
	if slip.Quote == nil {
		t.Error("quote should be cached after GetQuote")
	}

	// Remove selection should clear quote.
	engine.RemoveSelection(slip, "s1")
	if slip.Quote != nil {
		t.Error("quote should be nil after removing a selection")
	}
}

func TestGetQuote_ReturnsValidQuote(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00,
	})
	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.50,
	})

	quote := engine.GetQuote(slip, 100.00)

	if !quote.Valid {
		t.Errorf("expected valid quote, got errors: %v", quote.Errors)
	}
	if quote.AdjustedOdds == 0 {
		t.Error("adjusted odds should not be zero")
	}
	if slip.Quote == nil {
		t.Error("quote should be cached on betslip")
	}
}

func TestValidateBetslip_TooFewSelections(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.80,
	})

	errors := engine.ValidateBetslip(slip)
	if len(errors) == 0 {
		t.Error("expected validation errors for single selection")
	}
}

func TestValidateBetslip_ValidTwoSelections(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.80,
	})
	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.10,
	})

	errors := engine.ValidateBetslip(slip)
	if len(errors) != 0 {
		t.Errorf("expected no validation errors, got: %v", errors)
	}
}

func TestApplyTemplate_Valid(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)
	template := FootballBuilderTemplates()[1] // Match Result + Over 2.5

	selections := []AddSelectionRequest{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", SelectionName: "Home", OddsValue: 1.80},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", SelectionName: "Over 2.5", OddsValue: 2.10},
	}

	err := engine.ApplyTemplate(slip, template, selections)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(slip.Selections) != 2 {
		t.Errorf("selections = %d, want 2", len(slip.Selections))
	}
}

func TestApplyTemplate_WrongMarketCount(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)
	template := FootballBuilderTemplates()[0] // 3-market template

	selections := []AddSelectionRequest{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.80},
	}

	err := engine.ApplyTemplate(slip, template, selections)
	if err == nil {
		t.Error("expected error for wrong number of selections")
	}
}

func TestApplyTemplate_WrongMarketType(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)
	template := FootballBuilderTemplates()[1] // Match Result + Over 2.5

	selections := []AddSelectionRequest{
		{EventID: "ev1", MarketType: MarketBTTS, SelectionID: "s1", OddsValue: 1.80},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.10},
	}

	err := engine.ApplyTemplate(slip, template, selections)
	if err == nil {
		t.Error("expected error for mismatched market type")
	}
}

func TestApplyTemplate_ClearsExistingSelections(t *testing.T) {
	engine := newTestBuilderEngine()
	slip := engine.NewBetslip("ev1", SportFootball)

	// Add a selection first.
	engine.AddSelection(slip, AddSelectionRequest{
		EventID: "ev1", MarketType: MarketBTTS, SelectionID: "s0", OddsValue: 1.90,
	})

	template := FootballBuilderTemplates()[1]
	selections := []AddSelectionRequest{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.80},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.10},
	}

	err := engine.ApplyTemplate(slip, template, selections)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	// Old selection should be gone.
	if len(slip.Selections) != 2 {
		t.Errorf("selections = %d, want 2 (template should replace existing)", len(slip.Selections))
	}
	for _, sel := range slip.Selections {
		if sel.SelectionID == "s0" {
			t.Error("old selection s0 should have been cleared by template")
		}
	}
}

func TestGetTemplateByID_Found(t *testing.T) {
	tmpl := GetTemplateByID("fb-home-over25-btts")
	if tmpl == nil {
		t.Error("expected to find template fb-home-over25-btts")
	}
	if tmpl.Name != "Home Win + Over 2.5 Goals + BTTS" {
		t.Errorf("template name = %s, unexpected", tmpl.Name)
	}
}

func TestGetTemplateByID_NotFound(t *testing.T) {
	tmpl := GetTemplateByID("nonexistent")
	if tmpl != nil {
		t.Error("expected nil for nonexistent template")
	}
}

func TestGetPopularTemplates_Football(t *testing.T) {
	popular := GetPopularTemplates(SportFootball)
	if len(popular) == 0 {
		t.Error("expected at least one popular football template")
	}
	for _, tmpl := range popular {
		if !tmpl.Popular {
			t.Errorf("template %s is not marked as popular", tmpl.ID)
		}
	}
}

func TestGetPopularTemplates_UnknownSport(t *testing.T) {
	popular := GetPopularTemplates(Sport("cricket"))
	if len(popular) != 0 {
		t.Errorf("expected no templates for unknown sport, got %d", len(popular))
	}
}

func TestGetAllTemplates_Football(t *testing.T) {
	all := GetAllTemplates(SportFootball)
	if len(all) < 5 {
		t.Errorf("expected at least 5 football templates, got %d", len(all))
	}
}
