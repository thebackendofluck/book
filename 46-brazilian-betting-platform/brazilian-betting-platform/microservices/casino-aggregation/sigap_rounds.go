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
	"os"
	"time"

	"github.com/google/uuid"
	"github.com/rs/zerolog/log"
)

// SIGAPRoundsReporter generates round-level GGR reports for SIGAP.
//
// Legal basis:
//   - Portaria MF 615/2023 Art. 27 — round-level reporting obligations
//   - SIGAP Technical Specification v2.1 — casino round data format
//
// Reports must capture:
//   1. Every completed round with CPF, game, bet, and win amounts.
//   2. GGR contribution per round (bet - win; floors at 0 for player wins).
//   3. Monthly aggregates submitted by the 10th of the following month.
//
// Note: This implementation uses an in-memory store. Production should
// persist rounds to PostgreSQL and query by period.

// SIGAPRoundsReporter accumulates completed rounds and generates reports.
type SIGAPRoundsReporter struct {
	rounds       []Round
	gameMap      map[string]Game // gameID -> Game (for name/category lookup)
	operatorCNPJ string
}

// NewSIGAPRoundsReporter creates a reporter with the operator CNPJ from env.
func NewSIGAPRoundsReporter(catalog []Game) *SIGAPRoundsReporter {
	gm := make(map[string]Game, len(catalog))
	for _, g := range catalog {
		gm[g.GameID] = g
	}
	return &SIGAPRoundsReporter{
		rounds:       make([]Round, 0, 1024),
		gameMap:      gm,
		operatorCNPJ: os.Getenv("OPERATOR_CNPJ"),
	}
}

// RecordRound appends a completed round to the in-memory store.
func (s *SIGAPRoundsReporter) RecordRound(r Round) {
	// Compute GGR contribution; cannot be negative (operator cannot gain from a player win
	// at round level — net position is tracked at settlement).
	r.GGRContribution = r.BetAmount - r.WinAmount

	s.rounds = append(s.rounds, r)
	log.Debug().
		Str("round_id", r.RoundID).
		Str("cpf", r.CPF).
		Float64("bet", r.BetAmount).
		Float64("win", r.WinAmount).
		Float64("ggr", r.GGRContribution).
		Msg("Round recorded for SIGAP")
}

// UpdateGameCatalog refreshes the game metadata used for report enrichment.
func (s *SIGAPRoundsReporter) UpdateGameCatalog(catalog []Game) {
	for _, g := range catalog {
		s.gameMap[g.GameID] = g
	}
}

// GenerateReport produces the SIGAP round-level report for the given period.
//
// period format: "YYYY-MM"
func (s *SIGAPRoundsReporter) GenerateReport(period string) (*SIGAPRoundReport, error) {
	start, end, err := parsePeriod(period)
	if err != nil {
		return nil, err
	}

	var records []SIGAPRoundRecord
	var totalBet, totalWin, totalGGR float64

	for _, r := range s.rounds {
		if r.CompletedAt.Before(start) || !r.CompletedAt.Before(end) {
			continue
		}

		game, ok := s.gameMap[r.GameID]
		gameName := r.GameID
		sigapCat := "CASINO_GERAL"
		if ok {
			gameName = game.Name
			sigapCat = game.SIGAPCategory
		}

		records = append(records, SIGAPRoundRecord{
			RoundID:         r.RoundID,
			GameID:          r.GameID,
			GameName:        gameName,
			SIGAPCategory:   sigapCat,
			CPF:             r.CPF,
			BetAmount:       r.BetAmount,
			WinAmount:       r.WinAmount,
			GGRContribution: r.GGRContribution,
			RoundDate:       r.CompletedAt.Format("2006-01-02"),
		})

		totalBet += r.BetAmount
		totalWin += r.WinAmount
		totalGGR += r.GGRContribution
	}

	reportID := fmt.Sprintf("SIGAP-ROUNDS-%s-%s", period, uuid.New().String()[:8])

	report := &SIGAPRoundReport{
		ReportID:     reportID,
		Period:       period,
		OperatorCNPJ: s.operatorCNPJ,
		TotalRounds:  len(records),
		TotalBet:     roundFloat(totalBet),
		TotalWin:     roundFloat(totalWin),
		TotalGGR:     roundFloat(totalGGR),
		Records:      records,
		GeneratedAt:  time.Now().UTC().Format(time.RFC3339),
	}

	log.Info().
		Str("report_id", reportID).
		Str("period", period).
		Int("rounds", len(records)).
		Float64("total_ggr", totalGGR).
		Msg("SIGAP rounds report generated")

	return report, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// parsePeriod parses "YYYY-MM" and returns the inclusive start and exclusive end.
func parsePeriod(period string) (time.Time, time.Time, error) {
	var year, month int
	_, err := fmt.Sscanf(period, "%d-%d", &year, &month)
	if err != nil || month < 1 || month > 12 {
		return time.Time{}, time.Time{}, fmt.Errorf("invalid period format: %q (expected YYYY-MM)", period)
	}
	start := time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.UTC)
	end := start.AddDate(0, 1, 0)
	return start, end, nil
}

// roundFloat rounds to 2 decimal places.
func roundFloat(f float64) float64 {
	return float64(int(f*100+0.5)) / 100
}
