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

func newTestPricingEngine() *BuilderPricingEngine {
	graph := DefaultFootballCorrelationGraph()
	catalogue := DefaultFootballCompatibility()
	config := DefaultBuilderConfig()
	return NewBuilderPricingEngine(graph, catalogue, config)
}

func TestQuote_HomeWinOverBTTS(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", SelectionName: "Home Win", OddsValue: 1.80},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", SelectionName: "Over 2.5", OddsValue: 2.10},
		{EventID: "ev1", MarketType: MarketBTTS, SelectionID: "s3", SelectionName: "Yes", OddsValue: 1.90},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if !quote.Valid {
		t.Errorf("expected valid quote, got errors: %v", quote.Errors)
	}

	// Raw odds = 1.80 * 2.10 * 1.90 = 7.182
	expectedRaw := math.Round(1.80*2.10*1.90*100) / 100
	if quote.RawCombinedOdds != expectedRaw {
		t.Errorf("raw odds = %.2f, want %.2f", quote.RawCombinedOdds, expectedRaw)
	}

	// Adjusted odds should be less than raw (correlation reduces them).
	if quote.AdjustedOdds >= quote.RawCombinedOdds {
		t.Errorf("adjusted odds %.2f should be less than raw %.2f", quote.AdjustedOdds, quote.RawCombinedOdds)
	}

	// Correlation factor should be < 1.0 for these correlated markets.
	if quote.CorrelationFactor >= 1.0 {
		t.Errorf("correlation factor %.3f should be < 1.0", quote.CorrelationFactor)
	}

	// Potential return = adjusted odds * stake.
	expectedReturn := math.Round(100.00*quote.AdjustedOdds*100) / 100
	if quote.PotentialReturn != expectedReturn {
		t.Errorf("potential return = %.2f, want %.2f", quote.PotentialReturn, expectedReturn)
	}
}

func TestQuote_TwoMarkets_1X2PlusTotalGoals(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.50},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 50.00)

	if !quote.Valid {
		t.Errorf("expected valid quote, got errors: %v", quote.Errors)
	}

	// Raw odds = 5.00
	if quote.RawCombinedOdds != 5.00 {
		t.Errorf("raw odds = %.2f, want 5.00", quote.RawCombinedOdds)
	}

	// Correlation factor for 1X2 + Total Goals = 0.65
	if quote.CorrelationFactor != 0.65 {
		t.Errorf("correlation factor = %.3f, want 0.650", quote.CorrelationFactor)
	}

	// Adjusted odds = 5.00 * 0.65 = 3.25
	if quote.AdjustedOdds != 3.25 {
		t.Errorf("adjusted odds = %.2f, want 3.25", quote.AdjustedOdds)
	}

	// Return = 50 * 3.25 = 162.50
	if quote.PotentialReturn != 162.50 {
		t.Errorf("potential return = %.2f, want 162.50", quote.PotentialReturn)
	}
}

func TestQuote_UnloadedSport_Rejected(t *testing.T) {
	graph := NewCorrelationGraph()
	// No correlation graph loaded for any sport.
	catalogue := NewCompatibilityCatalogue()
	config := DefaultBuilderConfig()
	engine := NewBuilderPricingEngine(graph, catalogue, config)

	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev1", MarketType: MarketPlayerProps, SelectionID: "s2", OddsValue: 3.00},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	// No correlation graph loaded for football: the builder must refuse
	// the same-game combination rather than price the legs as independent.
	if quote.Valid {
		t.Error("expected invalid quote for a sport with no correlation graph loaded")
	}
	if quote.CorrelationFactor != unknownPairFactor {
		t.Errorf("correlation factor = %.3f, want %.3f (conservative fallback)", quote.CorrelationFactor, unknownPairFactor)
	}
}

func TestQuote_LoadedSport_UnmodeledPair_ConservativeFactor(t *testing.T) {
	engine := newTestPricingEngine()

	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev1", MarketType: MarketPlayerProps, SelectionID: "s2", OddsValue: 3.00},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	// Football's graph is loaded, so the sport-level gate does not fire,
	// but there is no edge between 1X2 and Player Props: GetFactor must
	// still fail closed to the conservative default instead of 1.0.
	if quote.CorrelationFactor != unknownPairFactor {
		t.Errorf("correlation factor = %.3f, want %.3f", quote.CorrelationFactor, unknownPairFactor)
	}
	if quote.AdjustedOdds == quote.RawCombinedOdds {
		t.Errorf("adjusted odds %.2f should be reduced from raw %.2f for an unmodeled pair", quote.AdjustedOdds, quote.RawCombinedOdds)
	}
}

