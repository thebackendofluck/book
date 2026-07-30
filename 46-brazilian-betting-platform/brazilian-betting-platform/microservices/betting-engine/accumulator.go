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

// AccumulatorPricing calculates combined odds and validates accumulator bets.
type AccumulatorPricing struct {
	limits StakeLimits
}

// NewAccumulatorPricing creates a pricing engine with the given limits.
func NewAccumulatorPricing(limits StakeLimits) *AccumulatorPricing {
	return &AccumulatorPricing{limits: limits}
}

// AccumulatorQuote holds the calculated pricing for an accumulator bet.
type AccumulatorQuote struct {
	CombinedOdds    float64          `json:"combined_odds"`
	PotentialReturn float64          `json:"potential_return"`
	LegCount        int              `json:"leg_count"`
	Legs            []AccumulatorLeg `json:"legs"`
	Valid           bool             `json:"valid"`
	Errors          []string         `json:"errors,omitempty"`
}

// AccumulatorLeg represents a single leg within an accumulator.
type AccumulatorLeg struct {
	EventID     string  `json:"event_id"`
	MarketID    string  `json:"market_id"`
	SelectionID string  `json:"selection_id"`
	OddsValue   float64 `json:"odds_value"`
	LegIndex    int     `json:"leg_index"`
}

// PriceAccumulator calculates the combined odds and validates an
// accumulator from the given selections and stake.
func (ap *AccumulatorPricing) PriceAccumulator(selections []SelectionReq, stake float64) AccumulatorQuote {
	quote := AccumulatorQuote{
		LegCount: len(selections),
		Valid:    true,
	}

	// Validate leg count.
	if len(selections) < ap.limits.MinAccumulatorLegs {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"minimum %d legs required, got %d", ap.limits.MinAccumulatorLegs, len(selections)))
	}
	if len(selections) > ap.limits.MaxAccumulatorLegs {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"maximum %d legs allowed, got %d", ap.limits.MaxAccumulatorLegs, len(selections)))
	}

	// Calculate combined odds (multiplicative pricing).
	combinedOdds := 1.0
	for i, sel := range selections {
		if sel.OddsValue < 1.01 {
			quote.Valid = false
			quote.Errors = append(quote.Errors, fmt.Sprintf(
				"leg %d: odds %.2f below minimum 1.01", i+1, sel.OddsValue))
			continue
		}

		combinedOdds *= sel.OddsValue
		quote.Legs = append(quote.Legs, AccumulatorLeg{
			EventID:     sel.EventID,
			MarketID:    sel.MarketID,
			SelectionID: sel.SelectionID,
			OddsValue:   sel.OddsValue,
			LegIndex:    i + 1,
		})
	}

	// Round combined odds to 2 decimal places.
	quote.CombinedOdds = math.Round(combinedOdds*100) / 100
	quote.PotentialReturn = math.Round(stake*quote.CombinedOdds*100) / 100

	// Check max return cap.
	if quote.PotentialReturn > ap.limits.MaxPotentialReturn {
		quote.Valid = false
		quote.Errors = append(quote.Errors, fmt.Sprintf(
			"potential return R$%.2f exceeds maximum R$%.2f",
			quote.PotentialReturn, ap.limits.MaxPotentialReturn))
	}

	// Correlation check: no two legs from the same event.
	eventCount := make(map[string]int)
	for _, sel := range selections {
		eventCount[sel.EventID]++
	}
	for eventID, count := range eventCount {
		if count > 1 {
			quote.Valid = false
			quote.Errors = append(quote.Errors, fmt.Sprintf(
				"event %s appears in %d legs; same-event accumulator legs are not allowed in Phase 1",
				eventID, count))
		}
	}

	return quote
}

// SettleAccumulator determines the outcome of an accumulator given
// individual leg results, applying Asian-handicap quarter-line rules:
//
//   - Won           contributes `odds`        to the running activeOdds.
//   - Half-won      contributes (odds-1)/2+1  (the winning half pays full odds,
//     the other half is refunded at stake).
//   - Half-lost     contributes 0.5           (half the stake on this leg is
//     forfeited, half is refunded).
//   - Void          contributes 1.0           (leg removed from the product).
//   - Lost          busts the accumulator     (return Lost, payout 0).
//   - Pending       defers settlement         (return Pending, payout 0).
//
// Final payout = stake * activeOdds. activeOdds may drop below 1.0 when
// half-lost factors dominate; the player still recovers a partial payout
// (not zero) — that is the defining behavior of the half-lost branch and
// what distinguishes it from a fully-lost accumulator. The result label
// stays SelectionResultWon for any settled non-loss path; the payout
// amount is the load-bearing field.
func SettleAccumulator(legs []BetSelection, stake float64) (SelectionResult, float64, float64) {
	anyLost := false
	voidCount := 0
	activeOdds := 1.0

	for _, leg := range legs {
		switch leg.Result {
		case SelectionResultLost:
			anyLost = true
		case SelectionResultVoid:
			voidCount++
			// Void legs contribute 1.0 — effectively removed from the product.
		case SelectionResultWon:
			activeOdds *= leg.OddsValue
		case SelectionResultHalfWon:
			// Asian handicap half-win: (odds - 1) / 2 + 1
			activeOdds *= (leg.OddsValue-1)/2 + 1
		case SelectionResultHalfLost:
			// Asian handicap half-lost: effective decimal 0.5 within the
			// product. Drops activeOdds toward — but never to — zero.
			activeOdds *= 0.5
		default:
			// Pending or unknown — not yet settled.
			return SelectionResultPending, 0, 0
		}
	}

	if anyLost {
		// Any fully-lost leg busts the accumulator.
		return SelectionResultLost, 0, 0
	}
	if voidCount == len(legs) {
		// All legs void — stake returned at unit odds.
		return SelectionResultVoid, stake, activeOdds
	}

	// No fully-lost leg and at least one non-void leg. activeOdds is the
	// multiplicative product of every non-void contribution.
	adjustedReturn := math.Round(stake*activeOdds*100) / 100
	return SelectionResultWon, adjustedReturn, math.Round(activeOdds*100) / 100
}
