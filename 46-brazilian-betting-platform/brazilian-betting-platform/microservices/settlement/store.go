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
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Store handles all PostgreSQL operations for the settlement service.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a new Store.
func NewStore(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool}
}

// GetUnsettledBetsForEvent retrieves all pending bets for a given event.
func (s *Store) GetUnsettledBetsForEvent(ctx context.Context, eventID string) ([]BetRecord, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT b.id, b.cpf, b.stake, b.potential_return, b.combined_odds
		FROM bets b
		JOIN bet_selections bs ON bs.bet_id = b.id
		WHERE bs.event_id = $1 AND b.status IN ('accepted','pending')
		GROUP BY b.id`, eventID,
	)
	if err != nil {
		return nil, fmt.Errorf("query unsettled bets: %w", err)
	}
	defer rows.Close()

	var bets []BetRecord
	for rows.Next() {
		var b BetRecord
		if err := rows.Scan(&b.ID, &b.CPF, &b.Stake, &b.PotentialReturn, &b.CombinedOdds); err != nil {
			return nil, fmt.Errorf("scan bet: %w", err)
		}
		bets = append(bets, b)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	// Load selections for each bet.
	for i, b := range bets {
		selRows, err := s.pool.Query(ctx, `
			SELECT selection_id, odds_value FROM bet_selections WHERE bet_id = $1
			ORDER BY selection_id`, b.ID)
		if err != nil {
			return nil, fmt.Errorf("query selections: %w", err)
		}
		for selRows.Next() {
			var sel BetSelectionRecord
			if err := selRows.Scan(&sel.SelectionID, &sel.OddsValue); err != nil {
				selRows.Close()
				return nil, err
			}
			bets[i].Selections = append(bets[i].Selections, sel)
		}
		selRows.Close()
	}
	return bets, nil
}

// SaveSettlement persists a settlement record and updates the bet status.
// SaveSettlement is idempotent: it advances the bet to 'settled' and inserts
// the settlement row in ONE transaction, gated on the bet still being
// unsettled. A replay finds the bet already settled, updates zero rows, and
// skips the insert, so a bet can never be settled (or paid) twice.
func (s *Store) SaveSettlement(ctx context.Context, settlement *Settlement) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin settlement tx: %w", err)
	}
	defer tx.Rollback(ctx)

	ct, err := tx.Exec(ctx,
		`UPDATE bets SET status = 'settled'
		 WHERE id = $1 AND status IN ('accepted','pending')`,
		settlement.BetID,
	)
	if err != nil {
		return fmt.Errorf("mark bet settled: %w", err)
	}
	if ct.RowsAffected() == 0 {
		// Already settled (or not settleable): idempotent no-op.
		return nil
	}

	if _, err := tx.Exec(ctx, `
		INSERT INTO settlements (id, bet_id, event_id, cpf, outcome, stake, gross_return, tax_withheld, net_payout, ggr_contrib, sigap_reported, settled_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
		// (Recommend a UNIQUE(bet_id) constraint on settlements as an extra
		// DB-level backstop; the status-gate above already prevents re-insert.)
		settlement.ID, settlement.BetID, settlement.EventID, settlement.CPF,
		settlement.Outcome, settlement.Stake, settlement.GrossReturn,
		settlement.TaxWithheld, settlement.NetPayout, settlement.GGRContrib,
		settlement.SIGAPReported, settlement.SettledAt,
	); err != nil {
		return fmt.Errorf("insert settlement: %w", err)
	}
	return tx.Commit(ctx)
}

// SaveEventSettlementRun persists an event settlement run record.
func (s *Store) SaveEventSettlementRun(ctx context.Context, run *EventSettlementRun) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO event_settlement_runs (id, event_id, status, total_bets, winning_bets, losing_bets, void_bets, total_stake, total_prizes_paid, total_tax_withheld, ggr, started_at, completed_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
		run.ID, run.EventID, run.Status, run.TotalBets, run.WinningBets, run.LosingBets,
		run.VoidBets, run.TotalStake, run.TotalPrizesPaid, run.TotalTaxWithheld,
		run.GGR, run.StartedAt, run.CompletedAt,
	)
	return err
}

// GetDailySettlementTotals returns aggregated settlement data for a date.
func (s *Store) GetDailySettlementTotals(ctx context.Context, date string) (map[string]float64, error) {
	row := s.pool.QueryRow(ctx, `
		SELECT
			COALESCE(SUM(stake), 0)           AS total_bets,
			COALESCE(SUM(gross_return), 0)    AS total_prizes,
			COALESCE(SUM(tax_withheld), 0)    AS total_tax,
			COUNT(*)                          AS bet_count,
			COUNT(DISTINCT cpf)               AS player_count
		FROM settlements
		WHERE DATE(settled_at) = $1`, date,
	)

	var totalBets, totalPrizes, totalTax, betCount, playerCount float64
	if err := row.Scan(&totalBets, &totalPrizes, &totalTax, &betCount, &playerCount); err != nil {
		return nil, fmt.Errorf("scan daily totals: %w", err)
	}
	return map[string]float64{
		"total_bets":    totalBets,
		"total_prizes":  totalPrizes,
		"total_tax":     totalTax,
		"bet_count":     betCount,
		"player_count":  playerCount,
	}, nil
}

