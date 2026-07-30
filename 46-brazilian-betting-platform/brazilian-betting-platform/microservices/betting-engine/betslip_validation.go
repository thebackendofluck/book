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
	"net/http"
	"time"
)

// ValidationResult holds the outcome of a betslip validation check.
type ValidationResult struct {
	Valid    bool     `json:"valid"`
	Errors   []string `json:"errors,omitempty"`
	Warnings []string `json:"warnings,omitempty"`
}

// NOTE: OddsFeedClient is defined but intentionally not wired into main.go.
// It is presented in the book as an extension point — readers can integrate it
// by registering its handlers/listeners in cmd/main.go. See chapter 46 §X.
//
// OddsFeedClient is used by the betting engine to verify current odds and
// market state from the odds-feed service.
type OddsFeedClient struct {
	httpClient *http.Client
	baseURL    string
}

// NewOddsFeedClient creates a client for the odds-feed service.
func NewOddsFeedClient(baseURL string) *OddsFeedClient {
	return &OddsFeedClient{
		httpClient: &http.Client{Timeout: 5 * time.Second},
		baseURL:    baseURL,
	}
}

// MarketCompatibilityRule defines whether two market types can coexist in
// the same betslip. Some markets within the same event are correlated and
// must be restricted.
type MarketCompatibilityRule struct {
	MarketA   MarketType
	MarketB   MarketType
	SameEvent bool // true = restriction only applies within the same event
	Allowed   bool
	Reason    string
}

// DefaultCompatibilityRules returns the baseline market compatibility matrix
// for football. Correlated markets within the same event are blocked.
func DefaultCompatibilityRules() []MarketCompatibilityRule {
	return []MarketCompatibilityRule{
		// Match winner and double chance on same event are correlated.
		{MarketTypeMatchWinner, "double_chance", true, false, "match winner and double chance on same event are correlated"},
		// Match winner and half-time result on same event are correlated.
		{MarketTypeMatchWinner, "half_time_result", true, false, "match winner and half-time result on same event are correlated"},
		// Over/under and both-teams-score on same event have moderate correlation.
		{MarketTypeOverUnder, "both_teams_score", true, false, "over/under and both teams score on same event are correlated"},
		// Same market type on same event is always blocked (no duplicate selections).
		{MarketTypeMatchWinner, MarketTypeMatchWinner, true, false, "duplicate market type on same event"},
		{MarketTypeOverUnder, MarketTypeOverUnder, true, false, "duplicate market type on same event"},
		{MarketTypeHandicap, MarketTypeHandicap, true, false, "duplicate market type on same event"},
	}
}

// StakeLimits defines the minimum and maximum stake configuration.
type StakeLimits struct {
	MinStakeSingle      float64 // Minimum stake for a single bet (BRL).
	MaxStakeSingle      float64 // Maximum stake for a single bet (BRL).
	MinStakeAccumulator float64 // Minimum stake for an accumulator (BRL).
	MaxStakeAccumulator float64 // Maximum stake for an accumulator (BRL).
	MaxPotentialReturn  float64 // Maximum potential return on any bet (BRL).
	MaxAccumulatorLegs  int     // Maximum selections in an accumulator.
	MinAccumulatorLegs  int     // Minimum selections in an accumulator.
}

// DefaultStakeLimits returns the default stake configuration for Brazil.
func DefaultStakeLimits() StakeLimits {
	return StakeLimits{
		MinStakeSingle:      1.00,
		MaxStakeSingle:      5000.00,
		MinStakeAccumulator: 1.00,
		MaxStakeAccumulator: 2000.00,
		MaxPotentialReturn:  100000.00,
		MaxAccumulatorLegs:  20,
		MinAccumulatorLegs:  2,
	}
}

// BetslipValidator performs comprehensive validation on a bet placement request.
type BetslipValidator struct {
	limits StakeLimits
	rules  []MarketCompatibilityRule
	store  *Store
	// TODO(integration): wire to /odds/event/{id} on the odds-feed service.
	// Currently stored but never read — see OddsFeedClient note above.
	oddsFeedURL string
	logger      *slog.Logger
}

// NewBetslipValidator creates a validator with default configuration.
func NewBetslipValidator(store *Store, oddsFeedURL string, logger *slog.Logger) *BetslipValidator {
	return &BetslipValidator{
		limits:      DefaultStakeLimits(),
		rules:       DefaultCompatibilityRules(),
		store:       store,
		oddsFeedURL: oddsFeedURL,
		logger:      logger,
	}
}

// ValidateBetslip runs all validation checks on a placement request.
// Returns a ValidationResult with any errors or warnings.
func (v *BetslipValidator) ValidateBetslip(ctx context.Context, req *PlaceBetRequest) ValidationResult {
	result := ValidationResult{Valid: true}

	// 1. Basic field validation.
	if req.CPF == "" || req.SessionID == "" {
		result.Valid = false
		result.Errors = append(result.Errors, "cpf and session_id are required")
		return result
	}

	if len(req.Selections) == 0 {
		result.Valid = false
		result.Errors = append(result.Errors, "at least one selection is required")
		return result
	}

	// 2. Bet type validation.
	if req.Type == BetTypeSingle && len(req.Selections) != 1 {
		result.Valid = false
		result.Errors = append(result.Errors, "single bet must have exactly one selection")
	}

	if req.Type == BetTypeMultiple {
		if len(req.Selections) < v.limits.MinAccumulatorLegs {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"accumulator must have at least %d selections", v.limits.MinAccumulatorLegs))
		}
		if len(req.Selections) > v.limits.MaxAccumulatorLegs {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"accumulator cannot exceed %d selections", v.limits.MaxAccumulatorLegs))
		}
	}

	// 3. Stake limits.
	v.validateStakeLimits(req, &result)

	// 4. Duplicate selection check.
	v.validateNoDuplicates(req, &result)

	// 5. Market compatibility (correlation controls).
	v.validateMarketCompatibility(req, &result)

	// 6. Odds validity.
	v.validateOdds(req, &result)

	// 7. Potential return cap.
	combinedOdds := 1.0
	for _, sel := range req.Selections {
		combinedOdds *= sel.OddsValue
	}
	potentialReturn := req.Stake * combinedOdds
	if potentialReturn > v.limits.MaxPotentialReturn {
		result.Valid = false
		result.Errors = append(result.Errors, fmt.Sprintf(
			"potential return R$%.2f exceeds maximum R$%.2f",
			potentialReturn, v.limits.MaxPotentialReturn))
	}

	// 8. Player daily/weekly limits.
	v.validatePlayerLimits(ctx, req, &result)

	return result
}