func TestQuote_IncompatibleMarkets_Invalid(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev1", MarketType: MarketDoubleChance, SelectionID: "s2", OddsValue: 1.40},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for 1X2 + Double Chance combination")
	}
}

func TestQuote_CorrectScorePlus1X2_Blocked(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketCorrectScore, SelectionID: "s1", OddsValue: 8.00},
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s2", OddsValue: 2.00},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 50.00)

	if quote.Valid {
		t.Error("expected invalid quote for CorrectScore + 1X2")
	}
}

func TestQuote_TooFewSelections(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for single selection")
	}
}

func TestQuote_TooManySelections(t *testing.T) {
	engine := newTestPricingEngine()
	selections := make([]BuilderSelection, 10)
	for i := range selections {
		selections[i] = BuilderSelection{
			EventID:    "ev1",
			MarketType: MarketType("market_" + string(rune('a'+i))),
			OddsValue:  1.50,
		}
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for 10 selections (max 6)")
	}
}

func TestQuote_DifferentEvents_Invalid(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 2.00},
		{EventID: "ev2", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.50},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for different-event selections")
	}
}

func TestQuote_LowOdds_Invalid(t *testing.T) {
	engine := newTestPricingEngine()
	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.01},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 2.50},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for odds below minimum 1.05")
	}
}

func TestQuote_MaxReturnExceeded(t *testing.T) {
	graph := DefaultFootballCorrelationGraph()
	catalogue := DefaultFootballCompatibility()
	config := BuilderConfig{
		MinSelections:      2,
		MaxSelections:      6,
		MinOddsPerLeg:      1.05,
		MaxPotentialReturn: 100.00, // Very low cap for testing.
	}
	engine := NewBuilderPricingEngine(graph, catalogue, config)

	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 5.00},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 5.00},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	if quote.Valid {
		t.Error("expected invalid quote for return exceeding max")
	}
}

func TestQuoteFromOdds_Convenience(t *testing.T) {
	engine := newTestPricingEngine()
	markets := []MarketType{MarketMatch1X2, MarketTotalGoals}
	odds := []float64{2.00, 2.50}

	adjusted, factor := engine.QuoteFromOdds(SportFootball, markets, odds)

	if factor != 0.65 {
		t.Errorf("factor = %.3f, want 0.650", factor)
	}
	expected := math.Round(2.00*2.50*0.65*100) / 100
	if adjusted != expected {
		t.Errorf("adjusted odds = %.2f, want %.2f", adjusted, expected)
	}
}

func TestQuoteFromOdds_MismatchedLengths(t *testing.T) {
	engine := newTestPricingEngine()
	markets := []MarketType{MarketMatch1X2}
	odds := []float64{2.00, 2.50}

	adjusted, factor := engine.QuoteFromOdds(SportFootball, markets, odds)

	if adjusted != 0 || factor != 0 {
		t.Errorf("expected zero return for mismatched lengths, got %.2f, %.3f", adjusted, factor)
	}
}

func TestQuote_AdjustedOddsFloorAt101(t *testing.T) {
	// Create a scenario where correlation would push odds below 1.01.
	graph := NewCorrelationGraph()
	graph.AddEdge(SportFootball, MarketMatch1X2, MarketTotalGoals, 0.10, true)

	catalogue := NewCompatibilityCatalogue()
	config := DefaultBuilderConfig()
	engine := NewBuilderPricingEngine(graph, catalogue, config)

	selections := []BuilderSelection{
		{EventID: "ev1", MarketType: MarketMatch1X2, SelectionID: "s1", OddsValue: 1.10},
		{EventID: "ev1", MarketType: MarketTotalGoals, SelectionID: "s2", OddsValue: 1.10},
	}

	quote := engine.Quote("ev1", SportFootball, selections, 100.00)

	// 1.10 * 1.10 * 0.10 = 0.121 which is < 1.01, should be clamped.
	if quote.AdjustedOdds < 1.01 {
		t.Errorf("adjusted odds = %.2f, should not go below 1.01", quote.AdjustedOdds)
	}
}
