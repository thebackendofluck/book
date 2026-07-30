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

// GGRCalculator computes Gross Gaming Revenue and generates SIGAP reports.
// GGR = Total bets staked - Gross prizes paid out. Player income tax is
// withheld FROM the gross prize (a slice of the winnings), not a separate
// operator disbursement, so it must NOT be subtracted again.
type GGRCalculator struct {
	store      *Store
	operatorID string
	logger     *slog.Logger
}

// NewGGRCalculator creates a GGRCalculator.
func NewGGRCalculator(store *Store, operatorID string, logger *slog.Logger) *GGRCalculator {
	return &GGRCalculator{store: store, operatorID: operatorID, logger: logger}
}

// CalculateDaily computes the GGR for a given date (YYYY-MM-DD).
// If date is empty, uses yesterday.
func (g *GGRCalculator) CalculateDaily(ctx context.Context, date string) (*GGRReport, error) {
	if date == "" {
		date = time.Now().UTC().AddDate(0, 0, -1).Format("2006-01-02")
	}

	totals, err := g.store.GetDailySettlementTotals(ctx, date)
	if err != nil {
		return nil, fmt.Errorf("get daily settlement totals for %s: %w", date, err)
	}

	totalBets := totals["total_bets"]
	totalPrizes := totals["total_prizes"]
	totalTax := totals["total_tax"]
	betCount := int(totals["bet_count"])
	playerCount := int(totals["player_count"])

	// GGR = Total staked - gross prizes (tax is already inside the prize).
	ggr := totalBets - totalPrizes
	_ = totalTax // retained for the report breakdown, not subtracted from GGR

	report := &GGRReport{
		ID:                uuid.NewString(),
		ReportDate:        date,
		OperatorID:        g.operatorID,
		TotalBetsAmount:   totalBets,
		TotalPrizesPaid:   totalPrizes,
		TotalTaxWithheld:  totalTax,
		GGR:               ggr,
		TotalBetCount:     betCount,
		UniquePlayerCount: playerCount,
		GeneratedAt:       time.Now().UTC(),
	}

	if err := g.store.SaveGGRReport(ctx, report); err != nil {
		return nil, fmt.Errorf("save ggr report: %w", err)
	}

	g.logger.Info("ggr calculated",
		"date", date,
		"total_bets", totalBets,
		"total_prizes", totalPrizes,
		"total_tax", totalTax,
		"ggr", ggr,
	)

	return report, nil
}

// GetReport retrieves a previously generated GGR report by date.
func (g *GGRCalculator) GetReport(ctx context.Context, date string) (*GGRReport, error) {
	return g.store.GetGGRReport(ctx, date)
}

// GGRFromSettlements calculates GGR directly from a set of settlements.
// This is useful for real-time GGR during event settlement.
func GGRFromSettlements(settlements []Settlement) (totalStake, totalPrizes, totalTax, ggr float64) {
	for _, s := range settlements {
		totalStake += s.Stake
		totalPrizes += s.GrossReturn
		totalTax += s.TaxWithheld
	}
	// GGR = staked - gross prizes. Tax is withheld from the prize and remitted
	// to the treasury, so it is already inside totalPrizes; subtracting it again
	// would understate the GGR (and thus the tax base computed from it).
	ggr = totalStake - totalPrizes
	return
}
