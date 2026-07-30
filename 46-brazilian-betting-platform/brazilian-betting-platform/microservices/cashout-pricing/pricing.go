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
	"log/slog"
	"math"
)

// CashoutPricingEngine computes real-time cashout values for open bets.
//
// Formula:
//
//	cashout_value = stake * (current_implied_prob / original_implied_prob) * (1 - operator_margin)
//
// Where implied_prob = 1 / decimal_odds for each selection, and for accumulators
// the combined implied probability is the product of individual implied probs.
type CashoutPricingEngine struct {
	config PricingConfig
	logger *slog.Logger
}

// NewCashoutPricingEngine constructs a pricing engine with the given config.
func NewCashoutPricingEngine(cfg PricingConfig, logger *slog.Logger) *CashoutPricingEngine {
	return &CashoutPricingEngine{
		config: cfg,
		logger: logger,
	}
}

// PricingResult holds the output of a cashout price calculation.
type PricingResult struct {
	CashoutValue     float64 // Amount to offer the player
	OriginalImplied  float64 // Combined implied prob at placement
	CurrentImplied   float64 // Combined implied prob now
	MarginApplied    float64 // Operator margin used
	Eligible         bool    // Whether the bet is eligible for cashout
	IneligibleReason string  // Reason if not eligible
}

// CalculateCashoutValue computes the cashout offer for a bet snapshot.
// The stake parameter allows partial cashout (pass bet.Stake for full).
func (e *CashoutPricingEngine) CalculateCashoutValue(bet BetSnapshot, stake float64) (PricingResult, error) {
	result := PricingResult{
		MarginApplied: e.config.DefaultMargin,
	}

	// Validate bet state.
	if bet.Status != "accepted" && bet.Status != "pending" {
		result.IneligibleReason = fmt.Sprintf("bet status %q not eligible for cashout", bet.Status)
		return result, nil
	}

	if len(bet.Selections) == 0 {
		result.IneligibleReason = "bet has no selections"
		return result, nil
	}

	if stake <= 0 || stake > bet.RemainingStake {
		return result, fmt.Errorf("invalid cashout stake %.2f: must be > 0 and <= remaining stake %.2f", stake, bet.RemainingStake)
	}

	// Check all selections are in a cashout-eligible state.
	for _, sel := range bet.Selections {
		if sel.EventState == "settled" || sel.EventState == "voided" || sel.EventState == "cancelled" {
			result.IneligibleReason = fmt.Sprintf("selection %s in event %s has state %q", sel.SelectionID, sel.EventID, sel.EventState)
			return result, nil
		}
		if sel.EventState == "suspended" && !e.config.SuspendedAllowed {
			result.IneligibleReason = fmt.Sprintf("selection %s in event %s is suspended", sel.SelectionID, sel.EventID)
			return result, nil
		}
		if !sel.MarketOpen && !e.config.SuspendedAllowed {
			result.IneligibleReason = fmt.Sprintf("market %s for selection %s is closed", sel.MarketID, sel.SelectionID)
			return result, nil
		}
		if sel.OddsAtPlace <= 0 || sel.CurrentOdds <= 0 {
			return result, fmt.Errorf("invalid odds for selection %s: placed=%.4f current=%.4f", sel.SelectionID, sel.OddsAtPlace, sel.CurrentOdds)
		}
	}

	// Compute combined implied probabilities.
	originalImplied := combinedImpliedProbability(bet.Selections, true)
	currentImplied := combinedImpliedProbability(bet.Selections, false)

	if originalImplied <= 0 {
		return result, fmt.Errorf("original implied probability is zero or negative: %.6f", originalImplied)
	}

	result.OriginalImplied = originalImplied
	result.CurrentImplied = currentImplied

	// Core formula: cashout_value = stake * (current_implied / original_implied) * (1 - margin)
	rawValue := stake * (currentImplied / originalImplied) * (1.0 - e.config.DefaultMargin)

	// Round to 2 decimal places (BRL centavos).
	cashoutValue := math.Round(rawValue*100) / 100

	// Cap against the bet's potential return: a cashout offer must never exceed
	// what the player could possibly win if the bet ran to completion. The
	// formula above can exceed PotentialReturn when the bet is heavily favored
	// (current implied prob much larger than original implied prob).
	if bet.PotentialReturn > 0 && cashoutValue > bet.PotentialReturn {
		cashoutValue = bet.PotentialReturn
	}

	// Apply minimum threshold.
	if cashoutValue < e.config.MinCashoutBRL {
		result.IneligibleReason = fmt.Sprintf("calculated cashout R$%.2f below minimum R$%.2f", cashoutValue, e.config.MinCashoutBRL)
		return result, nil
	}

	// Apply maximum threshold if configured.
	if e.config.MaxCashoutBRL > 0 && cashoutValue > e.config.MaxCashoutBRL {
		cashoutValue = e.config.MaxCashoutBRL
	}

	result.CashoutValue = cashoutValue
	result.Eligible = true

	e.logger.Debug("cashout price calculated",
		"bet_id", bet.ID,
		"stake", stake,
		"original_implied", originalImplied,
		"current_implied", currentImplied,
		"margin", e.config.DefaultMargin,
		"cashout_value", cashoutValue,
	)

	return result, nil
}

// combinedImpliedProbability computes the product of individual implied
// probabilities across all selections. For a single bet, this is just
// 1/odds. For an accumulator, it's the product of all 1/odds.
// If useOriginal is true, uses the odds at placement; otherwise current odds.
func combinedImpliedProbability(selections []SelectionSnapshot, useOriginal bool) float64 {
	combined := 1.0
	for _, sel := range selections {
		odds := sel.CurrentOdds
		if useOriginal {
			odds = sel.OddsAtPlace
		}
		if odds <= 0 {
			return 0
		}
		combined *= 1.0 / odds
	}
	return combined
}

// CheckEligibility determines whether a bet is eligible for cashout
// without computing a price.
func (e *CashoutPricingEngine) CheckEligibility(bet BetSnapshot) CashoutEligibility {
	if bet.Status != "accepted" && bet.Status != "pending" {
		return CashoutEligibility{
			BetID:    bet.ID,
			Eligible: false,
			Reason:   fmt.Sprintf("bet status %q not eligible", bet.Status),
		}
	}

	if bet.RemainingStake <= 0 {
		return CashoutEligibility{
			BetID:    bet.ID,
			Eligible: false,
			Reason:   "no remaining stake on bet",
		}
	}

	for _, sel := range bet.Selections {
		if sel.EventState == "settled" || sel.EventState == "voided" || sel.EventState == "cancelled" {
			return CashoutEligibility{
				BetID:    bet.ID,
				Eligible: false,
				Reason:   fmt.Sprintf("selection %s has terminal state %q", sel.SelectionID, sel.EventState),
			}
		}
		if sel.EventState == "suspended" && !e.config.SuspendedAllowed {
			return CashoutEligibility{
				BetID:    bet.ID,
				Eligible: false,
				Reason:   fmt.Sprintf("selection %s is suspended", sel.SelectionID),
			}
		}
	}

	return CashoutEligibility{
		BetID:    bet.ID,
		Eligible: true,
	}
}
