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
	"fmt"
	"log/slog"
	"math"
)

// PartialCashoutManager handles the logic for partial cashout operations
// where a player cashes out a portion of their bet while leaving the
// remainder active.
type PartialCashoutManager struct {
	engine *CashoutPricingEngine
	logger *slog.Logger
}

// NewPartialCashoutManager creates a partial cashout manager.
func NewPartialCashoutManager(engine *CashoutPricingEngine, logger *slog.Logger) *PartialCashoutManager {
	return &PartialCashoutManager{
		engine: engine,
		logger: logger,
	}
}

// PartialCashoutResult holds the computed split after a partial cashout.
type PartialCashoutResult struct {
	CashoutStake    float64 `json:"cashout_stake"`
	CashoutValue    float64 `json:"cashout_value"`
	RemainingStake  float64 `json:"remaining_stake"`
	RemainingReturn float64 `json:"remaining_potential_return"`
	OriginalStake   float64 `json:"original_stake"`
	OriginalReturn  float64 `json:"original_potential_return"`
	CashoutPercent  float64 `json:"cashout_percent"`
}

// CalculatePartialSplit computes the proportional stake split for a partial
// cashout. The cashout portion is priced using the engine; the remaining
// portion retains the original odds for potential settlement.
//
// The split is proportional:
//   - cashout_stake = remaining_stake * cashout_percent
//   - remaining_stake' = remaining_stake - cashout_stake
//   - remaining_potential_return = remaining_stake' * combined_odds
func (pm *PartialCashoutManager) CalculatePartialSplit(bet BetSnapshot, cashoutPercent float64) (*PartialCashoutResult, error) {
	cfg := pm.engine.config

	// Validate percentage bounds.
	if cashoutPercent <= 0 || cashoutPercent >= 1.0 {
		return nil, fmt.Errorf("partial cashout percent must be between 0 and 1 exclusive, got %.2f", cashoutPercent)
	}
	if cashoutPercent < cfg.MinPartialPct {
		return nil, fmt.Errorf("partial cashout %.0f%% below minimum %.0f%%", cashoutPercent*100, cfg.MinPartialPct*100)
	}
	if cashoutPercent > cfg.MaxPartialPct {
		return nil, fmt.Errorf("partial cashout %.0f%% above maximum %.0f%%", cashoutPercent*100, cfg.MaxPartialPct*100)
	}

	cashoutStake := math.Round(bet.RemainingStake*cashoutPercent*100) / 100
	remainingStake := math.Round((bet.RemainingStake-cashoutStake)*100) / 100

	// Ensure we don't go negative due to rounding.
	if remainingStake < 0 {
		remainingStake = 0
	}

	// Price the cashout portion.
	pricing, err := pm.engine.CalculateCashoutValue(bet, cashoutStake)
	if err != nil {
		return nil, fmt.Errorf("price partial cashout: %w", err)
	}
	if !pricing.Eligible {
		return nil, fmt.Errorf("bet not eligible for partial cashout: %s", pricing.IneligibleReason)
	}

	// Remaining potential return uses original combined odds.
	remainingReturn := math.Round(remainingStake*bet.CombinedOdds*100) / 100

	result := &PartialCashoutResult{
		CashoutStake:    cashoutStake,
		CashoutValue:    pricing.CashoutValue,
		RemainingStake:  remainingStake,
		RemainingReturn: remainingReturn,
		OriginalStake:   bet.Stake,
		OriginalReturn:  bet.PotentialReturn,
		CashoutPercent:  cashoutPercent,
	}

	pm.logger.Info("partial cashout split calculated",
		"bet_id", bet.ID,
		"cashout_pct", cashoutPercent,
		"cashout_stake", cashoutStake,
		"cashout_value", pricing.CashoutValue,
		"remaining_stake", remainingStake,
		"remaining_return", remainingReturn,
	)

	return result, nil
}

// ValidatePartialSequence checks whether an additional partial cashout is
// allowed on a bet that has already had previous partial cashouts.
// Rules:
//   - remaining stake after the new partial must be >= MinCashoutBRL (to be
//     eligible for a future full cashout or settlement)
//   - total partial cashouts must not exceed MaxPartialPct of original stake
func (pm *PartialCashoutManager) ValidatePartialSequence(ctx context.Context, bet BetSnapshot, store *QuoteStore, newPercent float64) error {
	newCashoutStake := bet.RemainingStake * newPercent
	futureRemaining := bet.RemainingStake - newCashoutStake

	cfg := pm.engine.config

	// Enforce the cumulative partial-cashout cap across the bet's full
	// history, not just this one request.
	if err := checkCumulativePartialCap(ctx, bet, store, newCashoutStake, cfg); err != nil {
		return err
	}

	// Remaining stake must be enough for minimum cashout or settlement.
	if futureRemaining > 0 && futureRemaining < cfg.MinCashoutBRL {
		return fmt.Errorf(
			"remaining stake R$%.2f after partial would be below minimum R$%.2f; use full cashout instead",
			futureRemaining, cfg.MinCashoutBRL,
		)
	}

	pm.logger.Debug("partial sequence validated",
		"bet_id", bet.ID,
		"remaining_after", futureRemaining,
	)

	return nil
}

// checkCumulativePartialCap enforces that the total value paid out via
// partial cashouts on a bet — everything already credited plus the cashout
// currently being requested — never exceeds MaxPartialPct of the bet's
// original stake. Without this, a sequence of partials that each
// individually satisfy the per-request Min/MaxPartialPct bounds could still
// drain far more of the stake than the cap intends.
func checkCumulativePartialCap(ctx context.Context, bet BetSnapshot, store *QuoteStore, newCashoutStake float64, cfg PricingConfig) error {
	entries, err := store.GetJournalEntriesForBet(ctx, bet.ID)
	if err != nil {
		return fmt.Errorf("get journal entries: %w", err)
	}

	var totalCashedOut float64
	for _, e := range entries {
		if e.Type == "partial_cashout_credit" || e.Type == "cashout_credit" {
			totalCashedOut += e.Amount
		}
	}

	maxAllowedStake := cfg.MaxPartialPct * bet.Stake
	totalAfter := totalCashedOut + newCashoutStake
	// Small epsilon to absorb floating-point rounding at the boundary.
	if totalAfter > maxAllowedStake+0.01 {
		return fmt.Errorf(
			"cumulative partial cashout (already R$%.2f + new R$%.2f = R$%.2f) would exceed cap of %.0f%% of original stake R$%.2f",
			totalCashedOut, newCashoutStake, totalAfter, cfg.MaxPartialPct*100, maxAllowedStake,
		)
	}
	return nil
}