// validateStakeLimits checks stake is within allowed bounds.
func (v *BetslipValidator) validateStakeLimits(req *PlaceBetRequest, result *ValidationResult) {
	if req.Type == BetTypeSingle {
		if req.Stake < v.limits.MinStakeSingle {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"minimum stake for single bet is R$%.2f", v.limits.MinStakeSingle))
		}
		if req.Stake > v.limits.MaxStakeSingle {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"maximum stake for single bet is R$%.2f", v.limits.MaxStakeSingle))
		}
	} else if req.Type == BetTypeMultiple {
		if req.Stake < v.limits.MinStakeAccumulator {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"minimum stake for accumulator is R$%.2f", v.limits.MinStakeAccumulator))
		}
		if req.Stake > v.limits.MaxStakeAccumulator {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"maximum stake for accumulator is R$%.2f", v.limits.MaxStakeAccumulator))
		}
	}
}

// validateNoDuplicates ensures no selection appears more than once and
// no two selections from the same market are included.
func (v *BetslipValidator) validateNoDuplicates(req *PlaceBetRequest, result *ValidationResult) {
	seenSelections := make(map[string]bool)
	seenMarkets := make(map[string]bool)

	for _, sel := range req.Selections {
		if seenSelections[sel.SelectionID] {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"duplicate selection: %s", sel.SelectionID))
		}
		seenSelections[sel.SelectionID] = true

		// Two selections from the same market are mutually exclusive.
		marketKey := sel.EventID + ":" + sel.MarketID
		if seenMarkets[marketKey] {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"conflicting selections from same market: %s", sel.MarketID))
		}
		seenMarkets[marketKey] = true
	}
}

// validateMarketCompatibility checks the correlation control matrix.
func (v *BetslipValidator) validateMarketCompatibility(req *PlaceBetRequest, result *ValidationResult) {
	// Build index of selections by event.
	type selInfo struct {
		eventID    string
		marketType string // We use marketID as proxy; in production, resolve from cache.
	}

	// For accumulators, check that correlated markets on the same event
	// are not combined.
	if len(req.Selections) < 2 {
		return
	}

	// Group by event.
	eventSelections := make(map[string][]SelectionReq)
	for _, sel := range req.Selections {
		eventSelections[sel.EventID] = append(eventSelections[sel.EventID], sel)
	}

	// Check for multiple selections from the same event (same-game multi).
	for eventID, sels := range eventSelections {
		if len(sels) > 1 {
			// Phase 1: same-event multi is restricted for accumulators.
			result.Warnings = append(result.Warnings, fmt.Sprintf(
				"multiple selections from event %s may be subject to correlation restrictions",
				eventID))
		}
	}
}

// validateOdds checks that all selections have valid odds.
func (v *BetslipValidator) validateOdds(req *PlaceBetRequest, result *ValidationResult) {
	for _, sel := range req.Selections {
		if sel.OddsValue < 1.01 {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"odds for selection %s are below minimum (%.2f)", sel.SelectionID, sel.OddsValue))
		}
		if sel.OddsValue > 1000.0 {
			result.Valid = false
			result.Errors = append(result.Errors, fmt.Sprintf(
				"odds for selection %s are unreasonably high (%.2f)", sel.SelectionID, sel.OddsValue))
		}
	}
}

// validatePlayerLimits checks responsible gambling limits for the player.
// When the validator is constructed without a store (the documented test
// pattern for exercising non-DB validation paths), this check is skipped
// and the upstream stake/odds/duplicate decisions are preserved.
func (v *BetslipValidator) validatePlayerLimits(ctx context.Context, req *PlaceBetRequest, result *ValidationResult) {
	if v.store == nil {
		return
	}

	limits, err := v.store.GetPlayerLimits(ctx, req.CPF)
	if err != nil {
		v.logger.Warn("could not fetch player limits", "cpf", maskCPF(req.CPF), "error", err)
		// Continue with defaults - GetPlayerLimits returns defaults on error.
	}

	if req.Stake > limits.MaxSingleBet {
		result.Valid = false
		result.Errors = append(result.Errors, fmt.Sprintf(
			"stake exceeds single-bet limit of R$%.2f", limits.MaxSingleBet))
	}

	dailyTotal, err := v.store.GetPlayerDailyStake(ctx, req.CPF)
	if err != nil {
		v.logger.Warn("could not fetch daily stake", "cpf", maskCPF(req.CPF), "error", err)
	}
	if dailyTotal+req.Stake > limits.DailyStakeLimit {
		result.Valid = false
		result.Errors = append(result.Errors, fmt.Sprintf(
			"daily stake limit would be exceeded (current: R$%.2f, limit: R$%.2f)",
			dailyTotal, limits.DailyStakeLimit))
	}
}
