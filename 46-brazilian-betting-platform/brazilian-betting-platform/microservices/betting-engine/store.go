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

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store handles all PostgreSQL operations for the betting engine.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a new Store with the given connection pool.
func NewStore(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool}
}

// CreateBet persists a new bet and its selections atomically.
func (s *Store) CreateBet(ctx context.Context, bet *Bet) error {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.Serializable})
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	_, err = tx.Exec(ctx, `
		INSERT INTO bets (
			id, cpf, type, status, stake, potential_return,
			combined_odds, sigap_report_id, ip_address, device_id, placed_at, updated_at
		) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
		bet.ID, bet.CPF, bet.Type, bet.Status, bet.Stake, bet.PotentialReturn,
		bet.CombinedOdds, bet.SIGAPReportID, bet.IPAddress, bet.DeviceID,
		bet.PlacedAt, bet.UpdatedAt,
	)
	if err != nil {
		return fmt.Errorf("insert bet: %w", err)
	}

	for _, sel := range bet.Selections {
		_, err = tx.Exec(ctx, `
			INSERT INTO bet_selections (
				id, bet_id, event_id, market_id, selection_id,
				odds_value, event_name, market_name, selection_name, result
			) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
			sel.ID, sel.BetID, sel.EventID, sel.MarketID, sel.SelectionID,
			sel.OddsValue, sel.EventName, sel.MarketName, sel.SelectionName, sel.Result,
		)
		if err != nil {
			return fmt.Errorf("insert selection: %w", err)
		}
	}

	return tx.Commit(ctx)
}

