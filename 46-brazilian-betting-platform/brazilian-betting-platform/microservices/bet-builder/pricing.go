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
	"fmt"
	"math"
)

// BuilderPricingEngine computes correlation-adjusted odds for bet builder
// selections. Unlike standard accumulators which use simple multiplication,
// the builder engine applies correlation factors to avoid overstating the
// combined probability of correlated outcomes.
//
// The pricing formula is:
//
//	raw_odds       = product of individual selection odds
//	composite_corr = product of pairwise correlation factors for all market pairs
//	adjusted_odds  = raw_odds * composite_corr
//
// This reduces the payout when selections are positively correlated (e.g.,
// "Home Win" + "Over 2.5 Goals" in football — a dominant home side is more
// likely to produce high-scoring outcomes).
type BuilderPricingEngine struct {
	graph     *CorrelationGraph
	catalogue *CompatibilityCatalogue
	config    BuilderConfig
}

// NewBuilderPricingEngine creates a pricing engine with the given dependencies.
func NewBuilderPricingEngine(graph *CorrelationGraph, catalogue *CompatibilityCatalogue, config BuilderConfig) *BuilderPricingEngine {
	return &BuilderPricingEngine{
		graph:     graph,
		catalogue: catalogue,
		config:    config,
	}
}

// Quote prices a bet builder combination and returns a full quote.
func (e *BuilderPricingEngine) Quote(eventID string, sport Sport, selections []BuilderSelection, stake float64) BuilderQuote {
	quote := BuilderQuote{
		EventID:        eventID,
		Selections:     selections,
		SelectionCount: len(selections),
		Valid:          true,
	}

	// Validate selection count.
	if len(selections) < e.config.MinSelections {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"minimum %d selections required, got %d", e.config.MinSelections, len(selections)))
	}
	if len(selections) > e.config.MaxSelections {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"maximum %d selections allowed, got %d", e.config.MaxSelections, len(selections)))
	}

	// Validate all selections belong to the same event.
	for _, sel := range selections {
		if sel.EventID != eventID {
			quote.Valid = false
			quote.Errors = append(quote.Errors, fmt.Sprintf(
				"selection %s belongs to event %s, expected %s; bet builder requires same-event selections",
				sel.SelectionID, sel.EventID, eventID))
		}
	}

	// Validate individual odds.
	for i, sel := range selections {
		if sel.OddsValue < e.config.MinOddsPerLeg {
			quote.Valid = false
			quote.Errors = append(quote.Errors, fmt.Sprintf(
				"selection %d: odds %.2f below minimum %.2f", i+1, sel.OddsValue, e.config.MinOddsPerLeg))
		}
	}

	// Compatibility check.
	if compatErrors := e.catalogue.ValidateSelections(sport, selections); len(compatErrors) > 0 {
		quote.Valid = false
		quote.Errors = append(quote.Errors, compatErrors...)
	}

	// Reject same-game combinations for sports without a loaded correlation
	// graph. Pricing them anyway would fall back to GetFactor's per-pair
	// default for every market pair, which is a reasonable safety net but
	// not a substitute for an actual correlation model.
	if len(selections) > 1 && !e.graph.HasSport(sport) {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"bet builder is not available for sport %q: no correlation model loaded", sport))
	}

	// Calculate raw multiplicative odds.
	rawOdds := 1.0
	for _, sel := range selections {
		rawOdds *= sel.OddsValue
	}
	quote.RawCombinedOdds = math.Round(rawOdds*100) / 100

	// Extract market types for correlation lookup.
	marketTypes := make([]MarketType, len(selections))
	for i, sel := range selections {
		marketTypes[i] = sel.MarketType
	}

	// Compute composite correlation factor.
	composite := e.graph.CompositeCorrelation(sport, marketTypes)
	quote.CorrelationFactor = math.Round(composite*1000) / 1000

	// Apply correlation adjustment to odds.
	adjustedOdds := rawOdds * composite
	// Floor at 1.01 to ensure the builder always returns more than stake.
	if adjustedOdds < 1.01 {
		adjustedOdds = 1.01
	}
	quote.AdjustedOdds = math.Round(adjustedOdds*100) / 100

	// Calculate potential return.
	quote.PotentialReturn = math.Round(stake*quote.AdjustedOdds*100) / 100

	// Check max return cap.
	if quote.PotentialReturn > e.config.MaxPotentialReturn {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"potential return R$%.2f exceeds maximum R$%.2f",
			quote.PotentialReturn, e.config.MaxPotentialReturn))
	}

	return quote
}

// QuoteFromOdds is a convenience method that prices from raw odds values
// and market types without requiring full BuilderSelection structs.
func (e *BuilderPricingEngine) QuoteFromOdds(sport Sport, marketTypes []MarketType, odds []float64) (adjustedOdds float64, correlationFactor float64) {
	if len(marketTypes) != len(odds) {
		return 0, 0
	}

	rawOdds := 1.0
	for _, o := range odds {
		rawOdds *= o
	}

	composite := e.graph.CompositeCorrelation(sport, marketTypes)
	adjusted := rawOdds * composite
	if adjusted < 1.01 {
		adjusted = 1.01
	}

	return math.Round(adjusted*100) / 100, math.Round(composite*1000) / 1000
}
