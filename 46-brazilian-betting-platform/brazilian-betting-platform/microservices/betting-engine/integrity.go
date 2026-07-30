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
	"time"

	"github.com/google/uuid"
)

// AlertType classifies the category of an integrity alert per Portaria 1207/2024.
type AlertType string

const (
	AlertTypeUnusualStakePattern     AlertType = "UNUSUAL_STAKE_PATTERN"
	AlertTypeRapidFireBets           AlertType = "RAPID_FIRE_BETS"
	AlertTypeOddsManipulation        AlertType = "ODDS_MANIPULATION"
	AlertTypeSuspiciousIPActivity    AlertType = "SUSPICIOUS_IP_ACTIVITY"
	AlertTypeMultipleAccountSuspect  AlertType = "MULTIPLE_ACCOUNT_SUSPECT"
	AlertTypeLargeWinClaim           AlertType = "LARGE_WIN_CLAIM"
	AlertTypeAbnormalOddsAcceptance  AlertType = "ABNORMAL_ODDS_ACCEPTANCE"
	AlertTypeMatchFixingRisk         AlertType = "MATCH_FIXING_RISK"
	AlertTypeExcessiveLosses         AlertType = "EXCESSIVE_LOSSES"
	AlertTypeLimitCircumvention      AlertType = "LIMIT_CIRCUMVENTION"
)

// Severity levels for integrity alerts.
const (
	SeverityLow      = "low"
	SeverityMedium   = "medium"
	SeverityHigh     = "high"
	SeverityCritical = "critical"
)

// IntegrityRule is a function that evaluates a bet and returns alerts.
type IntegrityRule func(ctx context.Context, bet *Bet, history []Bet) []IntegrityAlert

// IntegrityChecker evaluates bets against Portaria 1207/2024 integrity rules.
type IntegrityChecker struct {
	store  *Store
	logger *slog.Logger
	rules  []IntegrityRule
}

// NewIntegrityChecker builds a checker with all standard Brazilian regulatory rules.
func NewIntegrityChecker(store *Store, logger *slog.Logger) *IntegrityChecker {
	ic := &IntegrityChecker{store: store, logger: logger}
	ic.rules = []IntegrityRule{
		ic.checkRapidFireBets,
		ic.checkLargeStake,
		ic.checkAbnormalOdds,
		ic.checkFrequencyAnomaly,
		ic.checkStakeEscalation,
	}
	return ic
}

// Evaluate runs all integrity rules against the bet and persists any alerts.
func (ic *IntegrityChecker) Evaluate(ctx context.Context, bet *Bet) ([]IntegrityAlert, error) {
	history, err := ic.store.GetBetsByPlayer(ctx, bet.CPF)
	if err != nil {
		ic.logger.Warn("integrity: could not load player history",
			"cpf", maskCPF(bet.CPF), "error", err)
		history = nil
	}

	var alerts []IntegrityAlert
	for _, rule := range ic.rules {
		alerts = append(alerts, rule(ctx, bet, history)...)
	}

	for i := range alerts {
		if err := ic.store.SaveIntegrityAlert(ctx, &alerts[i]); err != nil {
			ic.logger.Error("integrity: failed to save alert",
				"alert_type", alerts[i].AlertType, "error", err)
		}
	}

	if len(alerts) > 0 {
		ic.logger.Info("integrity alerts generated",
			"bet_id", bet.ID,
			"count", len(alerts),
		)
	}

	return alerts, nil
}

// checkRapidFireBets flags bets placed in rapid succession (> 10 bets within 60 seconds).
func (ic *IntegrityChecker) checkRapidFireBets(_ context.Context, bet *Bet, history []Bet) []IntegrityAlert {
	window := time.Now().UTC().Add(-60 * time.Second)
	count := 0
	for _, h := range history {
		if h.PlacedAt.After(window) && h.ID != bet.ID {
			count++
		}
	}
	if count >= 10 {
		return []IntegrityAlert{ic.newAlert(bet, AlertTypeRapidFireBets, SeverityHigh,
			fmt.Sprintf("Player placed %d bets in the last 60 seconds", count))}
	}
	return nil
}

// checkLargeStake flags single bets above R$5,000.
func (ic *IntegrityChecker) checkLargeStake(_ context.Context, bet *Bet, _ []Bet) []IntegrityAlert {
	const largeStakeThreshold = 5000.00
	if bet.Stake >= largeStakeThreshold {
		severity := SeverityMedium
		if bet.Stake >= 20000 {
			severity = SeverityCritical
		} else if bet.Stake >= 10000 {
			severity = SeverityHigh
		}
		return []IntegrityAlert{ic.newAlert(bet, AlertTypeUnusualStakePattern, severity,
			fmt.Sprintf("Large single bet of R$%.2f placed", bet.Stake))}
	}
	return nil
}

// checkAbnormalOdds flags bets on selections with combined odds > 1000.
func (ic *IntegrityChecker) checkAbnormalOdds(_ context.Context, bet *Bet, _ []Bet) []IntegrityAlert {
	const highOddsThreshold = 1000.0
	if bet.CombinedOdds >= highOddsThreshold {
		return []IntegrityAlert{ic.newAlert(bet, AlertTypeAbnormalOddsAcceptance, SeverityMedium,
			fmt.Sprintf("Bet placed at combined odds of %.2f", bet.CombinedOdds))}
	}
	return nil
}

// checkFrequencyAnomaly flags players with > 50 bets per hour.
func (ic *IntegrityChecker) checkFrequencyAnomaly(_ context.Context, bet *Bet, history []Bet) []IntegrityAlert {
	window := time.Now().UTC().Add(-1 * time.Hour)
	count := 0
	for _, h := range history {
		if h.PlacedAt.After(window) {
			count++
		}
	}
	if count >= 50 {
		return []IntegrityAlert{ic.newAlert(bet, AlertTypeRapidFireBets, SeverityHigh,
			fmt.Sprintf("Player placed %d bets within the last hour", count))}
	}
	return nil
}

// checkStakeEscalation flags sudden stake increases > 10x recent average.
func (ic *IntegrityChecker) checkStakeEscalation(_ context.Context, bet *Bet, history []Bet) []IntegrityAlert {
	if len(history) < 5 {
		return nil
	}
	var sum float64
	const sampleSize = 10
	n := min(sampleSize, len(history))
	for i := 0; i < n; i++ {
		sum += history[i].Stake
	}
	avg := sum / float64(n)
	if avg > 0 && bet.Stake >= avg*10 {
		return []IntegrityAlert{ic.newAlert(bet, AlertTypeUnusualStakePattern, SeverityHigh,
			fmt.Sprintf("Stake R$%.2f is %.1fx the recent average of R$%.2f", bet.Stake, bet.Stake/avg, avg))}
	}
	return nil
}

func (ic *IntegrityChecker) newAlert(bet *Bet, alertType AlertType, severity, description string) IntegrityAlert {
	return IntegrityAlert{
		ID:          uuid.NewString(),
		BetID:       bet.ID,
		CPF:         bet.CPF,
		AlertType:   string(alertType),
		Description: description,
		Severity:    severity,
		Reported:    false,
		CreatedAt:   time.Now().UTC(),
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