// GetBet retrieves a bet with all its selections by ID.
func (s *Store) GetBet(ctx context.Context, id string) (*Bet, error) {
	bet := &Bet{}
	err := s.pool.QueryRow(ctx, `
		SELECT id, cpf, type, status, stake, potential_return, actual_return,
		       combined_odds, cashout_value, cashed_out_at, sigap_report_id,
		       ip_address, device_id, placed_at, settled_at, updated_at
		FROM bets WHERE id = $1`, id,
	).Scan(
		&bet.ID, &bet.CPF, &bet.Type, &bet.Status, &bet.Stake,
		&bet.PotentialReturn, &bet.ActualReturn, &bet.CombinedOdds,
		&bet.CashoutValue, &bet.CashedOutAt, &bet.SIGAPReportID,
		&bet.IPAddress, &bet.DeviceID, &bet.PlacedAt, &bet.SettledAt, &bet.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("get bet: %w", err)
	}

	rows, err := s.pool.Query(ctx, `
		SELECT id, bet_id, event_id, market_id, selection_id,
		       odds_value, event_name, market_name, selection_name, result, settled_at
		FROM bet_selections WHERE bet_id = $1`, id,
	)
	if err != nil {
		return nil, fmt.Errorf("get selections: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var sel BetSelection
		if err := rows.Scan(
			&sel.ID, &sel.BetID, &sel.EventID, &sel.MarketID, &sel.SelectionID,
			&sel.OddsValue, &sel.EventName, &sel.MarketName, &sel.SelectionName,
			&sel.Result, &sel.SettledAt,
		); err != nil {
			return nil, fmt.Errorf("scan selection: %w", err)
		}
		bet.Selections = append(bet.Selections, sel)
	}

	return bet, rows.Err()
}

// GetBetsByPlayer returns all bets for a given CPF, newest first.
func (s *Store) GetBetsByPlayer(ctx context.Context, cpf string) ([]Bet, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, cpf, type, status, stake, potential_return, actual_return,
		       combined_odds, cashout_value, cashed_out_at, sigap_report_id,
		       ip_address, device_id, placed_at, settled_at, updated_at
		FROM bets WHERE cpf = $1 ORDER BY placed_at DESC`, cpf,
	)
	if err != nil {
		return nil, fmt.Errorf("query bets by player: %w", err)
	}
	defer rows.Close()

	var bets []Bet
	for rows.Next() {
		var b Bet
		if err := rows.Scan(
			&b.ID, &b.CPF, &b.Type, &b.Status, &b.Stake,
			&b.PotentialReturn, &b.ActualReturn, &b.CombinedOdds,
			&b.CashoutValue, &b.CashedOutAt, &b.SIGAPReportID,
			&b.IPAddress, &b.DeviceID, &b.PlacedAt, &b.SettledAt, &b.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan bet: %w", err)
		}
		bets = append(bets, b)
	}
	return bets, rows.Err()
}

// UpdateBetStatus updates the status and optional fields of a bet.
func (s *Store) UpdateBetStatus(ctx context.Context, id string, status BetStatus, updates map[string]any) error {
	now := time.Now().UTC()
	_, err := s.pool.Exec(ctx,
		`UPDATE bets SET status=$1, updated_at=$2 WHERE id=$3`,
		status, now, id,
	)
	return err
}

// CashoutBet records an early cashout, updating the bet atomically.
func (s *Store) CashoutBet(ctx context.Context, id string, value float64) error {
	now := time.Now().UTC()
	_, err := s.pool.Exec(ctx, `
		UPDATE bets
		SET status=$1, cashout_value=$2, cashed_out_at=$3, actual_return=$2, updated_at=$3
		WHERE id=$4 AND status IN ('pending','accepted')`,
		BetStatusCashedOut, value, now, id,
	)
	return err
}

// SettleBet records the final settlement result for a bet.
func (s *Store) SettleBet(ctx context.Context, betID string, s2 *Settlement) error {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.ReadCommitted})
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	now := time.Now().UTC()
	_, err = tx.Exec(ctx, `
		UPDATE bets SET status=$1, actual_return=$2, settled_at=$3, updated_at=$3
		WHERE id=$4`,
		BetStatusSettled, s2.Return, now, betID,
	)
	if err != nil {
		return fmt.Errorf("update bet: %w", err)
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO settlements (id, bet_id, cpf, result, stake, return, profit, tax_withheld, net_payout, settled_by, settled_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
		s2.ID, s2.BetID, s2.CPF, s2.Result, s2.Stake, s2.Return, s2.Profit,
		s2.TaxWithheld, s2.NetPayout, s2.SettledBy, now,
	)
	if err != nil {
		return fmt.Errorf("insert settlement: %w", err)
	}

	return tx.Commit(ctx)
}

// GetPlayerDailyStake returns the total amount staked by a player today.
func (s *Store) GetPlayerDailyStake(ctx context.Context, cpf string) (float64, error) {
	var total float64
	err := s.pool.QueryRow(ctx, `
		SELECT COALESCE(SUM(stake), 0)
		FROM bets
		WHERE cpf=$1 AND placed_at >= CURRENT_DATE AND status != 'cancelled'`,
		cpf,
	).Scan(&total)
	return total, err
}

// GetPlayerLimits returns the responsible gambling limits for a player.
func (s *Store) GetPlayerLimits(ctx context.Context, cpf string) (*PlayerLimits, error) {
	limits := &PlayerLimits{}
	err := s.pool.QueryRow(ctx, `
		SELECT cpf, daily_stake_limit, weekly_stake_limit, monthly_stake_limit, max_single_bet, updated_at
		FROM player_limits WHERE cpf=$1`, cpf,
	).Scan(&limits.CPF, &limits.DailyStakeLimit, &limits.WeeklyStakeLimit,
		&limits.MonthlyStakeLimit, &limits.MaxSingleBet, &limits.UpdatedAt)
	if err != nil {
		// Return default limits if none set
		return &PlayerLimits{
			CPF:               cpf,
			DailyStakeLimit:   1000.00,
			WeeklyStakeLimit:  5000.00,
			MonthlyStakeLimit: 15000.00,
			MaxSingleBet:      500.00,
		}, nil
	}
	return limits, nil
}

// SaveIntegrityAlert persists a Portaria 1207 integrity alert.
func (s *Store) SaveIntegrityAlert(ctx context.Context, alert *IntegrityAlert) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO integrity_alerts (id, bet_id, cpf, alert_type, description, severity, reported, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
		alert.ID, alert.BetID, alert.CPF, alert.AlertType,
		alert.Description, alert.Severity, alert.Reported, alert.CreatedAt,
	)
	return err
}

// UpdateSIGAPReportID records the SIGAP report ID against a bet.
func (s *Store) UpdateSIGAPReportID(ctx context.Context, betID, reportID string) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE bets SET sigap_report_id=$1, updated_at=$2 WHERE id=$3`,
		reportID, time.Now().UTC(), betID,
	)
	return err
}