// SaveGGRReport persists a GGR report.
func (s *Store) SaveGGRReport(ctx context.Context, report *GGRReport) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO ggr_reports (id, report_date, operator_id, total_bets_amount, total_prizes_paid, total_tax_withheld, ggr, total_bet_count, unique_player_count, generated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (report_date) DO UPDATE
		SET total_bets_amount=$4, total_prizes_paid=$5, total_tax_withheld=$6, ggr=$7, total_bet_count=$8, unique_player_count=$9, generated_at=$10`,
		report.ID, report.ReportDate, report.OperatorID,
		report.TotalBetsAmount, report.TotalPrizesPaid, report.TotalTaxWithheld,
		report.GGR, report.TotalBetCount, report.UniquePlayerCount, report.GeneratedAt,
	)
	return err
}

// GetGGRReport retrieves a GGR report by date.
func (s *Store) GetGGRReport(ctx context.Context, date string) (*GGRReport, error) {
	r := &GGRReport{}
	err := s.pool.QueryRow(ctx, `
		SELECT id, report_date, operator_id, total_bets_amount, total_prizes_paid, total_tax_withheld, ggr, total_bet_count, unique_player_count, sigap_submitted_at, generated_at
		FROM ggr_reports WHERE report_date = $1`, date,
	).Scan(&r.ID, &r.ReportDate, &r.OperatorID, &r.TotalBetsAmount, &r.TotalPrizesPaid,
		&r.TotalTaxWithheld, &r.GGR, &r.TotalBetCount, &r.UniquePlayerCount,
		&r.SIGAPSubmittedAt, &r.GeneratedAt)
	if err != nil {
		return nil, fmt.Errorf("get ggr report: %w", err)
	}
	return r, nil
}

// SaveTaxWithholding persists a tax withholding record.
func (s *Store) SaveTaxWithholding(ctx context.Context, tw *TaxWithholding) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO tax_withholdings (id, cpf, period, total_winnings, total_stake, net_winnings, taxable_amount, tax_rate, tax_amount, withheld, withheld_at, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
		tw.ID, tw.CPF, tw.Period, tw.TotalWinnings, tw.TotalStake,
		tw.NetWinnings, tw.TaxableAmount, tw.TaxRate, tw.TaxAmount,
		tw.Withheld, tw.WithheldAt, tw.CreatedAt,
	)
	return err
}

// GetPlayerPeriodTotals returns a player's total winnings and stakes for a period.
func (s *Store) GetPlayerPeriodTotals(ctx context.Context, cpf, period string) (map[string]float64, error) {
	row := s.pool.QueryRow(ctx, `
		SELECT
			COALESCE(SUM(gross_return), 0) AS total_winnings,
			COALESCE(SUM(stake), 0)        AS total_stake
		FROM settlements
		WHERE cpf = $1
		  AND TO_CHAR(settled_at, 'YYYY-MM') = $2
		  AND outcome = 'won'`, cpf, period,
	)

	var winnings, stake float64
	if err := row.Scan(&winnings, &stake); err != nil {
		return nil, fmt.Errorf("scan player period totals: %w", err)
	}
	return map[string]float64{
		"total_winnings": winnings,
		"total_stake":    stake,
	}, nil
}

// GetLatestGGRReports returns the most recent N GGR reports.
func (s *Store) GetLatestGGRReports(ctx context.Context, n int) ([]GGRReport, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, report_date, operator_id, total_bets_amount, total_prizes_paid, total_tax_withheld, ggr, total_bet_count, unique_player_count, generated_at
		FROM ggr_reports ORDER BY report_date DESC LIMIT $1`, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var reports []GGRReport
	for rows.Next() {
		var r GGRReport
		if err := rows.Scan(&r.ID, &r.ReportDate, &r.OperatorID, &r.TotalBetsAmount,
			&r.TotalPrizesPaid, &r.TotalTaxWithheld, &r.GGR,
			&r.TotalBetCount, &r.UniquePlayerCount, &r.GeneratedAt); err != nil {
			return nil, err
		}
		reports = append(reports, r)
	}
	return reports, rows.Err()
}

// MarkSettlementSIGAPReported marks a settlement as reported to SIGAP.
func (s *Store) MarkSettlementSIGAPReported(ctx context.Context, settlementID string) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE settlements SET sigap_reported=true WHERE id=$1`, settlementID)
	return err
}

// MarkGGRReportSIGAPSubmitted records when a GGR report was submitted to SIGAP.
func (s *Store) MarkGGRReportSIGAPSubmitted(ctx context.Context, reportDate string) error {
	now := time.Now().UTC()
	_, err := s.pool.Exec(ctx,
		`UPDATE ggr_reports SET sigap_submitted_at=$1 WHERE report_date=$2`, now, reportDate)
	return err
}
